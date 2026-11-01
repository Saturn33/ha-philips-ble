"""GitHub workflows and templates: every check runs on a fresh clone."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    return workflow[True]  # PyYAML reads the key `on` as True


def _runs(workflow: dict) -> list[str]:
    return [step["run"] for job in workflow["jobs"].values() for step in job["steps"] if "run" in step]


def test_hacs_action_runs_every_check():
    (step,) = _workflow("validate.yml")["jobs"]["hacs"]["steps"]
    assert step["uses"] == "hacs/action@main"
    assert step["with"] == {"category": "integration"}


def test_hassfest_runs():
    steps = _workflow("validate.yml")["jobs"]["hassfest"]["steps"]
    assert {"uses": "home-assistant/actions/hassfest@master"} in steps


def test_tests_workflow_covers_every_suite():
    workflow = _workflow("tests.yml")
    runs = _runs(workflow)
    commands = (
        "ruff check .",
        "pytest -q",
        'pytest tests/ha -q -o addopts=""',
        "node --test tests/card/*.mjs",
    )
    for command in commands:
        assert command in runs, command
    card = workflow["jobs"]["card"]["steps"]
    assert {"uses": "actions/setup-node@v4", "with": {"node-version": "24"}} in card


def test_workflows_can_be_run_by_hand():
    for path in WORKFLOWS.glob("*.yml"):
        assert "workflow_dispatch" in _triggers(_workflow(path.name)), path.name
