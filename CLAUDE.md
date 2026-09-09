# Implementation map

Updated 2026-09-09. Read AGENTS.md first.

- `src/agent_review_coordinator/coordinator.py`: local CLI turns and publication.
- `config.py`: validated project-owned policy, loaded once per process.
- `protocol.py`: owner-authored lease and exact-snapshot admission.
- `watchdog.py`: one-shot recovery of explicitly deferred runs.
- `cli.py`: installed `agent-review` entry point.
- `action.yml` / `action_runner.py`: pinned, isolated GitHub Actions entry point.
- `examples/`: project integration templates; pin a full commit SHA before use.

Use `uv sync --locked`, `uv run pytest -q`, `uv run ruff check .`,
`uv run ruff format --check .`, and `uv build` for validation. The runtime is
stdlib-only and requires Python 3.11+. Keep the owner-account, one-host, trusted
same-repository pilot limits explicit. Organization/multi-host support is not
implemented. Project checks run with argv arrays; they are still executable policy.
