"""Clearance bot — SWM overlay application and Codex PR body signal extraction."""

from __future__ import annotations

from typing import Any, cast

from .classify import CodexBodySignal
from .constants import (
    ALL_CLEARANCE_LABELS,
    CLEARANCE_AGENT_SLUG,
    CLEARANCE_BLOCKED_LABEL,
    CLEARANCE_CODEX_REACTION_FOLLOW_UP_ACTION,
    CLEARANCE_CODEX_REACTION_FOLLOW_UP_EVENT,
    CLEARANCE_PENDING_LABEL,
    CLEARANCE_READY_FOR_APPROVAL_LABEL,
    CLEARANCE_READY_LABEL,
    configured_review_request_users,
)
from .evaluation import ClearanceEvaluation


def _has_non_thread_reason(reasons: list[Any]) -> bool:
    """Return true when evaluator reasons include blockers automation cannot clear."""
    return any("review thread(s) are unresolved." not in str(reason) for reason in reasons)


_PREEMPTING_REASON_PREFIXES = (
    "PR is still draft.",
    "PR is not open.",
    "Changes requested by:",
)


def _has_preempting_reason(reasons: list[Any]) -> bool:
    """True if reasons include a blocker the overlay must always preserve.

    Unlike `_has_non_thread_reason`, this excludes approval-state reasons
    ("No approval on the current PR head.", "Only stale approval(s)..."),
    because when env is configured those are precisely the ready_for_approval
    state's domain — the overlay must lift them, not preserve them.
    """
    return any(
        any(str(r).startswith(prefix) for prefix in _PREEMPTING_REASON_PREFIXES) for r in reasons
    )


def _visual_unresolved_allowance(automation: dict[str, Any]) -> int:
    if "visual_unresolved_thread_count" in automation:
        return int(automation.get("visual_unresolved_thread_count") or 0)
    return int(automation.get("sync_actions_count") or 0)


