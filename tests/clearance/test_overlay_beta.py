"""Regression tests for apply_swm_overlay — β READY_WITH_LOW_PRIORITY path.

Fix 1 (Codex P1 on overlay.py:34): automation status "ready_with_low_priority"
must NOT override the conclusion to success when non-Codex-thread blockers exist
(draft PR, missing approval, human CHANGES_REQUESTED). The overlay preserves those
evaluations unchanged and only applies the success override when unresolved review
threads are the sole blocker.

Fix 2 (Codex P2 on constants.py:24): clearance-ok is not a provisioned GitHub
label. The β success case now uses clearance-ready instead.

Also verifies that the "all RESOLVED" path (Status.READY plain) clears
thread-only live-evaluator blockers while preserving non-thread blockers.
"""

from __future__ import annotations

import pytest

from voyager.bots.clearance.constants import (
    CLEARANCE_LABELS,
    CLEARANCE_READY_LABEL,
)
from voyager.bots.clearance.overlay import apply_swm_overlay

# ---------------------------------------------------------------------------
# Minimal ClearanceEvaluation factory
# ---------------------------------------------------------------------------


def _blocked_evaluation() -> dict:
    """A minimal evaluation blocked only by unresolved review threads."""
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
            "remove": ["clearance-ready", "clearance-pending"],
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
        "remove": ["clearance-blocked", "clearance-pending"],
    }
    ev["reactions"] = {"add": ["+1"], "remove": ["eyes", "rocket"]}
    return ev


def _draft_blocked_evaluation() -> dict:
    """Evaluation blocked by draft PR (non-Codex-thread blocker)."""
    ev = _blocked_evaluation()
    ev["status"] = "clearance_pending"
    ev["conclusion"] = "neutral"
    ev["review_state"]["unresolved_thread_count"] = 0
    ev["confidence"]["reasons"] = ["PR is still draft."]
    ev["labels"] = {
        "add": ["clearance-pending"],
        "remove": ["clearance-ready", "clearance-blocked"],
    }
    return ev


def _changes_requested_evaluation() -> dict:
    """Evaluation blocked by human CHANGES_REQUESTED (non-Codex-thread blocker)."""
    ev = _blocked_evaluation()
    ev["review_state"]["blocking_reviewers"] = ["alice"]
    ev["review_state"]["unresolved_thread_count"] = 1
    ev["confidence"]["reasons"] = [
        "Changes requested by: @alice.",
        "1 review thread(s) are unresolved.",
    ]
    return ev


# ---------------------------------------------------------------------------
# Scenario 1: ready_with_low_priority + thread-only blocker → success override
# ---------------------------------------------------------------------------


def test_ready_with_low_priority_produces_success() -> None:
    """β thread-only case: automation.status=ready_with_low_priority → conclusion=success."""
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED; 1 low-priority thread still open",
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    result = apply_swm_overlay(_blocked_evaluation(), automation)
    assert result["conclusion"] == "success"


def test_ready_with_low_priority_sets_clearance_ready_status() -> None:
    """β thread-only case: overlay sets status=clearance_ready (not clearance_ok)."""
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED; 2 low-priority threads still open",
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    result = apply_swm_overlay(_blocked_evaluation(), automation)
    assert result["status"] == "clearance_ready"


def test_ready_with_low_priority_sets_summary_to_automation_reason() -> None:
    """β thread-only case: overlay sets summary to automation.reason."""
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


def test_ready_with_low_priority_applies_clearance_ready_label() -> None:
    """β thread-only case: overlay adds clearance-ready label and removes all others."""
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED; 1 low-priority thread still open",
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    result = apply_swm_overlay(_blocked_evaluation(), automation)
    labels = result["labels"]
    assert CLEARANCE_READY_LABEL in labels["add"]
    for label in CLEARANCE_LABELS:
        if label != CLEARANCE_READY_LABEL:
            assert label in labels["remove"], (
                f"expected {label!r} in labels['remove'] but got {labels['remove']!r}"
            )


def test_ready_with_low_priority_sets_positive_reaction() -> None:
    """β thread-only case: overlay sets +1 reaction (not eyes)."""
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
    """β thread-only case: overlay builds fallback reason when automation.reason is absent."""
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
# Scenario 1b: ready_with_low_priority + non-thread blockers → pass-through
# ---------------------------------------------------------------------------


