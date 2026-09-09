"""Load only the pinned action's package, never an import from the caller checkout."""

import os
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    raise SystemExit("Agent review requires Python 3.11+")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from agent_review_coordinator.cli import main  # noqa: E402

mode = os.environ["COORDINATOR_MODE"]
if mode not in {"admission", "recover"}:
    raise SystemExit("Action mode must be admission or recover")
raise SystemExit(main(["--project", os.getcwd(), mode]))
