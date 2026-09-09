"""Installed command entry point, also used by the pinned composite action."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from . import __version__
from .config import load_config
from .coordinator import serve
from .protocol import VARIABLE, admission
from .watchdog import recover


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument(
        "action", choices=["run", "status", "admission", "recover", "check-config"]
    )
    parser.add_argument("--agent-timeout", type=int, default=1200)
    args = parser.parse_args(argv)
    root = args.project.resolve()
    config = load_config(root)
    if args.action == "admission":
        return admission(config)
    if args.action == "check-config":
        print("Project configuration valid")
        return 0
    if args.action == "recover":
        recover(os.environ["GITHUB_REPOSITORY"], os.environ.get(VARIABLE, ""), config)
        return 0
    if not 60 <= args.agent_timeout <= 3600:
        parser.error("--agent-timeout must be 60..3600 seconds")
    serve(root, config, args.action, args.agent_timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