def test_ready_with_low_priority_draft_pr_preserves_evaluation() -> None:
    """β: draft PR is a non-thread blocker → evaluation returned unchanged."""
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED; 1 low-priority thread still open",
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    ev = _draft_blocked_evaluation()
    result = apply_swm_overlay(ev, automation)
    assert result is ev


def test_ready_with_low_priority_changes_requested_preserves_evaluation() -> None:
    """β: human CHANGES_REQUESTED is a non-thread blocker → evaluation returned unchanged."""
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED; 1 low-priority thread still open",
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    ev = _changes_requested_evaluation()
    result = apply_swm_overlay(ev, automation)
    assert result is ev


def test_ready_with_low_priority_changes_requested_conclusion_unchanged() -> None:
    """β: human CHANGES_REQUESTED → conclusion stays failure, not overridden to success."""
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED",
    }
    ev = _changes_requested_evaluation()
    result = apply_swm_overlay(ev, automation)
    assert result["conclusion"] == "failure"


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
# Scenario 3: plain READY (all-resolved path) → success override
# ---------------------------------------------------------------------------


def test_plain_ready_status_clears_thread_only_blocker() -> None:
    """automation.status='ready' clears a Codex thread that Stage 1.5 will sync."""
    automation = {
        "enabled": True,
        "status": "ready",
        "reason": "all Codex review threads RESOLVED",
        "sync_actions": [{"mutation": "resolve_review_thread", "threadId": "thread-1"}],
        "sync_actions_count": 1,
        "unresolved_codex_thread_count": 0,
    }
    result = apply_swm_overlay(_blocked_evaluation(), automation)
    assert result["conclusion"] == "success"
    assert result["status"] == "clearance_ready"
    assert CLEARANCE_READY_LABEL in result["labels"]["add"]
    assert "+1" in result["reactions"]["add"]


def test_plain_ready_status_with_skipped_sync_actions_still_clears_thread_only_blocker() -> None:
    """Skipped native UI sync must not make resolved SWM threads block readiness."""
    automation = {
        "enabled": True,
        "status": "ready",
        "reason": "all Codex review threads RESOLVED",
        "sync_actions": [
            {
                "mutation": "resolveReviewThread",
                "threadId": "thread-1",
                "result": {
                    "skipped": True,
                    "skip_reason": "viewerCanResolve is false",
                },
            }
        ],
        "sync_actions_count": 1,
        "unresolved_codex_thread_count": 0,
    }
    result = apply_swm_overlay(_blocked_evaluation(), automation)
    assert result["conclusion"] == "success"
    assert result["status"] == "clearance_ready"
    assert CLEARANCE_READY_LABEL in result["labels"]["add"]


