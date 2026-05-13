"""Unit tests for _compute_status (VOY-1809 §β aggregation rules).

Red phase: scenarios 3-8 and 11 fail against the pre-β _compute_status
implementation. Scenarios 1, 2, 9, 10 pass against both old and new logic.

β rules (precedence order):
  1. No threads → READY
  2. Any OPEN thread with effective_severity in {P1, P2} → BLOCKED
  3. Any NEEDS_HUMAN_JUDGMENT thread → PENDING
  4. Only OPEN threads remaining are P3 → READY (low-priority message)
  5. All RESOLVED → READY
"""

from __future__ import annotations

import pytest

from voyager.bots.clearance.models import Severity, Status, Thread, Verdict
from voyager.bots.clearance.pipeline import _compute_status

# ---------------------------------------------------------------------------
# Thread factory
# ---------------------------------------------------------------------------

_THREAD_COUNTER = 0


def _thread(
    verdict: Verdict,
    severity: Severity,
    *,
    codex_severity: Severity | None = None,
) -> Thread:
    """Construct a minimal Thread for status-aggregation tests."""
    global _THREAD_COUNTER
    _THREAD_COUNTER += 1
    return Thread(
        id=f"thread-{_THREAD_COUNTER:03d}",
        comment_id=_THREAD_COUNTER,
        path=f"src/file_{_THREAD_COUNTER}.py",
        codex_severity=codex_severity if codex_severity is not None else severity,
        effective_severity=severity,
        verdict=verdict,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open(severity: Severity) -> Thread:
    return _thread(Verdict.OPEN, severity)


def _resolved(severity: Severity) -> Thread:
    return _thread(Verdict.RESOLVED, severity)


def _nhj(severity: Severity) -> Thread:
    return _thread(Verdict.NEEDS_HUMAN_JUDGMENT, severity)


# ---------------------------------------------------------------------------
# Scenario 1-2: Empty / baseline
# ---------------------------------------------------------------------------


def test_no_threads_ready() -> None:
    """Scenario 1: No threads → READY with standard message."""
    status, reason = _compute_status([])
    assert status == Status.READY
    assert reason == "no Codex review threads on PR"


def test_all_resolved_mixed_severities_ready() -> None:
    """Scenario 2: All RESOLVED (P1, P2, P3 mix) → READY."""
    threads = [
        _resolved(Severity.P1),
        _resolved(Severity.P2),
        _resolved(Severity.P3),
    ]
    status, reason = _compute_status(threads)
    assert status == Status.READY
    assert reason == "all Codex review threads RESOLVED"


# ---------------------------------------------------------------------------
# Scenario 3-6: Severity-aware OPEN blocking
# ---------------------------------------------------------------------------

_BLOCKING_CASES = [
    # (threads, expected_reason)
    (
        [_open(Severity.P1)],
        "1 high-priority thread still OPEN",
    ),
    (
        [_open(Severity.P2), _open(Severity.P2)],
        "2 high-priority threads still OPEN",
    ),
    # Scenario 5: P1 + P3 OPEN → BLOCKED, count only high-priority
    (
        [_open(Severity.P1), _open(Severity.P3)],
        "1 high-priority thread still OPEN",
    ),
    # Scenario 6: P1 OPEN + P3 RESOLVED → BLOCKED on the P1
    (
        [_open(Severity.P1), _resolved(Severity.P3)],
        "1 high-priority thread still OPEN",
    ),
]


@pytest.mark.parametrize(("threads", "expected_reason"), _BLOCKING_CASES)
def test_high_priority_open_blocked(threads: list[Thread], expected_reason: str) -> None:
    """Scenarios 3-6: Any OPEN P1/P2 → BLOCKED; reason counts only high-priority."""
    status, reason = _compute_status(threads)
    assert status == Status.BLOCKED
    assert reason == expected_reason


# ---------------------------------------------------------------------------
# Scenario 7-8: P3-only OPEN no longer blocks (KEY β BEHAVIOR)
# ---------------------------------------------------------------------------

_P3_ONLY_CASES = [
    # Scenario 7: single P3 OPEN thread
    (
        [_open(Severity.P3)],
        "all blocking threads RESOLVED; 1 low-priority thread still open",
    ),
    # Scenario 8: P3 OPEN + resolved P1s
    (
        [_open(Severity.P3), _resolved(Severity.P1), _resolved(Severity.P1)],
        "all blocking threads RESOLVED; 1 low-priority thread still open",
    ),
]


@pytest.mark.parametrize(("threads", "expected_reason"), _P3_ONLY_CASES)
def test_p3_only_open_not_blocking(threads: list[Thread], expected_reason: str) -> None:
    """Scenarios 7-8: P3-only OPEN threads do NOT block; PR is READY_WITH_LOW_PRIORITY."""
    status, reason = _compute_status(threads)
    assert status == Status.READY_WITH_LOW_PRIORITY
    assert reason == expected_reason


# ---------------------------------------------------------------------------
# Scenario 9-10: NEEDS_HUMAN_JUDGMENT (unchanged behavior)
# ---------------------------------------------------------------------------

_NHJ_CASES = [
    # Scenario 9: single NHJ P2
    (
        [_nhj(Severity.P2)],
        "1 Codex review thread needs human judgment",
    ),
    # Scenario 10: NHJ + RESOLVED
    (
        [_nhj(Severity.P2), _resolved(Severity.P1)],
        "1 Codex review thread needs human judgment",
    ),
]


@pytest.mark.parametrize(("threads", "expected_reason"), _NHJ_CASES)
def test_nhj_pending(threads: list[Thread], expected_reason: str) -> None:
    """Scenarios 9-10: NEEDS_HUMAN_JUDGMENT → PENDING; message unchanged."""
    status, reason = _compute_status(threads)
    assert status == Status.PENDING
    assert reason == expected_reason


# ---------------------------------------------------------------------------
# Scenario 11: Edge case — OPEN P2 + NHJ → BLOCKED (β: rule 2 fires before rule 3)
# ---------------------------------------------------------------------------


def test_high_priority_open_beats_nhj() -> None:
    """Scenario 11: BLOCKED takes precedence over PENDING when P2 OPEN coexists with NHJ."""
    threads = [_open(Severity.P2), _nhj(Severity.P2)]
    status, reason = _compute_status(threads)
    assert status == Status.BLOCKED
    assert reason == "1 high-priority thread still OPEN"
