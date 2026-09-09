# Agent Review Coordinator

An optional local Claude–Codex review loop, with GitHub as the durable record and
CI as the independent validator. Run it for a project when you want agent review;
leave it off for ordinary pushes and CI. It never merges or blocks Git pushes.

## How it works

1. A PR declares its implementing agent, approved spec, and acceptance criteria.
2. The other agent reviews first. They alternate for at most two turns each.
3. The coordinator runs the configured local checks and publishes scoped fixes
   through non-force pushes, recording decisions and evidence on the PR.
4. Both agents must approve the same head, base, and criteria before releasing CI
   as approved. Changes invalidate earlier approvals; counters never reset.
5. Disagreements, timeouts, invalid metadata, or interrupted turns flag the owner
   and release normal CI without claiming approval. Merging stays manual.

A ten-minute renewable lease defers expensive CI while review runs. An hourly
GitHub watchdog resumes explicitly deferred first attempts after lease expiry.
Approval/shutdown dispatch it immediately. It never retries actual test/build
failures. Nominal crash recovery is within 70 minutes; GitHub schedule delays can
extend it. Manual CI reruns bypass the lease. Already-pushed work is on GitHub;
unfinished edits remain in preserved local worktrees.

## Install and run

Requirements: Python 3.11+, Git, GitHub CLI, current Codex CLI, Claude Code CLI,
and their existing authenticated accounts. The pilot supports macOS/Linux and
one coordinator host per repository. GitHub authentication must be as the
repository owner; organization-owned repositories are not yet supported.

```sh
# From this cloned repository:
uv tool install .

# From anywhere, after the target project's integration has merged:
agent-review --project /absolute/path/to/project check-config
agent-review --project /absolute/path/to/project run
```

Ctrl-C or SIGTERM stops active agent processes, releases the lease, and dispatches
normal CI. `agent-review --project /path/to/project status` shows saved state.
Each CLI defaults to a 20-minute deadline (`--agent-timeout 60..3600`). Idle
polling does not call models. CLI calls consume your configured accounts' usage.
No new model subscriptions, API keys, or token purchases are configured here.

The first run creates an owner-authored control issue/comment and the repository
Actions variable `REVIEW_CONTROL_COMMENT`. The process must start before the PR
updates to review. Existing unchanged PRs are exempt at startup; fork PRs always
use normal CI and never execute locally. Separate projects have separate leases,
state, and locks. One process is launched per project.

## Connect a project

Copy `examples/.agent-review.json` and adjust every field. It declares default
and target branches, workflow filenames, the CI job containing the hold step,
project instructions, protected paths, and local validation commands as argv
arrays. Configuration is trusted executable policy: review it before running.
The coordinator loads it once at startup; restart after changing it.

Add the admission steps from `examples/admission.yml` to the existing CI job
before expensive work. Every required result must depend on that job failing
when deferred—never treat skipped, untested work as passing. Add the watchdog
from `examples/review-watchdog.yml`. **Replace `COMMIT_SHA` with a reviewed full
40-character commit SHA in both examples.** Both actions call this repository's
shared runtime; do not copy its Python files into the target project.

The action uses an isolated Python launcher to avoid importing code from a PR
that happens to have the same package name. Watchdog jobs must check out trusted
default-branch configuration, never a PR, because their token can rerun Actions.

Include these lines in agent-authored PR bodies:

```text
Review-Author: codex
Review-Spec: docs/approved-spec.md
Review-Acceptance: Observable behavior and the checks needed to accept it.
```

`Review-Author: codex` starts Claude; `Review-Author: claude` starts Codex. Missing,
duplicate, or unknown authors escalate. Changing authors after any turn is
consumed cannot restart the budget. Specs must already exist at the target base;
new or conflicting requirements need a human decision. Project agent instructions
should require these fields whenever creating a PR.

## Boundaries and recovery

- Local state lives under the target clone's common Git directory in
  `review-coordinator/`. Do not delete it to obtain more review turns. Interrupted
  turns consume their allowance; their worktrees, logs, and publication intent
  survive for manual inspection. No automatic cleanup deletes unfinished work.
- Model verdicts are judgments, not deterministic proof. Structured evidence is
  mandatory, and configured local checks run before publishing. If a check alters
  source after review, the coordinator escalates instead of approving that tree.
- Worktrees isolate edits, not hostile code. The pilot runs trusted, same-repository
  PRs with your normal CLI permissions. Models are instructed not to run Git/GitHub
  writes; the coordinator owns publication. External interactive Git clients are
  not locked, and their pushes cause the in-flight review to stop safely.
- Config, declared specs, and configured protected paths cannot be edited by
  agent-generated fixes. A moved head/base/body prevents stale approval. Normal
  GitHub builds remain responsible for integration and platform verification.
- Once configured, an hourly watchdog allocates up to 24 runner jobs per day even
  while local review is off (roughly 720 billed minutes/month if each rounds to
  one minute), plus immediate dispatches. GitHub account pricing determines cost.
- GitHub outages or malformed lease records fail open to normal CI. No automated
  agent approval is inferred from fallback, a successful push, or passing CI.

## Development

```sh
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
```

The package has no runtime dependencies. Tests cover the review state machine,
configuration, packaging, stale publication, lease recovery, and action isolation.
A full live review/lease exercise is separate from those local tests; see the
first integration PR for deployment evidence. Pin a new reviewed commit to upgrade
projects; publishing an update here does not silently change their behavior.
