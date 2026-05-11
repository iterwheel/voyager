"""Clearance bot — async route enrichment and comment building."""

from __future__ import annotations

from typing import Any

from voyager.core.github_app import GitHubAppClient

from .constants import (
    CHECKBOX_ACTION_LABELS,
    CLEARANCE_AGENT_SLUG,
    CLEARANCE_CLASSIFIER_VERSION,
    CLEARANCE_COMMENT_MARKER,
)
from .evaluation import evaluate_clearance_snapshot
from .overlay import apply_swm_overlay


def format_user_list(users: list[str]) -> str:
    return ", ".join(f"@{user}" for user in users) if users else "none"


def format_review_request_status(review_request: dict[str, Any]) -> str:
    if not review_request.get("enabled"):
        return str(review_request.get("reason") or "disabled")
    if review_request.get("applied"):
        return f"requested {format_user_list(review_request.get('requested') or [])}"
    if review_request.get("planned"):
        return f"planned {format_user_list(review_request.get('planned') or [])}"
    already = review_request.get("already_requested") or []
    skipped = review_request.get("skipped_author") or []
    parts = []
    if already:
        parts.append(f"already requested {format_user_list(already)}")
    if skipped:
        parts.append(f"skipped PR author {format_user_list(skipped)}")
    if parts:
        return "; ".join(parts)
    return str(review_request.get("reason") or "not applied")


def one_line(value: Any, *, limit: int = 260) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def build_clearance_comment(
    evaluation: dict[str, Any], automation: dict[str, Any] | None = None
) -> str:
    review_state = evaluation["review_state"]
    lines = [
        CLEARANCE_COMMENT_MARKER,
        "Clearance review readiness",
        "",
        f"Classifier: {evaluation.get('classifier', CLEARANCE_CLASSIFIER_VERSION)}",
        "",
        f"Status: {evaluation['status'].replace('_', '-')}",
        "",
        evaluation["summary"],
        "",
        "Signals:",
        f"- Current approvals: {format_user_list(review_state['current_approvals'])}",
        f"- Stale approvals: {format_user_list(review_state['stale_approvals'])}",
        f"- Changes requested: {format_user_list(review_state['blocking_reviewers'])}",
        f"- Unresolved review threads: {review_state['unresolved_thread_count']}",
    ]
    reasons = evaluation["confidence"]["reasons"]
    if reasons:
        lines.extend(["", "Reasons:"])
        lines.extend(f"- {reason}" for reason in reasons)
    if automation and automation.get("enabled"):
        approve = automation.get("approve") or {}
        tick = automation.get("tick") or {}
        review_request = automation.get("review_request") or {}
        approval_status = (
            "applied"
            if approve.get("applied")
            else "already approved"
            if approve.get("already_approved")
            else "not applied"
        )
        tick_status = (
            f"applied ({tick.get('flipped', 0)} flipped)" if tick.get("applied") else "not applied"
        )
        lines.extend(
            [
                "",
                "SWM automation:",
                f"- Status: {automation.get('status', 'unknown')}",
                f"- Thread sync actions: {automation.get('sync_actions_count', 0)}",
                f"- Checkbox tick: {tick_status}",
                f"- Approval: {approval_status}",
            ]
        )
        if review_request:
            lines.append(f"- Review request: {format_review_request_status(review_request)}")
        if tick.get("reason"):
            lines.append(f"- Checkbox reason: {tick['reason']}")
        checkbox_items = tick.get("items") or []
        if checkbox_items:
            lines.append("- Checkbox items:")
            for item in checkbox_items:
                action = CHECKBOX_ACTION_LABELS.get(
                    str(item.get("action") or ""), str(item.get("action") or "unknown")
                )
                rule = item.get("rule") or "manual"
                satisfied = "yes" if item.get("satisfied") else "no"
                line = item.get("line") or "?"
                text = one_line(item.get("text"), limit=220)
                evidence = one_line(item.get("evidence"), limit=220)
                lines.append(
                    f"  - L{line} `{action}` [{rule}; satisfied={satisfied}]: {text}"
                    + (f" — {evidence}" if evidence else "")
                )
        if approve.get("reason"):
            lines.append(f"- Approval reason: {approve['reason']}")
    lines.extend(["", evaluation["confidence"]["semantic_fix_note"]])
    return "\n".join(lines).strip()


async def enrich_clearance_route(
    client: GitHubAppClient,
    route: dict[str, Any],
    *,
    repository: str,
    automation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pr_number = int(route["validation"]["pr_number"])
    snapshot = {
        "pull_request": await client.pull_request(CLEARANCE_AGENT_SLUG, repository, pr_number),
        "reviews": await client.pull_request_reviews(CLEARANCE_AGENT_SLUG, repository, pr_number),
        "review_threads": await client.pull_request_review_threads(
            CLEARANCE_AGENT_SLUG, repository, pr_number
        ),
    }
    evaluation = evaluate_clearance_snapshot(snapshot)
    evaluation = apply_swm_overlay(evaluation, automation)
    enriched = dict(route)
    enriched["validation"] = evaluation
    if automation is not None:
        enriched["automation"] = {"swm_clearance": automation}
    enriched["writeback"] = {
        "comment_marker": CLEARANCE_COMMENT_MARKER,
        "comment_mode": "append",
        "comment_body": build_clearance_comment(evaluation, automation),
        "labels": evaluation["labels"],
        "reactions": evaluation["reactions"],
    }
    return enriched
