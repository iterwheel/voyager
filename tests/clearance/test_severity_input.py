"""Unit tests for voyager.bots.clearance.severity_input.

Covers extract_severity_and_kind: severity badge parsing and finding_kind
classification from Codex review thread comment bodies.

Red phase: these tests fail with ImportError until the production module exists.
"""

from __future__ import annotations

import pytest
from voyager.bots.clearance.severity_input import extract_severity_and_kind

from voyager.bots.clearance.models import Severity


def _comments(body: str) -> list[dict]:
    return [{"body": body}]


# ---------------------------------------------------------------------------
# Severity extraction — badge marker variants
# ---------------------------------------------------------------------------

_SEVERITY_BADGE_CASES = [
    ("![P1 Badge] critical problem", Severity.P1),
    ("![P2 Badge] moderate issue", Severity.P2),
    ("![P3 Badge] minor nit", Severity.P3),
    ("|P1| severity table marker", Severity.P1),
    ("**P2** bold marker variant", Severity.P2),
]


@pytest.mark.parametrize(("body", "expected"), _SEVERITY_BADGE_CASES)
def test_severity_badge_extraction(body: str, expected: Severity) -> None:
    """Recognized badge markers are parsed to the correct Severity member."""
    sev, _ = extract_severity_and_kind(_comments(body))
    assert sev == expected


# ---------------------------------------------------------------------------
# Severity default — no badge / empty / None
# ---------------------------------------------------------------------------


def test_severity_default_no_badge() -> None:
    """Body with no P-badge → default Severity.P3."""
    sev, _ = extract_severity_and_kind(_comments("Some comment with no badge at all"))
    assert sev == Severity.P3


def test_severity_default_empty_body() -> None:
    """Empty comment body → default Severity.P3."""
    sev, _ = extract_severity_and_kind(_comments(""))
    assert sev == Severity.P3


def test_severity_default_comments_none() -> None:
    """comments=None → default Severity.P3."""
    sev, _ = extract_severity_and_kind(None)
    assert sev == Severity.P3


# ---------------------------------------------------------------------------
# Finding-kind extraction
# ---------------------------------------------------------------------------

_FINDING_KIND_CASES = [
    # (body, expected_kind)
    (
        "This is required check that uses paths-ignore to skip workflows",
        "required_check_coupling",
    ),
    (
        "The required status check bypasses paths-ignore configuration",
        "required_check_coupling",
    ),
    (
        "required check but nothing about p-a-t-h-s-i-g-n-o-r-e here",
        None,
    ),
    (
        "Unrelated comment about code style and naming conventions",
        None,
    ),
]


@pytest.mark.parametrize(("body", "expected_kind"), _FINDING_KIND_CASES)
def test_finding_kind_extraction(body: str, expected_kind: str | None) -> None:
    """finding_kind is returned when ALL required cues are present; None otherwise."""
    _, kind = extract_severity_and_kind(_comments(body))
    assert kind == expected_kind


# ---------------------------------------------------------------------------
# Combined / edge cases
# ---------------------------------------------------------------------------


def test_empty_comments_list() -> None:
    """comments=[] → (Severity.P3, None)."""
    assert extract_severity_and_kind([]) == (Severity.P3, None)


def test_none_comments() -> None:
    """comments=None → (Severity.P3, None)."""
    assert extract_severity_and_kind(None) == (Severity.P3, None)


def test_combined_p1_badge_and_required_check_coupling() -> None:
    """Body with both P1 badge and required_check_coupling cue → joint extraction."""
    body = "![P1 Badge] required check paths-ignore causes coupling"
    sev, kind = extract_severity_and_kind(_comments(body))
    assert sev == Severity.P1
    assert kind == "required_check_coupling"


# ---------------------------------------------------------------------------
# Case-insensitivity for finding_kind
# ---------------------------------------------------------------------------


def test_finding_kind_case_insensitive() -> None:
    """UPPERCASE required_check_coupling cues are recognized."""
    body = "REQUIRED CHECK PATHS-IGNORE blah blah"
    _, kind = extract_severity_and_kind(_comments(body))
    assert kind == "required_check_coupling"
