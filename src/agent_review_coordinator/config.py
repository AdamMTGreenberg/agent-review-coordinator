"""Project-owned policy; the installed runtime never depends on a project layout."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = ".agent-review.json"


@dataclass(frozen=True)
class Config:
    default_branch: str = "main"
    target_branches: tuple[str, ...] = ("main",)
    ci_workflow: str = "ci.yml"
    watchdog_workflow: str = "review-watchdog.yml"
    classifier_job: str = "Classify changed files"
    instructions: tuple[str, ...] = ("AGENTS.md",)
    protected_paths: tuple[str, ...] = (".github/",)
    validation_commands: tuple[tuple[str, ...], ...] = ()


def load_config(root: Path) -> Config:
    value = json.loads((root / CONFIG_NAME).read_text())
    if not isinstance(value, dict) or value.pop("version", None) != 1:
        raise ValueError("Expected version 1 project configuration")
    if set(value) != set(Config.__dataclass_fields__):
        raise ValueError(
            "Project configuration must declare exactly the documented fields"
        )
    for field in ("default_branch", "classifier_job"):
        if not isinstance(value[field], str) or not value[field].strip():
            raise ValueError(f"{field} must be a nonempty string")
    for field in ("ci_workflow", "watchdog_workflow"):
        if not isinstance(value[field], str) or not re.fullmatch(
            r"[\w.-]+\.ya?ml", value[field]
        ):
            raise ValueError(f"{field} must be a workflow filename")
    if (
        not isinstance(value["target_branches"], list)
        or not value["target_branches"]
        or any(
            not isinstance(branch, str) or not branch.strip()
            for branch in value["target_branches"]
        )
    ):
        raise ValueError("target_branches must be a nonempty array of branch names")
    value["target_branches"] = tuple(value["target_branches"])
    for field in ("instructions", "protected_paths"):
        if not isinstance(value[field], list):
            raise ValueError(f"{field} must be an array")
        for path in value[field]:
            if (
                not isinstance(path, str)
                or not path
                or Path(path).is_absolute()
                or ".." in Path(path).parts
            ):
                raise ValueError(f"{field} contains an invalid relative path")
        value[field] = tuple(value[field])
    commands = value["validation_commands"]
    if not isinstance(commands, list) or any(
        not isinstance(cmd, list)
        or not cmd
        or any(not isinstance(arg, str) or not arg for arg in cmd)
        for cmd in commands
    ):
        raise ValueError("validation_commands must be arrays of nonempty argv strings")
    value["validation_commands"] = tuple(tuple(cmd) for cmd in commands)
    return Config(**value)
