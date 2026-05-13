"""Clearance bot — SWM overlay application and Codex PR body signal extraction."""

from __future__ import annotations

from typing import Any, cast

from .classify import CodexBodySignal
from .constants import (
    CLEARANCE_AGENT_SLUG,
    CLEARANCE_BLOCKED_LABEL,
    CLEARANCE_CODEX_REACTION_FOLLOW_UP_ACTION,
    CLEARANCE_CODEX_REACTION_FOLLOW_UP_EVENT,
    CLEARANCE_LABELS,
    CLEARANCE_PENDING_LABEL,
    CLEARANCE_READY_LABEL,
)
from .evaluation import ClearanceEvaluation


def apply_swm_overlay(
    evaluation: ClearanceEvaluation, automation: dict[str, Any] | None
) -> ClearanceEvaluation:
    if not automation or not automation.get("enabled"):
        return evaluation
    swm_status = automation.get("status")
    if swm_status not in {"blocked", "pending", "error", "ready_with_low_priority"}:
        return evaluation

    updated: dict[str, Any] = dict(evaluation)

    if swm_status == "ready_with_low_priority":
        review_state = evaluation.get("review_state") or {}
        eval_confidence = evaluation.get("confidence") or {}
        reasons = eval_confidence.get("reasons") or []
        unresolved_thread_count = review_state.get("unresolved_thread_count", 0)
        has_non_thread_blockers = bool(
            review_state.get("blocking_reviewers")
            or (evaluation.get("status") == "clearance_pending")
            or (unresolved_thread_count > 0 and len(reasons) > 1)
        )
        if has_non_thread_blockers:
            return evaluation
        # Preserve when the live evaluator sees more unresolved threads than the
        # automation engine counted as unresolved Codex threads — the gap is
        # non-Codex (human reviewer) threads that β must NOT clear.
        # Only applies when unresolved_codex_thread_count is present; absent key
        # means an old automation dict that predates this field — skip the check.
        if "unresolved_codex_thread_count" in automation:
            unresolved_count = review_state.get("unresolved_thread_count", 0)
            if unresolved_count > automation["unresolved_codex_thread_count"]:
                return evaluation
        reason = automation.get("reason") or f"Clearance automation status is {swm_status}."
        updated["status"] = "clearance_ready"
        updated["conclusion"] = "success"
        updated["summary"] = reason
        updated["labels"] = {
            "add": [CLEARANCE_READY_LABEL],
            "remove": [item for item in CLEARANCE_LABELS if item != CLEARANCE_READY_LABEL],
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
        "remove": [item for item in CLEARANCE_LABELS if item != label],
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