def test_plain_ready_status_preserves_non_thread_blocker() -> None:
    """automation.status='ready' must not clear draft / PR-state blockers."""
    automation = {
        "enabled": True,
        "status": "ready",
        "reason": "no Codex review threads on PR",
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    ev = _draft_blocked_evaluation()
    result = apply_swm_overlay(ev, automation)
    assert result is ev


def test_plain_ready_status_preserves_untracked_human_thread() -> None:
    """automation.status='ready' must not clear threads outside Clearance automation."""
    automation = {
        "enabled": True,
        "status": "ready",
        "reason": "no Codex review threads on PR",
        "sync_actions": [],
        "sync_actions_count": 0,
        "unresolved_codex_thread_count": 0,
    }
    ev = _blocked_evaluation()
    result = apply_swm_overlay(ev, automation)
    assert result is ev


def test_plain_ready_status_preserves_no_approval_reason() -> None:
    """automation.status='ready' must not clear missing current-head approval."""
    automation = {
        "enabled": True,
        "status": "ready",
        "reason": "all Codex review threads RESOLVED",
        "sync_actions": [{"mutation": "resolve_review_thread", "threadId": "thread-1"}],
        "sync_actions_count": 1,
        "unresolved_codex_thread_count": 0,
    }
    ev = _blocked_evaluation()
    ev["confidence"]["reasons"] = [
        "1 review thread(s) are unresolved.",
        "No approval on the current PR head.",
    ]
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


# ---------------------------------------------------------------------------
# Scenario 5: ready_with_low_priority + non-Codex unresolved thread → pass-through
# Codex PR #12 inline comment 3237756524 (4th P1 finding)
# ---------------------------------------------------------------------------


def _blocked_with_extra_human_thread() -> dict:
    """Evaluation blocked by 1 P3 Codex thread AND 1 non-Codex unresolved thread.

    The live evaluator sees unresolved_thread_count=2; automation only processed
    1 Codex thread (codex_thread_count=1). The gap signals a human reviewer
    thread that the overlay must not clear.
    """
    ev = _blocked_evaluation()
    ev["review_state"]["unresolved_thread_count"] = 2
    ev["confidence"]["reasons"] = ["2 review thread(s) are unresolved."]
    return ev


def test_ready_with_low_priority_non_codex_thread_preserves_evaluation() -> None:
    """β: non-Codex unresolved thread gap → evaluation returned unchanged.

    PR has 1 P3 Codex thread (unresolved_codex_thread_count=1) plus 1 unresolved
    human thread.  The live evaluator reports unresolved_thread_count=2.  Because
    unresolved_thread_count > unresolved_codex_thread_count, at least one non-Codex
    thread is open and the overlay must not flip conclusion to success.
    """
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED; 1 low-priority thread still open",
        "unresolved_codex_thread_count": 1,
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    ev = _blocked_with_extra_human_thread()
    result = apply_swm_overlay(ev, automation)
    assert result is ev


def test_ready_with_low_priority_non_codex_thread_conclusion_unchanged() -> None:
    """β: non-Codex unresolved thread gap → conclusion stays failure."""
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED; 1 low-priority thread still open",
        "unresolved_codex_thread_count": 1,
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    ev = _blocked_with_extra_human_thread()
    result = apply_swm_overlay(ev, automation)
    assert result["conclusion"] == "failure"


def test_ready_with_low_priority_exact_match_allows_override() -> None:
    """β: unresolved_thread_count == unresolved_codex_thread_count → all threads are Codex → override fires."""
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED; 1 low-priority thread still open",
        "unresolved_codex_thread_count": 1,
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    ev = _blocked_evaluation()  # unresolved_thread_count=1, unresolved_codex_thread_count=1
    result = apply_swm_overlay(ev, automation)
    assert result["conclusion"] == "success"


def test_ready_with_low_priority_exact_match_preserves_no_approval_reason() -> None:
    """β: exact Codex-thread match does not clear missing current approval."""
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED; 1 low-priority thread still open",
        "unresolved_codex_thread_count": 1,
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    ev = _blocked_evaluation()
    ev["confidence"]["reasons"] = [
        "1 review thread(s) are unresolved.",
        "No approval on the current PR head.",
    ]
    result = apply_swm_overlay(ev, automation)
    assert result is ev


def test_ready_with_low_priority_no_codex_count_key_allows_override() -> None:
    """β: missing unresolved_codex_thread_count key (old automation dict) → override still fires.

    When unresolved_codex_thread_count is absent the check is skipped entirely
    (old automation dict predating this field). This test confirms the success
    override still fires when the key is absent.
    """
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED; 1 low-priority thread still open",
        # no unresolved_codex_thread_count key — old automation dict
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    ev = _blocked_evaluation()
    result = apply_swm_overlay(ev, automation)
    assert result["conclusion"] == "success"


# ---------------------------------------------------------------------------
# Scenario 5b: R5-P1 regression — resolved Codex threads must NOT count
# Codex PR #12 inline comment 3237870559
# ---------------------------------------------------------------------------


def _blocked_with_resolved_codex_and_extra_human_thread() -> dict:
    """Evaluation for a PR with 2 resolved Codex + 1 P3 OPEN Codex + 1 unresolved human thread.

    The live evaluator sees unresolved_thread_count=2 (1 P3 Codex OPEN + 1 human).
    The old (buggy) key codex_thread_count=3 (total Codex including resolved ones).
    The new key unresolved_codex_thread_count=1 (only the P3 OPEN Codex thread).
    unresolved_thread_count(2) > unresolved_codex_thread_count(1) → guard fires → preserved.
    """
    ev = _blocked_evaluation()
    ev["review_state"]["unresolved_thread_count"] = 2
    ev["confidence"]["reasons"] = ["2 review thread(s) are unresolved."]
    return ev


def test_r5_p1_resolved_codex_not_counted_in_guard() -> None:
    """R5-P1: unresolved_codex_thread_count excludes resolved threads.

    2 resolved Codex + 1 P3 OPEN Codex + 1 unresolved human thread.
    unresolved_codex_thread_count=1 (only the OPEN P3).
    unresolved_thread_count=2 (P3 Codex + human).
    2 > 1 → guard fires → evaluation preserved (human thread is still blocking).
    """
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED; 1 low-priority thread still open",
        "unresolved_codex_thread_count": 1,  # only the 1 OPEN P3 Codex thread
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    ev = _blocked_with_resolved_codex_and_extra_human_thread()
    result = apply_swm_overlay(ev, automation)
    assert result is ev, "guard should preserve evaluation when human thread is unresolved"
    assert result["conclusion"] == "failure"


# ---------------------------------------------------------------------------
# Scenario 6: r6-P1 — clearance_blocked + non-thread blocker coexisting
# Codex PR head 9f834d3, inline comment 3238070097
# ---------------------------------------------------------------------------


def _blocked_codex_thread_plus_no_approval() -> dict:
    """Evaluation blocked by 1 P3 Codex thread AND no current-head approval.

    evaluate_clearance_snapshot sets status=clearance_blocked (unresolved threads
    present) but ALSO appends a non-thread reason for the missing approval.
    The overlay must NOT clear this to clearance_ready.
    """
    ev = _blocked_evaluation()
    # unresolved_thread_count=1 already set; no current approvals → extra reason
    ev["confidence"]["reasons"] = [
        "1 review thread(s) are unresolved.",
        "No approval on the current PR head.",
    ]
    return ev


def _blocked_codex_thread_plus_stale_approval() -> dict:
    """Evaluation blocked by 1 P3 Codex thread AND only stale approval."""
    ev = _blocked_evaluation()
    ev["review_state"]["stale_approvals"] = ["reviewer"]
    ev["confidence"]["reasons"] = [
        "1 review thread(s) are unresolved.",
        "Only stale approval(s) exist: @reviewer.",
    ]
    return ev


def _blocked_codex_thread_plus_draft() -> dict:
    """Evaluation blocked by 1 P3 Codex thread AND draft PR."""
    ev = _blocked_evaluation()
    ev["confidence"]["reasons"] = [
        "PR is still draft.",
        "1 review thread(s) are unresolved.",
    ]
    return ev


def _blocked_codex_thread_plus_not_open() -> dict:
    """Evaluation blocked by 1 P3 Codex thread AND PR not open."""
    ev = _blocked_evaluation()
    ev["confidence"]["reasons"] = [
        "PR is not open.",
        "1 review thread(s) are unresolved.",
    ]
    return ev


def test_r6_p1_codex_thread_plus_no_approval_preserves_evaluation() -> None:
    """r6-P1: P3 Codex thread + no current-head approval → preserved."""
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED; 1 low-priority P3 thread still open",
        "unresolved_codex_thread_count": 1,
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    ev = _blocked_codex_thread_plus_no_approval()
    result = apply_swm_overlay(ev, automation)
    assert result is ev


def test_r6_p1_codex_thread_plus_no_approval_conclusion_unchanged() -> None:
    """r6-P1: missing approval keeps the conclusion as failure."""
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED; 1 low-priority P3 thread still open",
        "unresolved_codex_thread_count": 1,
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    ev = _blocked_codex_thread_plus_no_approval()
    result = apply_swm_overlay(ev, automation)
    assert result["conclusion"] == "failure"


def test_r6_p1_codex_thread_plus_stale_approval_preserves_evaluation() -> None:
    """r6-P1: P3 Codex thread + only stale approval → preserved."""
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED; 1 low-priority P3 thread still open",
        "unresolved_codex_thread_count": 1,
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    ev = _blocked_codex_thread_plus_stale_approval()
    result = apply_swm_overlay(ev, automation)
    assert result is ev


def test_r6_p1_codex_thread_plus_draft_preserves_evaluation() -> None:
    """r6-P1: clearance_blocked with P3 Codex thread + draft PR → preserved."""
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED; 1 low-priority P3 thread still open",
        "unresolved_codex_thread_count": 1,
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    ev = _blocked_codex_thread_plus_draft()
    result = apply_swm_overlay(ev, automation)
    assert result is ev


def test_r6_p1_codex_thread_plus_not_open_preserves_evaluation() -> None:
    """r6-P1: clearance_blocked with P3 Codex thread + PR not open → preserved."""
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED; 1 low-priority P3 thread still open",
        "unresolved_codex_thread_count": 1,
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    ev = _blocked_codex_thread_plus_not_open()
    result = apply_swm_overlay(ev, automation)
    assert result is ev


def test_r6_p1_thread_only_still_overrides() -> None:
    """r6-P1 regression guard: pure thread-only case with unresolved_codex_thread_count still overrides.

    Ensure the new guard doesn't break the existing happy path: exactly 1 reason
    (the unresolved-threads reason) and unresolved_thread_count == unresolved_codex_thread_count
    → override fires → conclusion=success.
    """
    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED; 1 low-priority P3 thread still open",
        "unresolved_codex_thread_count": 1,
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    ev = _blocked_evaluation()  # 1 reason: "1 review thread(s) are unresolved."
    result = apply_swm_overlay(ev, automation)
    assert result["conclusion"] == "success"


# ---------------------------------------------------------------------------
# Issue #25: SWM overlay with configured-approver gate
# ---------------------------------------------------------------------------


def _ready_for_approval_evaluation() -> dict:
    """Minimal clearance_ready_for_approval evaluation (new state from issue #25)."""
    from voyager.bots.clearance.constants import (
        ALL_CLEARANCE_LABELS,
        CLEARANCE_READY_FOR_APPROVAL_LABEL,
    )

    return {
        "status": "clearance_ready_for_approval",
        "conclusion": "neutral",
        "issue_number": 42,
        "pr_number": 42,
        "classifier": "clearance-v1",
        "summary": "Clearance is ready for human approval.",
        "review_state": {
            "current_approvals": ["someone-else"],
            "stale_approvals": [],
            "blocking_reviewers": [],
            "unresolved_thread_count": 0,
        },
        "confidence": {
            "reasons": ["Awaiting approval from configured reviewer(s): @required-approver"],
            "semantic_fix_verified": False,
            "semantic_fix_note": "",
        },
        "labels": {
            "add": [CLEARANCE_READY_FOR_APPROVAL_LABEL],
            "remove": [
                label
                for label in ALL_CLEARANCE_LABELS
                if label != CLEARANCE_READY_FOR_APPROVAL_LABEL
            ],
        },
        "reactions": {"add": ["eyes"], "remove": ["+1", "rocket"]},
        "pr_url": "https://github.com/example/repo/pull/42",
        "head_sha": "abc123",
        "target_kind": "pull_request",
    }


@pytest.fixture(autouse=True)
def reset_cache(monkeypatch):
    monkeypatch.delenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", raising=False)
    from voyager.bots.clearance.constants import reset_review_request_users_cache

    reset_review_request_users_cache()
    yield
    reset_review_request_users_cache()


def test_swm_ready_with_configured_user_not_approved_produces_ready_for_approval(
    monkeypatch,
) -> None:
    """SWM ready + configured user hasn't approved → clearance_ready_for_approval, NOT clearance_ready."""
    from voyager.bots.clearance.constants import (
        CLEARANCE_READY_FOR_APPROVAL_LABEL,
        reset_review_request_users_cache,
    )

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    reset_review_request_users_cache()

    automation = {
        "enabled": True,
        "status": "ready",
        "reason": "all Codex review threads RESOLVED",
        "sync_actions": [{"mutation": "resolve_review_thread", "threadId": "t-1"}],
        "sync_actions_count": 1,
        "unresolved_codex_thread_count": 0,
    }
    ev = _blocked_evaluation()
    result = apply_swm_overlay(ev, automation)
    # With configured approver not approved, overlay must produce ready_for_approval
    assert result["status"] == "clearance_ready_for_approval"
    assert result["labels"]["add"] == [CLEARANCE_READY_FOR_APPROVAL_LABEL]


def test_swm_ready_with_low_priority_configured_user_not_approved(monkeypatch) -> None:
    """SWM ready_with_low_priority + configured user hasn't approved → ready_for_approval."""
    from voyager.bots.clearance.constants import (
        CLEARANCE_READY_FOR_APPROVAL_LABEL,
        reset_review_request_users_cache,
    )

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    reset_review_request_users_cache()

    automation = {
        "enabled": True,
        "status": "ready_with_low_priority",
        "reason": "all blocking threads RESOLVED; 1 low-priority thread still open",
        "unresolved_codex_thread_count": 1,
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    ev = _blocked_evaluation()
    result = apply_swm_overlay(ev, automation)
    assert result["status"] == "clearance_ready_for_approval"
    assert result["labels"]["add"] == [CLEARANCE_READY_FOR_APPROVAL_LABEL]


def test_swm_overlay_ready_for_approval_label_remove_includes_legacy(monkeypatch) -> None:
    """SWM overlay ready_for_approval result includes ALL legacy labels in remove."""
    from voyager.bots.clearance.constants import (
        LEGACY_CLEARANCE_LABELS,
        reset_review_request_users_cache,
    )

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "required-approver")
    reset_review_request_users_cache()

    automation = {
        "enabled": True,
        "status": "ready",
        "reason": "all Codex review threads RESOLVED",
        "sync_actions": [{"mutation": "resolve_review_thread", "threadId": "t-1"}],
        "sync_actions_count": 1,
        "unresolved_codex_thread_count": 0,
    }
    ev = _blocked_evaluation()
    result = apply_swm_overlay(ev, automation)
    remove = result["labels"]["remove"]
    for label in LEGACY_CLEARANCE_LABELS:
        assert label in remove, f"Legacy label {label!r} should be in labels['remove']"


def test_swm_blocked_branch_remove_includes_legacy_labels() -> None:
    """SWM blocked overlay includes ALL_CLEARANCE_LABELS (legacy) in labels.remove."""
    from voyager.bots.clearance.constants import LEGACY_CLEARANCE_LABELS

    automation = {
        "enabled": True,
        "status": "blocked",
        "reason": "high-priority thread open",
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    result = apply_swm_overlay(_ready_evaluation(), automation)
    remove = result["labels"]["remove"]
    for label in LEGACY_CLEARANCE_LABELS:
        assert label in remove, f"Legacy label {label!r} must be in remove for migration"


def test_swm_pending_branch_remove_includes_legacy_labels() -> None:
    """SWM pending overlay includes legacy labels in labels.remove."""
    from voyager.bots.clearance.constants import LEGACY_CLEARANCE_LABELS

    automation = {
        "enabled": True,
        "status": "pending",
        "reason": "waiting on SWM tick",
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    result = apply_swm_overlay(_ready_evaluation(), automation)
    remove = result["labels"]["remove"]
    for label in LEGACY_CLEARANCE_LABELS:
        assert label in remove, f"Legacy label {label!r} must be in remove for migration"


def test_swm_error_branch_remove_includes_legacy_labels() -> None:
    """SWM error overlay includes legacy labels in labels.remove."""
    from voyager.bots.clearance.constants import LEGACY_CLEARANCE_LABELS

    automation = {
        "enabled": True,
        "status": "error",
        "error": "fetch timeout",
        "sync_actions": [],
        "sync_actions_count": 0,
    }
    result = apply_swm_overlay(_ready_evaluation(), automation)
    remove = result["labels"]["remove"]
    for label in LEGACY_CLEARANCE_LABELS:
        assert label in remove, f"Legacy label {label!r} must be in remove for migration"


def test_swm_ready_no_env_configured_gives_clearance_ready_not_for_approval() -> None:
    """When env is empty, SWM ready overlay falls back to clearance_ready (legacy behavior)."""
    automation = {
        "enabled": True,
        "status": "ready",
        "reason": "all Codex review threads RESOLVED",
        "sync_actions": [{"mutation": "resolve_review_thread", "threadId": "t-1"}],
        "sync_actions_count": 1,
        "unresolved_codex_thread_count": 0,
    }
    ev = _blocked_evaluation()
    result = apply_swm_overlay(ev, automation)
    assert result["status"] == "clearance_ready"
    assert result["labels"]["add"] == [CLEARANCE_READY_LABEL]


def test_swm_ready_with_case_mismatched_configured_approver_clears_to_ready(
    monkeypatch,
) -> None:
    """SWM ready + env 'Frankyxhl' + approval by 'frankyxhl' → clearance_ready (case-insensitive)."""
    from voyager.bots.clearance.constants import (
        CLEARANCE_READY_FOR_APPROVAL_LABEL,
        reset_review_request_users_cache,
    )

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "Frankyxhl")
    reset_review_request_users_cache()

    automation = {
        "enabled": True,
        "status": "ready",
        "reason": "all Codex review threads RESOLVED",
        "sync_actions": [{"mutation": "resolve_review_thread", "threadId": "t-1"}],
        "sync_actions_count": 1,
        "unresolved_codex_thread_count": 0,
    }
    # Build a blocked evaluation that has 'frankyxhl' in current_approvals (lowercase)
    ev = _blocked_evaluation()
    ev["review_state"]["current_approvals"] = ["frankyxhl"]

    result = apply_swm_overlay(ev, automation)
    # Case-insensitive match: env 'Frankyxhl' matches approval by 'frankyxhl'
    # → configured approver HAS approved → clearance_ready, NOT clearance_ready_for_approval
    assert result["status"] == "clearance_ready", (
        f"Expected clearance_ready but got {result['status']!r}; "
        f"labels.add={result['labels']['add']!r}"
    )
    assert CLEARANCE_READY_LABEL in result["labels"]["add"]
    assert CLEARANCE_READY_FOR_APPROVAL_LABEL not in result["labels"]["add"]


# ---------------------------------------------------------------------------
# Bootstrap routing: SWM ready + env set + no current approvals →
# clearance_ready_for_approval (not clearance_ready, not clearance_pending).
#
# This test is RED until the overlay's _has_non_thread_reason guard is fixed:
# "No approval on the current PR head." must NOT be treated as a non-thread
# blocker when env is set; instead it should be lifted to ready_for_approval.
# ---------------------------------------------------------------------------


def test_swm_ready_with_env_set_and_no_approvals_routes_to_ready_for_approval(
    monkeypatch,
) -> None:
    """SWM automation status=ready + env=frankyxhl + empty current_approvals → clearance_ready_for_approval.

    The base evaluation is clearance_pending (because no approval exists yet and
    the evaluator's elif-reasons branch fires). The overlay receives this pending
    evaluation with automation status=ready. With env set and no configured-user
    approval present, the desired output is clearance_ready_for_approval.

    Currently FAILS because _has_non_thread_reason returns True for the
    "No approval on the current PR head." reason, causing the overlay to
    pass-through the base clearance_pending evaluation unchanged.
    """
    from voyager.bots.clearance.constants import (
        CLEARANCE_READY_FOR_APPROVAL_LABEL,
        reset_review_request_users_cache,
    )

    monkeypatch.setenv("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "frankyxhl")
    reset_review_request_users_cache()

    automation = {
        "enabled": True,
        "status": "ready",
        "reason": "all Codex review threads RESOLVED",
        "sync_actions": [{"mutation": "resolve_review_thread", "threadId": "t-1"}],
        "sync_actions_count": 1,
        "unresolved_codex_thread_count": 0,
    }
    # Build a base evaluation that looks like what evaluate_clearance_snapshot
    # produces when env is set but no current-head approval exists yet:
    # status=clearance_pending, reason includes "No approval on the current PR head."
    ev = _blocked_evaluation()
    ev["status"] = "clearance_pending"
    ev["conclusion"] = "neutral"
    ev["review_state"]["unresolved_thread_count"] = 0
    ev["review_state"]["current_approvals"] = []
    ev["confidence"]["reasons"] = ["No approval on the current PR head."]
    ev["labels"] = {
        "add": ["clearance-1-pending"],
        "remove": [
            "clearance-2-blocked",
            "clearance-3-ready-for-approval",
            "clearance-4-ready-for-merge",
        ],
    }

    result = apply_swm_overlay(ev, automation)

    assert result["status"] == "clearance_ready_for_approval", (
        f"Expected clearance_ready_for_approval but got {result['status']!r}; "
        f"labels.add={result['labels']['add']!r}"
    )
    assert result["labels"]["add"] == [CLEARANCE_READY_FOR_APPROVAL_LABEL], (
        f"Expected [{CLEARANCE_READY_FOR_APPROVAL_LABEL!r}] but got {result['labels']['add']!r}"
    )
