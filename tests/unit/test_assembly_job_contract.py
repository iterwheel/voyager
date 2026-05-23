"""Unit tests for the Assembly Job Contract (VOY-1817 Surface 16)."""

from __future__ import annotations

from voyager.bots.assembly.constants import FORBIDDEN_OPERATIONS, VERIFICATION_COMMANDS
from voyager.bots.assembly.job_contract import build_job_contract

_ISSUE_BODY = """## Problem / Goal

Implement Assembly bot to write code from blueprint-ready issues.

## Acceptance Criteria

- [ ] Assembly responds to /assembly on blueprint-ready issues
- [ ] Refuses on non-allow-listed repos
- [ ] Posts @codex review after every push
"""


def _build() -> dict:
    contract = build_job_contract(
        issue={
            "number": 69,
            "title": "[Feature]: Implement Assembly bot MVP",
            "html_url": "https://github.com/iterwheel/voyager-sandbox/issues/69",
            "body": _ISSUE_BODY,
        },
        repository="iterwheel/voyager-sandbox",
        branch_name="69-implement-assembly-bot-mvp",
        delivery_id="abc-123",
    )
    return contract.to_dict()


def test_contract_fields_match_schema() -> None:
    data = _build()
    assert data["repository"] == "iterwheel/voyager-sandbox"
    assert data["issue_number"] == 69
    assert data["issue_url"].endswith("/issues/69")
    assert data["branch_name"] == "69-implement-assembly-bot-mvp"
    assert data["base_branch"] == "main"
    assert data["delivery_id"] == "abc-123"
    assert data["requested_at"]  # iso utc string present


def test_forbidden_operations_match_canonical_constant() -> None:
    """Per D9: contract surfaces VOY-1805 §5 Deny column verbatim."""
    data = _build()
    assert tuple(data["forbidden_operations"]) == FORBIDDEN_OPERATIONS
    assert "Merge pull requests" in data["forbidden_operations"]
    assert "Approve its own pull requests" in data["forbidden_operations"]
    assert "Resolve review threads as a reviewer" in data["forbidden_operations"]
    assert (
        "Apply `clearance-4-ready-for-merge` or `countdown-go` labels"
        in data["forbidden_operations"]
    )


def test_verification_commands_present() -> None:
    data = _build()
    assert tuple(data["verification_commands"]) == VERIFICATION_COMMANDS
    assert "pytest tests/" in data["verification_commands"]


def test_acceptance_criteria_extracted_from_bullets() -> None:
    data = _build()
    assert data["acceptance_criteria_source"] == "section"
    assert any("Assembly responds to /assembly" in item for item in data["acceptance_criteria"])
    assert len(data["acceptance_criteria"]) == 3


def test_task_summary_extracted_from_section() -> None:
    data = _build()
    assert data["task_summary_source"] == "section"
    assert "Assembly" in data["task_summary"]


def test_d14_fallback_when_sections_missing() -> None:
    """D14: when AC / Problem sections are absent, fall back to title."""
    contract = build_job_contract(
        issue={
            "number": 70,
            "title": "[Bug]: Crash on startup",
            "html_url": "https://example/issues/70",
            "body": "Just a free-form description, no sections.",
        },
        repository="iterwheel/voyager-sandbox",
        branch_name="70-crash-on-startup",
        delivery_id="d",
    ).to_dict()
    assert contract["acceptance_criteria_source"] == "title_fallback"
    assert contract["acceptance_criteria"] == ["[Bug]: Crash on startup"]
    assert contract["task_summary_source"] == "title_fallback"
    assert contract["task_summary"] == "[Bug]: Crash on startup"


def test_to_dict_returns_list_for_tuples() -> None:
    data = _build()
    assert isinstance(data["forbidden_operations"], list)
    assert isinstance(data["verification_commands"], list)
