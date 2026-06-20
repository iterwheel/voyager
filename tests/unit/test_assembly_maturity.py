"""Unit tests for Assembly gate maturity (VOY-1822)."""

from __future__ import annotations

from typing import Any

from voyager.bots.assembly.ac_spotcheck import (
    ADVISORY_FINDING_DIRECTION,
    BLOCKING_FINDING_DIRECTION,
    AcceptanceSpotCheckFinding,
    AcceptanceSpotCheckResult,
    check_acceptance_exact_tokens,
)
from voyager.bots.assembly.adapters import (
    _AC_SPOTCHECK_MATURITY,
    _spotcheck_has_blocking_findings,
    _spotcheck_result_for_maturity,
)
from voyager.bots.assembly.maturity import DEFAULT_GATE_MATURITY, GateMaturity


def test_gate_maturity_enum_values() -> None:
    """GateMaturity has three well-defined levels."""
    assert GateMaturity.L1.value == "L1"
    assert GateMaturity.L2.value == "L2"
    assert GateMaturity.L3.value == "L3"


def test_default_maturity_is_l1() -> None:
    """New gates default to L1 (advisory-only)."""
    assert DEFAULT_GATE_MATURITY == GateMaturity.L1


def test_ac_spotcheck_maturity_is_l3() -> None:
    """The AC spotcheck gate was already shipped as blocking (L3)."""
    assert _AC_SPOTCHECK_MATURITY == GateMaturity.L3


def test_l1_gate_does_not_block_on_finding() -> None:
    """A gate at L1 records findings in details but does not block publish.

    This directly tests the branching logic from the PiOhMyPiDeepSeekAdapter
    spotcheck section.  At L1, findings go into ``details`` and execution
    continues; at L3 the same findings would return a blocked result.
    """
    issue_body = """## Acceptance Criteria
- Must declare `mandatory-bind` scope"""
    body = "no matching tokens in the diff"
    ac = ["Must declare `mandatory-bind` scope"]

    # The check produces findings (would block at L3)
    base_result = check_acceptance_exact_tokens(
        issue_body=issue_body,
        changed_text=body,
        acceptance_criteria=ac,
    )
    assert not base_result.ok
    assert len(base_result.findings) > 0

    # ---- L1 branch: record findings but do not block ----
    details: dict[str, Any] = {}
    result = _spotcheck_result_for_maturity(base_result, GateMaturity.L1)
    details["ac_spotcheck"] = result.to_dict()
    maturity = GateMaturity.L1
    blocked = False
    if not result.ok:
        if maturity == GateMaturity.L1:
            details["ac_spotcheck_maturity"] = "L1"
        elif _spotcheck_has_blocking_findings(result):
            blocked = True
    assert not blocked
    assert details["ac_spotcheck_maturity"] == "L1"
    assert details["ac_spotcheck"]["findings"][0]["direction"] == ADVISORY_FINDING_DIRECTION

    # ---- L3 branch (comparison): findings block publish ----
    details = {}
    result = _spotcheck_result_for_maturity(base_result, GateMaturity.L3)
    details["ac_spotcheck"] = result.to_dict()
    maturity = GateMaturity.L3
    blocked = False
    if not result.ok:
        if maturity == GateMaturity.L1:
            details["ac_spotcheck_maturity"] = "L1"
        elif _spotcheck_has_blocking_findings(result):
            blocked = True
    assert blocked
    assert "ac_spotcheck_maturity" not in details
    assert details["ac_spotcheck"]["findings"][0]["direction"] == BLOCKING_FINDING_DIRECTION


def test_spotcheck_direction_controls_automatic_blocking() -> None:
    block_result = AcceptanceSpotCheckResult(
        (
            AcceptanceSpotCheckFinding(
                source="acceptance_criterion",
                criterion="Add value `mandatory-bind`",
                required_tokens=("mandatory-bind",),
                missing_tokens=("mandatory-bind",),
                direction=BLOCKING_FINDING_DIRECTION,
            ),
        )
    )
    advisory_result = AcceptanceSpotCheckResult(
        (
            AcceptanceSpotCheckFinding(
                source="l1_advisory_gate",
                criterion="Add value `mandatory-bind`",
                required_tokens=("mandatory-bind",),
                missing_tokens=("mandatory-bind",),
                direction=ADVISORY_FINDING_DIRECTION,
            ),
        )
    )

    assert _spotcheck_has_blocking_findings(block_result)
    assert not _spotcheck_has_blocking_findings(advisory_result)
