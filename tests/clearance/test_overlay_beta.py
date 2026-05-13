"""Regression tests for apply_swm_overlay — β READY_WITH_LOW_PRIORITY path.

Fix 1 (Codex P2 on overlay.py:25): automation status "ready_with_low_priority"
was previously caught by the early-return guard (swm_status not in
{"blocked", "pending", "error"}) and discarded, so the live evaluator's
blocked-by-threads decision stood even though β should have cleared it.

These tests verify the corrected behaviour: apply_swm_overlay produces a
success-conclusion evaluation with status="clearance_ok" and the
clearance-ok label when automation.status is "ready_with_low_priority".

Also verifies that the "all RESOLVED" path (Status.READY plain) still
no-ops through the overlay (evaluation unchanged).
"""

from __future__ import annotations

from voyager.bots.clearance.constants import (
    CLEARANCE_LABELS,
    CLEARANCE_OK_LABEL,
)
from voyager.bots.clearance.overlay import apply_swm_overlay

# ---------------------------------------------------------------------------
# Minimal ClearanceEvaluation factory
# ---------------------------------------------------------------------------


def _blocked_evaluation() -> dict:
    """A minimal evaluation that looks like a live-evaluator 'blocked' result."""
    return {
        "status": "clearance_blocked",
        "conclusion": "failure",
        "issue_number": 42,
        "pr_number": 42,
        "classifier": "clearance-v1",
        "summary": "Clearance is not ready yet.",
        "review_state": {
            "current_approvals": [],
            "stale_approvals": [],
            "blocking_reviewers": [],
            "unresolved_thread_count": 1,
        },
        "confidence": {
            "reasons": ["1 review thread(s) are unresolved."],
            "semantic_fix_verified": False,
            "semantic_fix_note": "",
        },
        "labels": {
            "add": ["clearance-blocked"],
            "remove": ["clearance-ready", "clearance-ok", "clearance-pending"],
        },
        "reactions": {"add": ["eyes"], "remove": ["+1", "rocket"]},
        "pr_url": "https://github.com/example/repo/pull/42",
        "head_sha": "abc123",
        "target_kind": "pull_request",
    }


def _ready_evaluation() -> dict:
    """A minimal evaluation that looks like a live-evaluator 'ready' result."""
    ev = _blocked_evaluation()
    ev["status"] = "clearance_ready"
    ev["conclusion"] = "success"
    ev["labels"] = {
        "add": ["clearance-ready"],
        "remove": ["clearance-ok", "clearance-blocked", "clearance-pending"],
    }
    ev["reactions"] = {"add": ["+1"], "remove": ["eyes", "rocket"]}
    return ev


# ---------------------------------------------------------------------------
# Scenario 1: ready_with_low_priority → success override
# ---------------------------------------------------------------------------


def test_ready_with_low_priority_produces_success() -> None:
    """β P3-only case: automation.status=ready_with_low_priority → conclusion=success."""
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED; 1 low-priority thread still open",
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    result = apply_swm_overlay(_blocked_evaluation(), automation)
    assert result["conclusion"] == "success"


def test_ready_with_low_priority_sets_clearance_ok_status() -> None:
    """β P3-only case: overlay sets status=clearance_ok."""
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED; 2 low-priority threads still open",
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    result = apply_swm_overlay(_blocked_evaluation(), automation)
    assert result["status"] == "clearance_ok"


def test_ready_with_low_priority_sets_summary_to_automation_reason() -> None:
    """β P3-only case: overlay sets summary to automation.reason."""
    reason = "all blocking threads RESOLVED; 1 low-priority thread still open"
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": reason,
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    result = apply_swm_overlay(_blocked_evaluation(), automation)
    assert result["summary"] == reason


def test_ready_with_low_priority_applies_clearance_ok_label() -> None:
    """β P3-only case: overlay adds clearance-ok label and removes all others."""
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED; 1 low-priority thread still open",
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    result = apply_swm_overlay(_blocked_evaluation(), automation)
    labels = result["labels"]
    assert CLEARANCE_OK_LABEL in labels["add"]
    for label in CLEARANCE_LABELS:
        if label != CLEARANCE_OK_LABEL:
            assert label in labels["remove"], (
                f"expected {label!r} in labels['remove'] but got {labels['remove']!r}"
            )


def test_ready_with_low_priority_sets_positive_reaction() -> None:
    """β P3-only case: overlay sets +1 reaction (not eyes)."""
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED; 1 low-priority thread still open",
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    result = apply_swm_overlay(_blocked_evaluation(), automation)
    assert "+1" in result["reactions"]["add"]
    assert "eyes" not in result["reactions"]["add"]


def test_ready_with_low_priority_fallback_reason_when_none() -> None:
    """β P3-only case: overlay builds fallback reason when automation.reason is absent."""
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    result = apply_swm_overlay(_blocked_evaluation(), automation)
    assert result["conclusion"] == "success"
    assert "ready_with_low_priority" in result["summary"]


# ---------------------------------------------------------------------------
# Scenario 2: automation disabled → evaluation unchanged (regression guard)
# ---------------------------------------------------------------------------


def test_disabled_automation_no_op() -> None:
    """Disabled automation dict → overlay no-ops, evaluation returned unchanged."""
    automation = {"enabled": False, "status": "ready_with_low_priority"}
    ev = _blocked_evaluation()
    result = apply_swm_overlay(ev, automation)
    assert result is ev


def test_none_automation_no_op() -> None:
    """None automation → overlay no-ops."""
    ev = _blocked_evaluation()
    result = apply_swm_overlay(ev, None)
    assert result is ev


# ---------------------------------------------------------------------------
# Scenario 3: plain READY (all-resolved path) → evaluation unchanged
# ---------------------------------------------------------------------------


def test_plain_ready_status_no_op() -> None:
    """automation.status='ready' (all RESOLVED) → overlay no-ops.

    This is the existing 7B-3 behavior for 'all RESOLVED': the evaluation
    is already correct (conclusion=success) so the overlay must not override it.
    """
    automation = {
        "enabled": True,
        "status": "ready",
        "reason": "all Codex review threads RESOLVED",
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    ev = _ready_evaluation()
    result = apply_swm_overlay(ev, automation)
    assert result is ev


# ---------------------------------------------------------------------------
# Scenario 4: blocked/pending/error statuses still work (regression guard)
# ---------------------------------------------------------------------------


def test_blocked_status_still_overrides_to_failure() -> None:
    """Existing blocked path still produces failure conclusion."""
    automation = {
        "enabled": True,
        "status": "blocked",
        "reason": "2 high-priority threads still OPEN",
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    result = apply_swm_overlay(_ready_evaluation(), automation)
    assert result["conclusion"] == "failure"
    assert result["status"] == "clearance_blocked"


def test_pending_status_still_overrides_to_neutral() -> None:
    """Existing pending path still produces neutral conclusion."""
    automation = {
        "enabled": True,
        "status": "pending",
        "reason": "1 Codex review thread needs human judgment",
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    result = apply_swm_overlay(_ready_evaluation(), automation)
    assert result["conclusion"] == "neutral"
    assert result["status"] == "clearance_pending"


def test_error_status_still_overrides_to_failure() -> None:
    """Existing error path still produces failure conclusion."""
    automation = {
        "enabled": True,
        "status": "error",
        "error": "pipeline failed: RuntimeError: fetch timeout",
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    result = apply_swm_overlay(_ready_evaluation(), automation)
    assert result["conclusion"] == "failure"
    assert result["status"] == "clearance_blocked"
