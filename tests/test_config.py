"""Project policy isolation, validation, and configured recovery behavior."""

import copy
import json
from dataclasses import asdict

import pytest

from agent_review_coordinator import coordinator, watchdog
from agent_review_coordinator.config import CONFIG_NAME, Config, load_config
from agent_review_coordinator.protocol import HOLD_STEP


def write_config(root, **overrides):
    root.mkdir(exist_ok=True)
    value = {"version": 1, **asdict(Config()), **overrides}
    (root / CONFIG_NAME).write_text(json.dumps(value))
    return value


def test_different_projects_keep_independent_policy(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    write_config(first)
    write_config(
        second,
        default_branch="trunk",
        target_branches=["trunk", "release/next"],
        ci_workflow="build.yaml",
        watchdog_workflow="recover.yaml",
        classifier_job="Select work",
        instructions=["CONTRIBUTING.md"],
        protected_paths=["policy/"],
        validation_commands=[["make", "check"]],
    )
    a, b = load_config(first), load_config(second)
    assert a.default_branch == "main"
    assert b.default_branch == "trunk"
    assert b.target_branches == ("trunk", "release/next")
    assert b.ci_workflow == "build.yaml"
    assert b.watchdog_workflow == "recover.yaml"
    assert b.instructions == ("CONTRIBUTING.md",)
    assert b.protected_paths == ("policy/",)
    assert b.validation_commands == (("make", "check"),)
    assert load_config(first) == a


@pytest.mark.parametrize(
    "change",
    [
        {"version": 2},
        {"unknown": True},
        {"default_branch": ""},
        {"target_branches": []},
        {"target_branches": "trunk"},
        {"ci_workflow": "../other.yml"},
        {"classifier_job": 12},
        {"instructions": ["../outside"]},
        {"protected_paths": ["/absolute"]},
        {"validation_commands": ["make check"]},
        {"validation_commands": [[]]},
        {"validation_commands": [["make", None]]},
    ],
)
def test_malformed_policy_rejected(tmp_path, change):
    write_config(tmp_path, **change)
    with pytest.raises(ValueError):
        load_config(tmp_path)


@pytest.mark.parametrize("raw", ["not json", "[]", "{}"])
def test_non_configuration_document_rejected(tmp_path, raw):
    (tmp_path / CONFIG_NAME).write_text(raw)
    with pytest.raises(ValueError):
        load_config(tmp_path)


def test_recovery_uses_configured_workflow_classifier_and_target(monkeypatch):
    config = Config(
        default_branch="trunk",
        target_branches=("release/next",),
        ci_workflow="build.yaml",
        classifier_job="Select work",
    )
    pr = {
        "number": 7,
        "state": "open",
        "head": {"sha": "review-head"},
        "base": {"sha": "base", "ref": "release/next"},
    }
    excluded = copy.deepcopy(pr)
    excluded.update(
        number=8, head={"sha": "other-head"}, base={"sha": "base", "ref": "main"}
    )
    run = {
        "id": 77,
        "event": "pull_request",
        "head_sha": "review-head",
        "conclusion": "failure",
        "run_attempt": 1,
    }
    paths, mutations = [], []
    monkeypatch.setattr(watchdog, "read_control", lambda *args: {})

    def pages(repo, path, key=None):
        paths.append(path)
        if path == "pulls?state=open":
            return [pr, excluded]
        if path.startswith("actions/workflows/build.yaml/runs?"):
            return [run, {**run, "id": 88, "head_sha": "other-head"}]
        if path == "actions/runs/77/jobs":
            return [
                {
                    "name": "Select work",
                    "steps": [{"name": HOLD_STEP, "conclusion": "failure"}],
                }
            ]
        pytest.fail(f"Unexpected endpoint: {path}")

    def api(repo, path, **kwargs):
        if path == "pulls/7":
            return pr
        mutations.append((path, kwargs))

    monkeypatch.setattr(watchdog, "pages", pages)
    monkeypatch.setattr(watchdog, "api", api)
    assert watchdog.recover("owner/project", "123", config) == 1
    assert mutations == [("actions/runs/77/rerun", {"method": "POST"})]
    assert "actions/runs/88/jobs" not in paths


def test_dispatch_uses_project_default_and_watchdog(tmp_path, monkeypatch):
    service = coordinator.Coordinator(
        tmp_path,
        "owner/project",
        tmp_path,
        60,
        Config(default_branch="trunk", watchdog_workflow="recover.yaml"),
    )
    calls = []
    monkeypatch.setattr(coordinator, "api", lambda *args: calls.append(args))
    service.dispatch()
    assert calls == [
        ("owner/project", "actions/workflows/recover.yaml/dispatches", {"ref": "trunk"})
    ]


def test_validation_mutation_cannot_publish_agent_approval(tmp_path, monkeypatch):
    service = coordinator.Coordinator(
        tmp_path,
        "owner/project",
        tmp_path,
        60,
        Config(validation_commands=(("formatter", "--fix"),)),
    )
    head, base = "a" * 40, "b" * 40
    pr = {
        "number": 7,
        "head": {"sha": head},
        "base": {"sha": base},
        "body": "Review-Author: claude\nReview-Spec: SPEC.md\nReview-Acceptance: requirement",
    }
    state = {
        "head": head,
        "base": base,
        "counts": {"codex": 0, "claude": 0},
        "approvals": {},
        "history": [],
    }
    service.state["prs"]["7"] = state
    calls = []

    def command(args, cwd=None, **kwargs):
        calls.append(args)
        if args[:3] == ["git", "worktree", "add"]:
            from pathlib import Path

            tree = Path(args[-2])
            tree.mkdir()
            (tree / "source.txt").write_text("reviewed version")
        if args[:2] == ["git", "ls-files"]:
            return "source.txt\0"
        if args == ["formatter", "--fix"]:
            (cwd / "source.txt").write_text("unreviewed formatted version")
        return ""

    def unexpected(*args, **kwargs):
        pytest.fail("Approval publication must not occur")

    monkeypatch.setattr(coordinator, "command", command)
    monkeypatch.setattr(coordinator, "api", unexpected)
    monkeypatch.setattr(service, "heartbeat", lambda: None)
    monkeypatch.setattr(
        service,
        "run_agent",
        lambda *args: {
            "decision": "approve",
            "summary": "Approved",
            "evidence": ["Requirement checked"],
        },
    )
    with pytest.raises(RuntimeError, match="Validation changed source"):
        service.turn(pr, state)
    assert ["formatter", "--fix"] in calls
    assert not any(args[:2] in [["git", "commit"], ["git", "push"]] for args in calls)
    assert state["approvals"] == {}
