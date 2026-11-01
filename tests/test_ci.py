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
    assert {"uses": "actions/setup-node@v7", "with": {"node-version": "24"}} in card


def test_workflows_can_be_run_by_hand():
    for path in WORKFLOWS.glob("*.yml"):
        assert "workflow_dispatch" in _triggers(_workflow(path.name)), path.name


TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"
FORMS = ("bug_report.yml", "new_light.yml", "feature_request.yml")
ITEM_TYPES = {"markdown", "input", "textarea", "dropdown", "checkboxes"}


def _form(name: str) -> dict:
    return yaml.safe_load((TEMPLATES / name).read_text(encoding="utf-8"))


def _ids(form: dict) -> list[str]:
    return [item["id"] for item in form["body"] if "id" in item]


def test_issue_forms_are_valid():
    for name in FORMS:
        form = _form(name)
        assert form["name"] and form["description"] and form["body"], name
        ids = _ids(form)
        assert len(ids) == len(set(ids)), name
        assert {item["type"] for item in form["body"]} <= ITEM_TYPES, name


def test_bug_report_asks_for_versions_and_diagnostics():
    ids = _ids(_form("bug_report.yml"))
    for wanted in ("integration_version", "ha_version", "package_version", "diagnostics"):
        assert wanted in ids, wanted


def test_new_light_asks_what_a_profile_needs():
    form = _form("new_light.yml")
    ids = _ids(form)
    for wanted in ("model", "what_works", "captures", "beacons"):
        assert wanted in ids, wanted
    text = (TEMPLATES / "new_light.yml").read_text(encoding="utf-8")
    assert "docs/PROFILES.md" in text
    assert "neighbours' devices" in text


def test_private_data_warning():
    for name in FORMS:
        first = _form(name)["body"][0]
        assert first["type"] == "markdown", name
        assert "Wi-Fi password" in first["attributes"]["value"], name
        assert "API key" in first["attributes"]["value"], name


def test_blank_issues_disabled():
    config = yaml.safe_load((TEMPLATES / "config.yml").read_text(encoding="utf-8"))
    assert config["blank_issues_enabled"] is False


def test_pull_request_template_asks_for_checks():
    text = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    assert "personal data" in text
    assert "node --test tests/card/*.mjs" in text


def test_test_requirements_cover_the_tools():
    """tests/test_tools.py runs the research tools, which import aioesphomeapi (CI, 2026-11-01)."""
    requirements = (ROOT / "requirements-test.txt").read_text(encoding="utf-8").splitlines()
    assert "-r tools/requirements.txt" in requirements


def test_actions_run_on_node_24():
    """checkout@v4, setup-python@v5 and setup-node@v4 target the deprecated Node 20."""
    wanted = {"actions/checkout": "v7", "actions/setup-python": "v7", "actions/setup-node": "v7"}
    for path in WORKFLOWS.glob("*.yml"):
        for job in _workflow(path.name)["jobs"].values():
            for step in job["steps"]:
                name, _, version = step.get("uses", "").partition("@")
                if name in wanted:
                    assert version == wanted[name], f"{path.name}: {step['uses']}"
