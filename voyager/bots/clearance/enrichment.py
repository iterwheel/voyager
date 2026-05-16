"""Clearance bot — async route enrichment and comment building."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from voyager.core.github_app import GitHubAppClient
from voyager.core.writeback import dry_run_enabled

from .constants import (
    CHECKBOX_ACTION_LABELS,
    CLEARANCE_AGENT_SLUG,
    CLEARANCE_CLASSIFIER_VERSION,
    CLEARANCE_COMMENT_MARKER,
    configured_review_request_users,
)
from .evaluation import ClearanceEvaluation, evaluate_clearance_snapshot
from .overlay import apply_swm_overlay

_log = logging.getLogger(__name__)


def format_user_list(users: list[str]) -> str:
    return ", ".join(f"@{user}" for user in users) if users else "none"


def format_review_request_status(review_request: dict[str, Any]) -> str:
    if not review_request.get("enabled"):
        return str(review_request.get("reason") or "disabled")
    parts: list[str] = []
    if review_request.get("requested"):
        parts.append(f"requested {format_user_list(review_request['requested'])}")
    if review_request.get("planned"):
        parts.append(f"planned {format_user_list(review_request['planned'])}")
    if review_request.get("already_requested"):
        parts.append(f"already requested {format_user_list(review_request['already_requested'])}")
    if review_request.get("skipped_author"):
        parts.append(f"skipped PR author {format_user_list(review_request['skipped_author'])}")
    reason = str(review_request.get("reason") or "")
    if review_request.get("applied") is False and reason and reason not in ("dry-run", "disabled"):
        parts.append(f"reason: {reason}")
    if parts:
        return "; ".join(parts)
    return reason or "not applied"


def one_line(value: Any, *, limit: int = 260) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


async def _dispatch_review_request(
    client: GitHubAppClient,
    *,
    repository: str,
    pull_request: dict[str, Any],
    configured_users: tuple[str, ...],
) -> dict[str, Any]:
    if not configured_users:
        return {"enabled": False, "reason": "no configured reviewers"}

    pr_author = (pull_request.get("user") or {}).get("login", "")
    pr_number = pull_request["number"]
    existing_requested_lc = {
        ((r or {}).get("login") or "").lower()
        for r in (pull_request.get("requested_reviewers") or [])
    }

    to_request: list[str] = []
    already: list[str] = []
    skipped_author: list[str] = []

    for user in configured_users:
        if user.lower() == pr_author.lower():
            skipped_author.append(user)
        elif user.lower() in existing_requested_lc:
            already.append(user)
        else:
            to_request.append(user)

    if dry_run_enabled():
        result: dict[str, Any] = {
            "enabled": True,
            "applied": False,
            "planned": to_request,
            "already_requested": already,
            "skipped_author": skipped_author,
            "reason": "dry-run",
        }
        _log.info(
            "clearance.review_request: applied=%s planned=%s already=%s skipped_author=%s reason=%s",
            result["applied"],
            result["planned"],
            result["already_requested"],
            result["skipped_author"],
            result["reason"],
        )
        return result

    if to_request:
        try:
            await client.request_pull_request_reviewers(
                CLEARANCE_AGENT_SLUG, repository, pr_number, to_request
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else None
            is_already_requested = False
            if status == 422 and exc.response is not None:
                try:
                    errors = (exc.response.json() or {}).get("errors") or []
                    is_already_requested = any(
                        "already been requested" in (err.get("message") or "").lower()
                        for err in errors
                    )
                except (ValueError, AttributeError):
                    is_already_requested = False
            if is_already_requested:
                result = {
                    "enabled": True,
                    "applied": False,
                    "requested": [],
                    "already_requested": already + to_request,
                    "skipped_author": skipped_author,
                    "reason": "already requested (422 race)",
                }
                _log.info(
                    "clearance.review_request: applied=%s requested=%s already=%s"
                    " skipped_author=%s reason=%s",
                    result["applied"],
                    result.get("requested") or [],
                    result["already_requested"],
                    result["skipped_author"],
                    result["reason"],
                )
                return result
            _log.warning(
                "clearance.review_request: dispatch failed (class=%s status=%s)",
                exc.__class__.__name__,
                status,
            )
            result = {
                "enabled": True,
                "applied": False,
                "planned": to_request,
                "already_requested": already,
                "skipped_author": skipped_author,
                "reason": f"API request failed ({exc.__class__.__name__})",
            }
            _log.info(
                "clearance.review_request: applied=%s planned=%s already=%s"
                " skipped_author=%s reason=%s",
                result["applied"],
                result.get("planned") or [],
                result["already_requested"],
                result["skipped_author"],
                result["reason"],
            )
            return result
        except (httpx.HTTPError, RuntimeError) as exc:
            _log.warning(
                "clearance.review_request: dispatch failed (class=%s status=%s)",
                exc.__class__.__name__,
                None,
            )
            result = {
                "enabled": True,
                "applied": False,
                "planned": to_request,
                "already_requested": already,
                "skipped_author": skipped_author,
                "reason": f"API request failed ({exc.__class__.__name__})",
            }
            _log.info(
                "clearance.review_request: applied=%s planned=%s already=%s"
                " skipped_author=%s reason=%s",
                result["applied"],
                result.get("planned") or [],
                result["already_requested"],
                result["skipped_author"],
                result["reason"],
            )
            return result

    result = {
        "enabled": True,
        "applied": bool(to_request),
        "requested": to_request,
        "already_requested": already,
        "skipped_author": skipped_author,
    }
    _log.info(
        "clearance.review_request: applied=%s requested=%s already=%s skipped_author=%s reason=%s",
        result["applied"],
        result.get("requested") or [],
        result["already_requested"],
        result["skipped_author"],
        "",
    )
    return result


def build_clearance_comment(
    evaluation: ClearanceEvaluation,
    automation: dict[str, Any] | None = None,
    *,
    review_request: dict[str, Any] | None = None,
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
    if review_request:
        lines.extend(["", f"Review request: {format_review_request_status(review_request)}"])
    if automation and automation.get("enabled"):
        approve = automation.get("approve") or {}
        tick = automation.get("tick") or {}
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
                "Clearance automation:",
                f"- Status: {automation.get('status', 'unknown')}",
                f"- Thread sync actions: {automation.get('sync_actions_count', 0)}",
                f"- Checkbox tick: {tick_status}",
                f"- Approval: {approval_status}",
            ]
        )
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
    pull_request = await client.pull_request(CLEARANCE_AGENT_SLUG, repository, pr_number)
    snapshot = {
        "pull_request": pull_request,
        "reviews": await client.pull_request_reviews(CLEARANCE_AGENT_SLUG, repository, pr_number),
        "review_threads": await client.pull_request_review_threads(
            CLEARANCE_AGENT_SLUG, repository, pr_number
        ),
    }
    evaluation = evaluate_clearance_snapshot(snapshot)
    evaluation = apply_swm_overlay(evaluation, automation)

    review_request: dict[str, Any] | None = None
    if evaluation["status"] == "clearance_ready_for_approval":
        review_request = await _dispatch_review_request(
            client,
            repository=repository,
            pull_request=pull_request,
            configured_users=configured_review_request_users(),
        )

    enriched = dict(route)
    enriched["validation"] = evaluation
    if automation is not None:
        enriched["automation"] = {"swm_clearance": automation}
    enriched["writeback"] = {
        "comment_marker": CLEARANCE_COMMENT_MARKER,
        "comment_mode": "append",
        "comment_body": build_clearance_comment(
            evaluation, automation, review_request=review_request
        ),
        "labels": evaluation["labels"],
        "reactions": evaluation["reactions"],
    }
    return enriched
