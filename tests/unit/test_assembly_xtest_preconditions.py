"""Cross-test for Assembly precondition gates — independent scenarios.

Covers: every refusal reason path, the ``--allow-missing-stack`` override,
and the happy path.  Does not duplicate GLM's label-combination coverage.
"""

from __future__ import annotations

from voyager.bots.assembly.preconditions import PreconditionResult, validate_preconditions


def _issue(**overrides: object) -> dict:
    """Build a minimal issue dict for precondition testing."""
    base: dict = {
        "number": 99,
        "title": "Test Issue",
        "state": "open",
        "labels": [{"name": "blueprint-ready"}, {"name": "stack-type-feature"}],
    }
    base.update(overrides)  # type: ignore[arg-type]
    return base


# ---------------------------------------------------------------------------
# Refusal: PR-not-issue
# ---------------------------------------------------------------------------


class TestRefusalPRNotIssue:
    def test_none_issue_refused(self) -> None:
        result = validate_preconditions(None)
        assert result.ok is False
        assert result.reason == "pr_not_issue"
        assert result.missing_labels == []

    def test_pull_request_key_set_refused(self) -> None:
        issue = _issue(pull_request={"url": "https://api.github.com/repos/o/r/pulls/1"})
        result = validate_preconditions(issue)
        assert result.ok is False
        assert result.reason == "pr_not_issue"
        assert result.missing_labels == []


# ---------------------------------------------------------------------------
# Refusal: issue closed
# ---------------------------------------------------------------------------


class TestRefusalIssueClosed:
    def test_closed_issue_refused(self) -> None:
        issue = _issue(state="closed")
        result = validate_preconditions(issue)
        assert result.ok is False
        assert result.reason == "issue_closed"
        assert result.missing_labels == []

    def test_closed_uppercase_refused(self) -> None:
        issue = _issue(state="CLOSED")
        result = validate_preconditions(issue)
        assert result.ok is False
        assert result.reason == "issue_closed"


# ---------------------------------------------------------------------------
# Refusal: missing blueprint-ready
# ---------------------------------------------------------------------------


class TestRefusalMissingBlueprintReady:
    def test_no_blueprint_ready_label(self) -> None:
        issue = _issue(labels=[{"name": "stack-type-feature"}])
        result = validate_preconditions(issue)
        assert result.ok is False
        assert result.reason == "missing_blueprint_ready_label"
        assert "blueprint-ready" in result.missing_labels

    def test_empty_labels(self) -> None:
        issue = _issue(labels=[])
        result = validate_preconditions(issue)
        assert result.ok is False
        assert result.reason == "missing_blueprint_ready_label"
        assert "blueprint-ready" in result.missing_labels
        assert "stack-type-*" in result.missing_labels


# ---------------------------------------------------------------------------
# Refusal: missing stack-type-*
# ---------------------------------------------------------------------------


class TestRefusalMissingStackType:
    def test_no_stack_type_label(self) -> None:
        issue = _issue(labels=[{"name": "blueprint-ready"}])
        result = validate_preconditions(issue)
        assert result.ok is False
        assert result.reason == "missing_stack_type_label"
        assert "stack-type-*" in result.missing_labels

    def test_unrelated_labels_only(self) -> None:
        issue = _issue(labels=[{"name": "blueprint-ready"}, {"name": "bug"}])
        result = validate_preconditions(issue)
        assert result.ok is False
        assert result.reason == "missing_stack_type_label"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_all_preconditions_met(self) -> None:
        issue = _issue()
        result = validate_preconditions(issue)
        assert result.ok is True
        assert result.reason is None
        assert result.missing_labels == []

    def test_extra_labels_dont_break(self) -> None:
        issue = _issue(
            labels=[
                {"name": "blueprint-ready"},
                {"name": "stack-type-feature"},
                {"name": "stack-area-github"},
                {"name": "priority-high"},
            ]
        )
        result = validate_preconditions(issue)
        assert result.ok is True

    def test_string_labels_accepted(self) -> None:
        issue = _issue(labels=["blueprint-ready", "stack-type-bug"])
        result = validate_preconditions(issue)
        assert result.ok is True


# ---------------------------------------------------------------------------
# --allow-missing-stack override
# ---------------------------------------------------------------------------


class TestAllowMissingStackOverride:
    def test_missing_stack_with_override_passes(self) -> None:
        issue = _issue(labels=[{"name": "blueprint-ready"}])
        result = validate_preconditions(issue, allow_missing_stack=True)
        assert result.ok is True
        assert result.reason is None
        assert result.missing_labels == []

    def test_override_does_not_skip_blueprint_ready(self) -> None:
        issue = _issue(labels=[{"name": "stack-type-feature"}])
        result = validate_preconditions(issue, allow_missing_stack=True)
        assert result.ok is False
        assert result.reason == "missing_blueprint_ready_label"
        assert "blueprint-ready" in result.missing_labels
        # stack-type-* should NOT be in missing_labels when override is on
        assert "stack-type-*" not in result.missing_labels

    def test_override_with_no_labels_at_all(self) -> None:
        issue = _issue(labels=[])
        result = validate_preconditions(issue, allow_missing_stack=True)
        assert result.ok is False
        assert result.reason == "missing_blueprint_ready_label"
        assert result.missing_labels == ["blueprint-ready"]


# ---------------------------------------------------------------------------
# as_refusal_dict shape
# ---------------------------------------------------------------------------


class TestAsRefusalDict:
    def test_ok_result_returns_none(self) -> None:
        result = PreconditionResult(ok=True, reason=None, missing_labels=[])
        assert result.as_refusal_dict() is None

    def test_refusal_shape(self) -> None:
        result = PreconditionResult(
            ok=False, reason="missing_blueprint_ready_label", missing_labels=["blueprint-ready"]
        )
        d = result.as_refusal_dict()
        assert d is not None
        assert d["reason"] == "missing_blueprint_ready_label"
        assert d["missing_labels"] == ["blueprint-ready"]
        assert d["outside_allow_list"] is False
