"""Installed entry point and isolated pinned-action package loading."""

import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from agent_review_coordinator.config import CONFIG_NAME, Config


@pytest.mark.parametrize("branch", ["main", "trunk"])
def test_installed_command_checks_explicit_project_from_elsewhere(tmp_path, branch):
    project, elsewhere = tmp_path / "project", tmp_path / "elsewhere"
    project.mkdir()
    elsewhere.mkdir()
    value = {
        "version": 1,
        **asdict(Config(default_branch=branch, target_branches=(branch,))),
    }
    (project / CONFIG_NAME).write_text(json.dumps(value))
    executable = Path(sys.executable).parent / "agent-review"
    assert executable.is_file(), (
        "Run tests with uv run so the package entry point is installed"
    )
    completed = subprocess.run(
        [str(executable), "--project", str(project), "check-config"],
        cwd=elsewhere,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "Project configuration valid"


def test_pinned_action_ignores_caller_package_shadow(tmp_path):
    (tmp_path / CONFIG_NAME).write_text(json.dumps({"version": 1, **asdict(Config())}))
    marker = tmp_path / "injected.txt"
    malicious = "from pathlib import Path\nPath('injected.txt').write_text('executed')\nraise RuntimeError('caller import')\n"
    (tmp_path / "agent_review_coordinator.py").write_text(malicious)
    shadow = tmp_path / "agent_review_coordinator"
    shadow.mkdir()
    (shadow / "__init__.py").write_text(malicious)
    (tmp_path / "sitecustomize.py").write_text(malicious)
    action = Path(__file__).resolve().parents[1] / "action_runner.py"
    env = {
        **os.environ,
        "COORDINATOR_MODE": "admission",
        "GITHUB_EVENT_NAME": "push",
        "PYTHONPATH": str(tmp_path),
    }
    completed = subprocess.run(
        [sys.executable, "-I", str(action)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert not marker.exists()
