"""Review safety boundaries without invoking models, Git, or GitHub."""

from __future__ import annotations

import copy
import json

import pytest

from agent_review_coordinator import coordinator as review

HEAD = "a" * 40
BASE = "b" * 40
EDITED = "c" * 40


def initial_state():
    return {
        "head": HEAD,
        "base": BASE,
        "counts": {"codex": 0, "claude": 0},
        "next": "codex",
        "approvals": {},
        "history": [],
        "status": "reviewing",
    }


def approve():
    return {
        "decision": "approve",
        "summary": "Acceptance criteria verified.",
        "evidence": ["docs/SPEC.md requirement one: targeted checks passed"],
    }


@pytest.fixture
def pr():
    return {
        "number": 42,
        "state": "open",
        "head": {
            "sha": HEAD,
            "ref": "feature/example",
            "repo": {"full_name": "owner/repo"},
        },
        "base": {"sha": BASE},
        "body": "Review-Author: claude\nReview-Spec: docs/SPEC.md\nReview-Acceptance: requirement one",
    }


@pytest.fixture
def service(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Unexpected external operation")

    monkeypatch.setattr(review, "api", unexpected)
    monkeypatch.setattr(review, "command", unexpected)
    coordinator = review.Coordinator(tmp_path, "owner/repo", tmp_path, 60)
    monkeypatch.setattr(coordinator, "run_agent", unexpected)
    return coordinator


def mock_git(monkeypatch):
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return HEAD
        if args[:2] == ["git", "show"]:
            return "Requirement one"
        return ""

    monkeypatch.setattr(review, "command", command)
    return calls


def test_both_agents_must_approve_same_resulting_head():
    state = initial_state()
    state["counts"]["codex"] = 1
    assert review.record_turn(state, "codex", HEAD, approve()) == "reviewing"
    state["counts"]["claude"] = 1
    assert review.record_turn(state, "claude", HEAD, approve()) == "approved"


def test_changed_head_clears_previous_approval():
    state = initial_state()
    state["counts"] = {"codex": 1, "claude": 1}
    state["approvals"] = {"codex": HEAD}
    assert review.record_turn(state, "claude", EDITED, approve()) == "reviewing"
    assert state["approvals"] == {"claude": EDITED}
    assert state["next"] == "codex"


def test_final_claude_edit_escalates_instead_of_self_approving():
    state = initial_state()
    state["counts"] = {"codex": 2, "claude": 2}
    state["approvals"] = {"codex": HEAD}
    assert review.record_turn(state, "claude", EDITED, approve()) == "human"
    assert state["approvals"] == {"claude": EDITED}


def test_turn_is_durably_consumed_before_agent_invocation(service, pr, monkeypatch):
    state = initial_state()
    service.state["prs"]["42"] = state
    mock_git(monkeypatch)

    def crash(*args):
        persisted = json.loads(service.path.read_text())["prs"]["42"]
        assert persisted["counts"] == {"codex": 1, "claude": 0}
        assert persisted["status"] == "running"
        assert persisted["worktree"]
        raise RuntimeError("Simulated agent crash")

    monkeypatch.setattr(service, "run_agent", crash)
    with pytest.raises(RuntimeError, match="Simulated agent crash"):
        service.turn(pr, state)
    assert state["approvals"] == {}


@pytest.mark.parametrize("movement", ["head", "base", "body", "closed"])
def test_remote_change_prevents_publication(service, pr, monkeypatch, movement):
    state = initial_state()
    service.state["prs"]["42"] = state
    calls = mock_git(monkeypatch)
    monkeypatch.setattr(service, "run_agent", lambda *args: approve())
    current = copy.deepcopy(pr)
    if movement in {"head", "base"}:
        current[movement]["sha"] = EDITED
    elif movement == "body":
        current["body"] += "\nReview-Acceptance: additional requirement"
    else:
        current["state"] = "closed"
    monkeypatch.setattr(review, "api", lambda *args: current)

    with pytest.raises(RuntimeError, match="PR changed during review"):
        service.turn(pr, state)

    assert not any(args[1] in {"add", "commit", "push"} for args in calls)
    assert state["approvals"] == {}
    assert state["history"] == []


def test_interrupted_poll_escalates_without_replaying_agent(service, pr, monkeypatch):
    state = initial_state()
    state["status"] = "running"
    state["counts"]["codex"] = 1
    service.state["prs"]["42"] = state
    monkeypatch.setattr(review, "pages", lambda *args: [pr])
    monkeypatch.setattr(service, "heartbeat", lambda: None)
    finished = []
    monkeypatch.setattr(service, "finish", lambda *args: finished.append(args))

    service.poll()

    assert len(finished) == 1
    assert finished[0][2] == "interrupted — inspect preserved worktree"
    assert state["counts"] == {"codex": 1, "claude": 0}


@pytest.mark.parametrize("stopping", [False, True])
def test_foreign_lease_cannot_be_renewed_or_cleared(service, monkeypatch, stopping):
    monkeypatch.setattr(service, "control", lambda: {"session": "another-process"})
    monkeypatch.setattr(review.time, "monotonic", lambda: 1000)
    with pytest.raises(RuntimeError, match="Lease ownership changed"):
        service.heartbeat(stopping=stopping)


@pytest.mark.parametrize("movement", ["body", "closed"])
def test_late_remote_change_cannot_receive_approval(service, pr, monkeypatch, movement):
    state = initial_state()
    service.state["prs"]["42"] = state
    mock_git(monkeypatch)
    monkeypatch.setattr(service, "run_agent", lambda *args: approve())
    moved = copy.deepcopy(pr)
    if movement == "body":
        moved["body"] += "\nReview-Acceptance: changed during publication"
    else:
        moved["state"] = "closed"
    snapshots = iter([pr, moved])
    monkeypatch.setattr(review, "api", lambda *args: next(snapshots))
    with pytest.raises(RuntimeError, match="Remote changed before verdict"):
        service.turn(pr, state)
    assert state["approvals"] == {}
    assert state["history"] == []


def test_large_prompt_cannot_block_before_timeout(service, tmp_path, monkeypatch):
    class Stalled:
        def poll(self):
            return None

    def spawn(args, **kwargs):
        # A regular input file doesn't wait for the child to drain a pipe.
        assert kwargs["stdin"].read() == "x" * 200000
        return Stalled()

    monkeypatch.setattr(review.subprocess, "Popen", spawn)
    clock = iter([0, 61])
    monkeypatch.setattr(review.time, "monotonic", lambda: next(clock))
    stopped = []
    monkeypatch.setattr(service, "kill_child", lambda: stopped.append(True))
    with pytest.raises(TimeoutError):
        review.Coordinator.run_agent(service, "codex", tmp_path, "x" * 200000, tmp_path)
    assert stopped == [True]


@pytest.mark.parametrize("author,first", [("codex", "claude"), ("claude", "codex")])
def test_author_routes_full_two_round_sequence(author, first):
    state = initial_state()
    body = f"Review-Author: {author}"
    other = author
    changes = {**approve(), "decision": "changes"}
    for index, agent in enumerate([first, other, first, other]):
        assert review.route_author(state, body) == agent
        state["counts"][agent] += 1
        status = review.record_turn(state, agent, HEAD, changes)
        assert status == ("human" if index == 3 else "reviewing")
    assert state["counts"] == {"codex": 2, "claude": 2}


@pytest.mark.parametrize(
    "body",
    [
        "",
        "Review-Author: human",
        "Review-Author: ",
        "Review-Author: codex\nReview-Author: claude",
        "Review-Author: codex\nReview-Author: codex",
    ],
)
def test_invalid_author_never_guesses_or_consumes_a_turn(body):
    state = initial_state()
    with pytest.raises(ValueError, match="exactly one Review-Author"):
        review.route_author(state, body)
    assert state["counts"] == {"codex": 0, "claude": 0}


def test_author_cannot_change_or_migrate_unknown_history_after_start():
    state = initial_state()
    assert review.route_author(state, "Review-Author: codex") == "claude"
    state["counts"]["claude"] = 1
    before = copy.deepcopy(state)
    with pytest.raises(ValueError, match="after review began"):
        review.route_author(state, "Review-Author: claude")
    assert state == before
    del state["author"]
    with pytest.raises(ValueError, match="after review began"):
        review.route_author(state, "Review-Author: codex")


@pytest.mark.parametrize("author,expected", [("codex", "claude"), ("claude", "codex")])
def test_real_turn_invokes_opposite_cli(service, pr, monkeypatch, author, expected):
    state = initial_state()
    pr["body"] = pr["body"].replace("Review-Author: claude", f"Review-Author: {author}")
    service.state["prs"]["42"] = state
    mock_git(monkeypatch)

    def agent(name, *args):
        assert name == expected
        persisted = json.loads(service.path.read_text())["prs"]["42"]
        assert persisted["author"] == author
        assert persisted["counts"][expected] == 1
        raise RuntimeError("Routing verified")

    monkeypatch.setattr(service, "run_agent", agent)
    with pytest.raises(RuntimeError, match="Routing verified"):
        service.turn(pr, state)


def test_author_metadata_accepts_github_crlf_and_rejects_duplicates():
    state = initial_state()
    assert (
        review.route_author(state, "Review-Author: codex\r\nReview-Spec: docs/SPEC.md")
        == "claude"
    )
    with pytest.raises(ValueError, match="exactly one"):
        review.route_author(state, "Review-Author: codex\r\nReview-Author: claude\r\n")


def test_commit_hook_cannot_publish_an_unreviewed_tree(service, pr, monkeypatch):
    state = initial_state()
    service.state["prs"]["42"] = state
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["rev-parse", "HEAD"]:
            return HEAD
        if args[1] == "status":
            return " M file.txt"
        if args[1:3] == ["diff", "--cached"] and "--name-only" in args:
            return "file.txt"
        if args[1] == "write-tree":
            return "reviewed-tree"
        if args[1:3] == ["rev-parse", "HEAD^{tree}"]:
            return "hook-modified-tree"
        return ""

    monkeypatch.setattr(review, "command", command)
    monkeypatch.setattr(review, "api", lambda *args: pr)
    monkeypatch.setattr(service, "run_agent", lambda *args: approve())
    with pytest.raises(RuntimeError, match="Commit hooks changed reviewed source"):
        service.turn(pr, state)
    assert not any(args[1] == "push" for args in calls)
    assert state["approvals"] == {}
