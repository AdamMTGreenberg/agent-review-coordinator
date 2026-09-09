"""CI is never green merely because review deferred its builds."""

import json

import pytest

from agent_review_coordinator import protocol, watchdog


def lease(**overrides):
    return {
        "version": 1,
        "expires": 1500,
        "session": "session",
        "released": {},
        **overrides,
    }


def test_only_fresh_exact_lease_can_defer():
    assert protocol.deferred(lease(), 1, "head", "base", now=1000)
    for malformed in (
        {},
        lease(expires=1000),
        lease(expires=1601),
        lease(expires="tomorrow"),
        lease(released=[]),
    ):
        assert not protocol.deferred(malformed, 1, "head", "base", now=1000)
    released = lease(released={"1": {"head": "head", "base": "base"}})
    assert not protocol.deferred(released, 1, "head", "base", now=1000)
    assert protocol.deferred(released, 1, "new-head", "base", now=1000)
    assert protocol.deferred(released, 1, "head", "new-base", now=1000)


def test_control_record_is_owner_authored_and_versioned():
    comment = {"user": {"login": "Owner"}, "body": protocol.encode(lease())}
    assert protocol.decode(comment, "owner") == lease()
    with pytest.raises(ValueError):
        protocol.decode(comment, "imposter")
    comment["body"] = protocol.encode(lease(version=2))
    with pytest.raises(ValueError):
        protocol.decode(comment, "owner")


@pytest.mark.parametrize("mode", ["active", "off", "outage", "fork", "rerun", "main"])
def test_ci_admission_does_not_fake_results(tmp_path, monkeypatch, mode):
    payload = {
        "pull_request": {
            "number": 1,
            "head": {"sha": "head", "repo": {"full_name": "owner/repo"}},
            "base": {"sha": "base"},
        }
    }
    if mode == "fork":
        payload["pull_request"]["head"]["repo"]["full_name"] = "fork/repo"
    event = tmp_path / "event.json"
    event.write_text(json.dumps(payload))
    output = tmp_path / "output"
    monkeypatch.setenv(
        "GITHUB_EVENT_NAME", "push" if mode == "main" else "pull_request"
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2" if mode == "rerun" else "1")
    monkeypatch.setattr(protocol.time, "time", lambda: 1000)

    def read(*_):
        if mode == "outage":
            raise RuntimeError("API unavailable")
        return {} if mode == "off" else lease()

    monkeypatch.setattr(protocol, "read_control", read)
    assert protocol.admission() == 0
    if mode == "active":
        assert output.read_text() == "deferred=true\n"
    else:
        assert not output.exists()


def test_recovery_never_retries_real_failures_or_second_attempts():
    run = {"event": "pull_request", "conclusion": "failure", "run_attempt": 1}
    held = [
        {
            "name": "Classify changed files",
            "steps": [{"name": protocol.HOLD_STEP, "conclusion": "failure"}],
        }
    ]
    assert watchdog.held_run(run, held)
    assert not watchdog.held_run({**run, "run_attempt": 2}, held)
    assert not watchdog.held_run({**run, "event": "push"}, held)
    assert not watchdog.held_run(
        run,
        [
            {
                "name": "KMP Unit Tests",
                "steps": [{"name": "Run tests", "conclusion": "failure"}],
            }
        ],
    )


@pytest.mark.parametrize("broken", [False, True])
def test_watchdog_releases_once_and_ignores_stale_heads(monkeypatch, broken):
    pr = {
        "number": 1,
        "state": "open",
        "head": {"sha": "current"},
        "base": {"sha": "base"},
    }
    run = {
        "id": 42,
        "head_sha": "current",
        "event": "pull_request",
        "conclusion": "failure",
        "run_attempt": 1,
    }
    jobs = [
        {
            "name": "Classify changed files",
            "steps": [{"name": protocol.HOLD_STEP, "conclusion": "failure"}],
        }
    ]

    def read(*_):
        if broken:
            raise ValueError("deleted or malformed control")
        return {}

    monkeypatch.setattr(watchdog, "read_control", read)

    def pages(_repo, path, _key=None):
        if path.startswith("pulls"):
            return [pr]
        if path.endswith("/jobs"):
            return jobs
        return [run, {**run, "id": 41, "head_sha": "stale"}]

    monkeypatch.setattr(watchdog, "pages", pages)
    calls = []

    def api(_repo, path, data=None, method=None):
        if path.startswith("pulls/"):
            return pr
        calls.append(path)
        run["run_attempt"] = 2

    monkeypatch.setattr(watchdog, "api", api)
    assert watchdog.recover("owner/repo", "1") == 1
    assert watchdog.recover("owner/repo", "1") == 0
    assert calls == ["actions/runs/42/rerun"]


def test_changed_criteria_invalidate_release():
    released = lease(released={"1": {"head": "h", "base": "b", "criteria": "old"}})
    assert not protocol.deferred(released, 1, "h", "b", now=1000, criteria="old")
    assert protocol.deferred(released, 1, "h", "b", now=1000, criteria="new")
