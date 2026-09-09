"""Release only explicitly deferred PR runs; never retry a build/test failure.

Runs on trusted main via review-watchdog.yml. Re-running preserves the original
PR event/ref and therefore the existing required check contexts. One recovery
attempt per run; superseded heads and closed PRs are ignored.
"""

from __future__ import annotations

import subprocess

from .config import Config
from .protocol import (
    HOLD_STEP,
    api,
    criteria_key,
    deferred,
    pages,
    read_control,
)


def held_run(run: dict, jobs: list[dict], config: Config | None = None) -> bool:
    config = config or Config()
    return (
        run.get("event") == "pull_request"
        and run.get("conclusion") == "failure"
        and run.get("run_attempt") == 1
        and any(
            job.get("name") == config.classifier_job
            and any(
                step.get("name") == HOLD_STEP and step.get("conclusion") == "failure"
                for step in job.get("steps", [])
            )
            for job in jobs
        )
    )


def recover(repo: str, comment_id: str, config: Config | None = None) -> int:
    config = config or Config()
    # The lease fails open; rerun admission also bypasses it explicitly.
    try:
        state = read_control(repo, comment_id)
    except (ValueError, KeyError, RuntimeError, OSError, subprocess.TimeoutExpired):
        state = {}  # A broken control record cannot strand already-deferred CI.
    prs = {
        pr["head"]["sha"]: pr
        for pr in pages(repo, "pulls?state=open")
        if pr["base"].get("ref", config.default_branch) in config.target_branches
    }
    recovered = 0
    seen = set()
    # Github permits reruns for 30 days; API created filter bounds the history.
    from datetime import datetime, timedelta, timezone

    since = (datetime.now(timezone.utc) - timedelta(days=29)).date().isoformat()
    for run in pages(
        repo,
        f"actions/workflows/{config.ci_workflow}/runs?event=pull_request&created=>={since}",
        "workflow_runs",
    ):
        head = run["head_sha"]
        pr = prs.get(head)
        if not pr or head in seen:
            continue
        seen.add(head)  # Only the newest run for a head can be released.
        if deferred(
            state,
            pr["number"],
            head,
            pr["base"]["sha"],
            criteria=criteria_key(pr.get("body")),
        ):
            continue
        if run.get("conclusion") != "failure" or run.get("run_attempt") != 1:
            continue
        jobs = list(pages(repo, f"actions/runs/{run['id']}/jobs", "jobs"))
        if not held_run(run, jobs, config):
            continue
        # Re-fetch immediately before mutation. Closed/new-head PRs stay untouched.
        current = api(repo, f"pulls/{pr['number']}")
        if current["state"] != "open" or current["head"]["sha"] != head:
            continue
        if deferred(
            state,
            current["number"],
            head,
            current["base"]["sha"],
            criteria=criteria_key(current.get("body")),
        ):
            continue
        api(repo, f"actions/runs/{run['id']}/rerun", method="POST")
        print(f"Released deferred CI run {run['id']} for PR #{pr['number']}")
        recovered += 1
    return recovered
