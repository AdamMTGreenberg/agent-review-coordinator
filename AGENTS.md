# Agent Review Coordinator

Read README.md and CLAUDE.md before changing behavior. Keep the runtime portable:
project-specific names, instructions, and validation belong in project config.
Preserve manual merges, unrestricted pushes, two turns per agent, exact-snapshot
approvals, and normal-CI crash fallback. Tests must exercise failure paths, not
only happy-path mocks. Review every change; use isolated task branches/worktrees.
Never publish credentials or raw agent logs. No automatic merge or force-push.
PR bodies identify the implementing agent with `Review-Author: codex` or `claude`.
