"""Unit tests for Assembly preconditions (VOY-1817 Surface 14)."""

from __future__ import annotations

from typing import Any

from voyager.bots.assembly.constants import (
    REFUSAL_ISSUE_CLOSED,
    REFUSAL_MISSING_STACK_TYPE,
    REFUSAL_NOT_BLUEPRINT_READY,
    REFUSAL_PR_NOT_ISSUE,
)
from voyager.bots.assembly.preconditions import validate_preconditions


def _issue(**overrides: Any) -> dict[str, Any]:
    base = {
        "number": 69,
        "title": "[Feature]: Implement Assembly bot MVP",
        "state": "open",
        "labels": [
            {"name": "blueprint-ready"},
            {"name": "stack-type-feature"},
        ],
    }
    base.update(overrides)
    return base


def test_happy_path_is_ok() -> None:
    result = validate_preconditions(_issue())
    assert result.ok
    assert result.as_refusal_dict() is None


def test_pull_request_payload_is_refused() -> None:
    issue = _issue(pull_request={"url": "https://api.github.com/...pulls/1"})
    result = validate_preconditions(issue)
    assert not result.ok
    assert result.reason == REFUSAL_PR_NOT_ISSUE


def test_missing_blueprint_ready_is_refused() -> None:
    issue = _issue(labels=[{"name": "stack-type-feature"}])
    result = validate_preconditions(issue)
    assert not result.ok
    assert result.reason == REFUSAL_NOT_BLUEPRINT_READY
    assert "blueprint-ready" in result.missing_labels


def test_missing_stack_type_is_refused() -> None:
    issue = _issue(labels=[{"name": "blueprint-ready"}])
    result = validate_preconditions(issue)
    assert not result.ok
    assert result.reason == REFUSAL_MISSING_STACK_TYPE
    assert any(label.startswith("stack-type-") for label in result.missing_labels)


def test_allow_missing_stack_override_lets_through() -> None:
    issue = _issue(labels=[{"name": "blueprint-ready"}])
    result = validate_preconditions(issue, allow_missing_stack=True)
    assert result.ok


def test_closed_issue_is_refused() -> None:
    issue = _issue(state="closed")
    result = validate_preconditions(issue)
    assert not result.ok
    assert result.reason == REFUSAL_ISSUE_CLOSED


def test_empty_issue_is_refused() -> None:
    assert not validate_preconditions(None).ok
    assert not validate_preconditions({}).ok


def test_string_labels_are_supported() -> None:
    """GitHub sometimes serialises labels as bare strings."""
    issue = _issue(labels=["blueprint-ready", "stack-type-bug"])
    result = validate_preconditions(issue)
    assert result.ok


def test_refusal_dict_shape_matches_schema() -> None:
    issue = _issue(labels=[])
    result = validate_preconditions(issue)
    refusal = result.as_refusal_dict()
    assert refusal is not None
    assert set(refusal.keys()) == {"reason", "missing_labels", "outside_allow_list"}
    assert refusal["outside_allow_list"] is False