def apply_swm_overlay(
    evaluation: ClearanceEvaluation, automation: dict[str, Any] | None
) -> ClearanceEvaluation:
    if not automation or not automation.get("enabled"):
        return evaluation
    swm_status = automation.get("status")
    if swm_status not in {"blocked", "pending", "error", "ready", "ready_with_low_priority"}:
        return evaluation

    updated: dict[str, Any] = dict(evaluation)

    if swm_status in {"ready", "ready_with_low_priority"}:
        review_state = evaluation.get("review_state") or {}
        eval_confidence = evaluation.get("confidence") or {}
        reasons = eval_confidence.get("reasons") or []
        configured = configured_review_request_users()
        has_preempting_blockers = bool(
            review_state.get("blocking_reviewers") or _has_preempting_reason(reasons)
        )
        if has_preempting_blockers:
            return evaluation
        # When env is UNSET, apply the legacy guard: any non-thread reason
        # (including approval-state reasons) preserves the base evaluation.
        # When env is SET, approval-state reasons are the ready_for_approval
        # domain — the overlay must lift them, not preserve them.
        if not configured and (
            evaluation.get("status") == "clearance_pending" or _has_non_thread_reason(reasons)
        ):
            return evaluation
        # Preserve when the live evaluator sees more unresolved threads than
        # Clearance can account for. The allowance includes unresolved Codex
        # threads plus Stage 1.5 sync actions that may still be visible in a
        # dry-run or immediately before GitHub reflects resolveReviewThread.
        if "unresolved_codex_thread_count" in automation:
            unresolved_count = review_state.get("unresolved_thread_count", 0)
            unresolved_codex_count = int(automation.get("unresolved_codex_thread_count") or 0)
            visual_allowance = _visual_unresolved_allowance(automation)
            if unresolved_count > unresolved_codex_count + visual_allowance:
                return evaluation
        reason = automation.get("reason") or f"Clearance automation status is {swm_status}."
        # Configured-approver gate: if a human approver is required and hasn't approved yet,
        # produce ready_for_approval instead of ready.
        review_state = evaluation.get("review_state") or {}
        current_approvals = review_state.get("current_approvals") or []
        current_approvals_lc = {u.lower() for u in current_approvals}
        configured_approval_present = bool(configured) and any(
            user.lower() in current_approvals_lc for user in configured
        )
        if configured and not configured_approval_present:
            updated["status"] = "clearance_ready_for_approval"
            updated["conclusion"] = "neutral"
            updated["summary"] = "Clearance is ready for human approval."
            updated["labels"] = {
                "add": [CLEARANCE_READY_FOR_APPROVAL_LABEL],
                "remove": [
                    item
                    for item in ALL_CLEARANCE_LABELS
                    if item != CLEARANCE_READY_FOR_APPROVAL_LABEL
                ],
            }
            updated["reactions"] = {"add": ["eyes"], "remove": ["+1", "rocket"]}
            return cast(ClearanceEvaluation, updated)
        updated["status"] = "clearance_ready"
        updated["conclusion"] = "success"
        updated["summary"] = reason
        updated["labels"] = {
            "add": [CLEARANCE_READY_LABEL],
            "remove": [item for item in ALL_CLEARANCE_LABELS if item != CLEARANCE_READY_LABEL],
        }
        updated["reactions"] = {"add": ["+1"], "remove": ["eyes", "rocket"]}
        return cast(ClearanceEvaluation, updated)

    confidence = dict(updated.get("confidence") or {})
    reasons = list(confidence.get("reasons") or [])
    reason = (
        automation.get("reason")
        or automation.get("error")
        or f"Clearance automation status is {swm_status}."
    )
    reasons.append(f"Clearance automation engine: {reason}")
    confidence["reasons"] = reasons
    updated["confidence"] = confidence

    if swm_status in {"blocked", "error"}:
        label = CLEARANCE_BLOCKED_LABEL
        updated["status"] = "clearance_blocked"
        updated["conclusion"] = "failure"
        updated["summary"] = "Clearance is blocked by the automation engine."
    else:
        label = CLEARANCE_PENDING_LABEL
        updated["status"] = "clearance_pending"
        updated["conclusion"] = "neutral"
        updated["summary"] = "Clearance is waiting on the automation engine."

    updated["labels"] = {
        "add": [label],
        "remove": [item for item in ALL_CLEARANCE_LABELS if item != label],
    }
    updated["reactions"] = {"add": ["eyes"], "remove": ["+1", "rocket"]}
    return cast(ClearanceEvaluation, updated)


def clearance_swm_codex_pr_body_signal(route: dict[str, Any]) -> str | None:
    automation = (route.get("automation") or {}).get("swm_clearance") or {}
    poll = automation.get("poll") or {}
    signal = automation.get("codex_pr_body_signal") or poll.get("codex_pr_body_signal")
    return str(signal) if signal else None


def clearance_waiting_on_codex_pr_body_reaction(route: dict[str, Any]) -> bool:
    validation = route.get("validation") or {}
    return (
        route.get("agent") == CLEARANCE_AGENT_SLUG
        and validation.get("status") == "clearance_pending"
        and clearance_swm_codex_pr_body_signal(route) == CodexBodySignal.REVIEWING
    )


def should_schedule_codex_reaction_follow_up(event: str, route: dict[str, Any]) -> bool:
    return event == "check_suite" and clearance_waiting_on_codex_pr_body_reaction(route)


def build_codex_reaction_follow_up_route(route: dict[str, Any]) -> dict[str, Any]:
    validation = dict(route.get("validation") or {})
    validation["status"] = "clearance_pending"
    validation["conclusion"] = "neutral"
    validation["summary"] = "Clearance scheduled a Codex PR body reaction follow-up."

    follow_up = dict(route)
    follow_up["event"] = CLEARANCE_CODEX_REACTION_FOLLOW_UP_EVENT
    follow_up["action"] = CLEARANCE_CODEX_REACTION_FOLLOW_UP_ACTION
    follow_up["validation"] = validation
    follow_up["writeback"] = {"dynamic": "clearance_readiness"}
    follow_up.pop("automation", None)
    return follow_up
