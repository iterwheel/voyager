"""Clearance bot — SWM overlay application and Codex PR body signal extraction."""

from __future__ import annotations

from typing import Any

from .constants import (
    CLEARANCE_AGENT_SLUG,
    CLEARANCE_BLOCKED_LABEL,
    CLEARANCE_CODEX_REACTION_FOLLOW_UP_ACTION,
    CLEARANCE_CODEX_REACTION_FOLLOW_UP_EVENT,
    CLEARANCE_LABELS,
    CLEARANCE_PENDING_LABEL,
)


def apply_swm_overlay(
    evaluation: dict[str, Any], automation: dict[str, Any] | None
) -> dict[str, Any]:
    if not automation or not automation.get("enabled"):
        return evaluation
    swm_status = automation.get("status")
    if swm_status not in {"blocked", "pending", "error"}:
        return evaluation

    updated = dict(evaluation)
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
    return updated


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
        and clearance_swm_codex_pr_body_signal(route) == "reviewing"
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
