"""Shared lease protocol for local review and GitHub CI admission.

A lease only delays computation. It never grants merge approval or inhibits a
push. Absent, expired, malformed, or unavailable control state means normal CI.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from .config import Config

VARIABLE = "REVIEW_CONTROL_COMMENT"
HOLD_STEP = "Hold CI for local review"
TTL = 600
PREFIX = "Local review coordinator (not a merge approval).\n\n```json\n"


def command(args: list[str], *, cwd=None, stdin=None, timeout=60) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        # Do not copy subprocess stderr: authentication helpers can include secrets.
        raise RuntimeError(f"{args[0]} {args[1]} failed (exit {result.returncode})")
    return result.stdout.strip()


def api(repo: str, path: str, data=None, method=None):
    args = ["gh", "api", f"repos/{repo}/{path}"]
    if method:
        args += ["--method", method]
    if data is not None:
        args += ["--input", "-"]
    raw = command(args, stdin=json.dumps(data) if data is not None else None)
    return json.loads(raw) if raw else None


def pages(repo: str, path: str, key=None):
    """Read every API page, rather than silently ignoring busy repositories."""
    separator = "&" if "?" in path else "?"
    page = 1
    while True:
        result = api(repo, f"{path}{separator}per_page=100&page={page}")
        rows = result[key] if key else result
        yield from rows
        if len(rows) < 100:
            return
        page += 1


def encode(state: dict) -> str:
    return PREFIX + json.dumps(state, sort_keys=True) + "\n```"


def decode(comment: dict, owner: str) -> dict:
    if comment.get("user", {}).get("login", "").lower() != owner.lower():
        raise ValueError("Control comment must belong to the repository owner")
    body = comment["body"]
    if not body.startswith(PREFIX) or not body.endswith("\n```"):
        raise ValueError("Invalid control envelope")
    value = json.loads(body[len(PREFIX) : -4])
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Unknown protocol")
    return value


def read_control(repo: str, comment_id: str) -> dict:
    if not comment_id.isdigit():
        return {}
    return decode(api(repo, f"issues/comments/{comment_id}"), repo.split("/")[0])


def criteria_key(body: str | None) -> str:
    return hashlib.sha256((body or "").encode()).hexdigest()


def deferred(
    state: dict, number: int, head: str, base: str, now=None, criteria=""
) -> bool:
    """Fail open for the lease, never fabricate a successful CI result."""
    now = time.time() if now is None else now
    try:
        expires = state["expires"]
        if type(expires) not in (int, float) or not now < expires <= now + TTL:
            return False
        releases = state["released"]
        if not isinstance(releases, dict) or not isinstance(state["session"], str):
            return False
        release = releases.get(str(number), {})
        return not (
            release.get("head") == head
            and release.get("base") == base
            and release.get("criteria", "") == criteria
        )
    except (KeyError, TypeError, AttributeError):
        return False


def admission(config: Config | None = None) -> int:
    config = config or Config()
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        return 0
    if int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")) > 1:
        print("Recovery/manual rerun: normal CI, without implying agent approval.")
        return 0
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        pr = event["pull_request"]
        if pr["base"].get("ref", config.default_branch) not in config.target_branches:
            return 0
        if (pr["head"].get("repo") or {}).get("full_name") != os.environ[
            "GITHUB_REPOSITORY"
        ]:
            return 0
        state = read_control(
            os.environ["GITHUB_REPOSITORY"], os.environ.get(VARIABLE, "")
        )
        hold = deferred(
            state,
            pr["number"],
            pr["head"]["sha"],
            pr["base"]["sha"],
            criteria=criteria_key(pr.get("body")),
        )
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        print("Local review state unavailable: running normal CI (no agent approval).")
        return 0
    if hold:
        print(
            "::notice::Local review is active. Expensive CI is deferred; no tests passed."
        )
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            print("deferred=true", file=output)
        return 0
    print("Normal CI admitted; this is not an agent or merge approval.")
    return 0


if __name__ == "__main__":
    raise SystemExit(admission())
