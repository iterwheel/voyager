"""Clearance bot — async route enrichment and comment building."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from voyager.core.github_app import GitHubAppClient
from voyager.core.writeback import dry_run_enabled, format_writeback_failure_warning

from .constants import (
    CHECKBOX_ACTION_LABELS,
    CLEARANCE_AGENT_SLUG,
    CLEARANCE_BLOCKED_LABEL,
    CLEARANCE_CLASSIFIER_VERSION,
    CLEARANCE_COMMENT_MARKER,
    CLEARANCE_PENDING_LABEL,
    CLEARANCE_READY_FOR_APPROVAL_LABEL,
    CLEARANCE_READY_LABEL,
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


_STAGE_BY_STATUS: dict[str, tuple[int, str, str]] = {
    "clearance_pending": (1, "Pending", CLEARANCE_PENDING_LABEL),
    "clearance_blocked": (2, "Blocked", CLEARANCE_BLOCKED_LABEL),
    "clearance_ready_for_approval": (
        3,
        "Ready for approval",
        CLEARANCE_READY_FOR_APPROVAL_LABEL,
    ),
    "clearance_ready": (4, "Ready for merge", CLEARANCE_READY_LABEL),
}


def _selected_label(evaluation: ClearanceEvaluation) -> str:
    labels = evaluation.get("labels") or {}
    add_labels = labels.get("add") or []
    if add_labels:
        return str(add_labels[0])
    return _stage_metadata(evaluation)[2]


def _stage_metadata(evaluation: ClearanceEvaluation) -> tuple[int, str, str]:
    status = evaluation["status"]
    return _STAGE_BY_STATUS.get(status, (0, status.replace("_", " ").title(), "unknown"))


def _review_request_users(review_request: dict[str, Any] | None) -> list[str]:
    if not review_request:
        return []
    users: list[str] = []
    seen: set[str] = set()
    for key in ("requested", "already_requested", "planned"):
        for user in review_request.get(key) or []:
            normalized = str(user).lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            users.append(str(user))
    return users


def _review_status_line(
    evaluation: ClearanceEvaluation, review_request: dict[str, Any] | None
) -> str:
    review_state = evaluation["review_state"]
    if review_request:
        return f"👤 Review: {format_review_request_status(review_request)}"
    if review_state["blocking_reviewers"]:
        return f"❌ Review: changes requested by {format_user_list(review_state['blocking_reviewers'])}"
    if review_state["current_approvals"]:
        return f"✅ Review: approved by {format_user_list(review_state['current_approvals'])}"
    configured = list(configured_review_request_users())
    if evaluation["status"] == "clearance_ready_for_approval" and configured:
        return f"⏳ Review: waiting for {format_user_list(configured)}"
    return "⏳ Review: no current approval"


def _threads_status_line(evaluation: ClearanceEvaluation) -> str:
    count = int(evaluation["review_state"]["unresolved_thread_count"])
    if count == 0:
        return "✅ Threads: 0 unresolved"
    return f"❌ Threads: {count} unresolved"


def _approval_status_line(
    evaluation: ClearanceEvaluation, review_request: dict[str, Any] | None
) -> str:
    review_state = evaluation["review_state"]
    current_approvals = review_state["current_approvals"]
    status = evaluation["status"]
    if status == "clearance_ready":
        return f"✅ Approval: current from {format_user_list(current_approvals)}"
    if status == "clearance_ready_for_approval":
        targets = (
            _review_request_users(review_request)
            if review_request is not None
            else list(configured_review_request_users())
        )
        if targets:
            return f"⏳ Approval: waiting for {format_user_list(targets)}"
        return "⏳ Approval: waiting for eligible reviewer"
    if review_state["blocking_reviewers"]:
        return f"❌ Approval: blocked by {format_user_list(review_state['blocking_reviewers'])}"
    if current_approvals:
        return f"✅ Approval: current from {format_user_list(current_approvals)}"
    return "⏳ Approval: waiting"


def _automation_status_line(automation: dict[str, Any] | None) -> str:
    if not automation or not automation.get("enabled"):
        return "⏳ Automation: not run"
    status = str(automation.get("status") or "unknown")
    icon = (
        "✅"
        if status in {"ready", "ready_with_low_priority"}
        else "❌"
        if status in {"blocked", "error"}
        else "⏳"
    )
    return (
        f"{icon} Automation: {status.replace('_', ' ')}; "
        f"thread sync actions: {automation.get('sync_actions_count', 0)}"
    )


def _author_only_deadlock_warning(review_request: dict[str, Any] | None) -> str | None:
    if not review_request or not review_request.get("author_only_deadlock"):
        return None
    skipped = review_request.get("skipped_author") or []
    who = format_user_list(skipped) if skipped else "the PR author"
    return (
        f"⚠️ Warning: {who} is the only configured reviewer, but PR authors cannot "
        "approve their own PRs. To unblock, add another configured reviewer, request "
        "an eligible non-author reviewer, or update "
        "`VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS`."
    )


def _next_action(evaluation: ClearanceEvaluation, review_request: dict[str, Any] | None) -> str:
    status = evaluation["status"]
    if status == "clearance_ready":
        return "Next: merge when the repository's normal merge gates are satisfied."
    if status == "clearance_ready_for_approval":
        if review_request and review_request.get("author_only_deadlock"):
            return (
                "Next: add a non-author configured reviewer or update "
                "`VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS`, then rerun Clearance."
            )
        targets = (
            _review_request_users(review_request)
            if review_request is not None
            else list(configured_review_request_users())
        )
        if not targets:
            return (
                "Next: request review from an eligible non-author reviewer. After approval, "
                "Clearance should move to Stage 4 - Ready for merge."
            )
        who = format_user_list(targets)
        return (
            f"Next: {who} review + approve. After approval, Clearance should move to "
            "Stage 4 - Ready for merge."
        )
    if status == "clearance_blocked":
        return "Next: resolve the blocking review state, then rerun Clearance."
    return "Next: wait for pending signals or rerun Clearance when the PR state changes."


def _last_updated_line(provenance: dict[str, Any] | None) -> str:
    provenance = provenance or {}
    updated_at = str(provenance.get("updated_at") or "")
    if not updated_at:
        updated_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    event = str(provenance.get("event") or "")
    action = str(provenance.get("action") or "")
    delivery = str(provenance.get("delivery_id") or "")

    parts = [updated_at]
    if event:
        via = event + (f".{action}" if action else "")
        parts.append(f"via {via}")
    if delivery:
        parts.append(f"delivery {delivery}")
    return " ".join(parts)


def _automation_details(automation: dict[str, Any] | None) -> str:
    if not automation or not automation.get("enabled"):
        return "not run; thread sync actions: 0"
    parts = [
        str(automation.get("status") or "unknown"),
        f"thread sync actions: {automation.get('sync_actions_count', 0)}",
    ]
    if "dry_run" in automation:
        parts.append(f"dry-run: {str(bool(automation.get('dry_run'))).lower()}")
    reason = automation.get("reason")
    if reason:
        parts.append(f"reason: {one_line(reason, limit=180)}")
    return "; ".join(parts)


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

    author_only_deadlock = bool(skipped_author) and not to_request and not already
    if author_only_deadlock:
        _log.warning(
            "clearance.review_request: author-only reviewer deadlock "
            "repository=%s pr_number=%s configured_users=%s pr_author=%s",
            repository,
            pr_number,
            list(configured_users),
            pr_author,
        )

    if dry_run_enabled():
        result: dict[str, Any] = {
            "enabled": True,
            "applied": False,
            "planned": to_request,
            "already_requested": already,
            "skipped_author": skipped_author,
            "author_only_deadlock": author_only_deadlock,
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
                    "author_only_deadlock": False,
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
                "author_only_deadlock": False,
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
                "author_only_deadlock": False,
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
        "author_only_deadlock": author_only_deadlock,
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
    provenance: dict[str, Any] | None = None,
) -> str:
    review_state = evaluation["review_state"]
    stage_number, stage_name, fallback_label = _stage_metadata(evaluation)
    selected_label = _selected_label(evaluation) or fallback_label
    reasons = evaluation["confidence"]["reasons"]
    warning = _author_only_deadlock_warning(review_request)

    # CHG-1813: Writeback failure warning line.
    writeback_warning = None
    failures = (automation or {}).get("writeback_failures") or []
    if failures:
        writeback_warning = format_writeback_failure_warning(failures[0])

    lines = [
        CLEARANCE_COMMENT_MARKER,
        "## Clearance",
        "",
        f"🚦 Stage: {stage_number} - {stage_name} (`{selected_label}`)",
        _review_status_line(evaluation, review_request),
        _threads_status_line(evaluation),
        _approval_status_line(evaluation, review_request),
        _automation_status_line(automation),
    ]
    if writeback_warning:
        lines.append(writeback_warning)
    if warning:
        lines.append(warning)
    lines.extend(
        [
            "",
            _next_action(evaluation, review_request),
            "",
            "<details>",
            "<summary>Details</summary>",
            "",
            f"- Classifier: {evaluation.get('classifier', CLEARANCE_CLASSIFIER_VERSION)}",
            f"- Status: {evaluation['status'].replace('_', '-')}",
            f"- Selected label: `{selected_label}`",
            f"- Current approvals: {format_user_list(review_state['current_approvals'])}",
            f"- Stale approvals: {format_user_list(review_state['stale_approvals'])}",
            f"- Changes requested: {format_user_list(review_state['blocking_reviewers'])}",
            f"- Unresolved threads: {review_state['unresolved_thread_count']}",
            f"- Automation: {_automation_details(automation)}",
            f"- Last updated: {_last_updated_line(provenance)}",
        ]
    )
    if evaluation.get("head_sha"):
        lines.append(f"- Head SHA: `{evaluation['head_sha']}`")
    if reasons:
        lines.append("- Reasons:")
        lines.extend(f"- {reason}" for reason in reasons)
    if review_request:
        lines.append(f"- Review request: {format_review_request_status(review_request)}")
    if automation and automation.get("enabled") and automation.get("tick"):
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
    if evaluation["confidence"].get("semantic_fix_note"):
        lines.append(f"- Note: {evaluation['confidence']['semantic_fix_note']}")
    lines.extend(["", "</details>"])
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
        "comment_mode": "upsert",
        "comment_body": build_clearance_comment(
            evaluation,
            automation,
            review_request=review_request,
            provenance={
                "event": route.get("event"),
                "action": route.get("action"),
                "delivery_id": route.get("delivery_id"),
            },
        ),
        "labels": evaluation["labels"],
        "reactions": evaluation["reactions"],
    }
    return enriched
