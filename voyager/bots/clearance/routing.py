"""Clearance bot — event routing and route construction."""

from __future__ import annotations

import logging
from typing import Any

from voyager.bots.assembly.actor import evaluate_actor_authorization

from .constants import (
    CHECK_SUITE_ACTIONS,
    CLEARANCE_AGENT_ID,
    CLEARANCE_AGENT_SLUG,
    CLEARANCE_BOT_LOGIN,
    CLEARANCE_CLASSIFIER_VERSION,
    CODEX_REVIEW_REACTION_CONTENTS,
    CODEX_REVIEW_RESULT_PREFIX,
    PULL_REQUEST_ACTIONS,
    PULL_REQUEST_REVIEW_ACTIONS,
    REACTION_ACTIONS,
    is_codex_login,
)

_log = logging.getLogger(__name__)


def payload_actor_login(event: str, payload: dict[str, Any]) -> str | None:
    if event == "pull_request_review":
        review = payload.get("review") or {}
        user = review.get("user") or {}
        return user.get("login")
    if event == "pull_request_review_comment":
        comment = payload.get("comment") or {}
        user = comment.get("user") or {}
        return user.get("login")
    if event == "issue_comment":
        comment = payload.get("comment") or {}
        user = comment.get("user") or {}
        return user.get("login")
    if event == "reaction":
        reaction = payload.get("reaction") or {}
        user = reaction.get("user") or payload.get("sender") or {}
        if user.get("login"):
            return user.get("login")
    sender = payload.get("sender") or {}
    return sender.get("login")


def is_codex_review_result_comment(payload: dict[str, Any]) -> bool:
    comment = payload.get("comment") or {}
    user = comment.get("user") or {}
    body = str(comment.get("body") or "").lstrip()
    return is_codex_login(user.get("login")) and body.startswith(CODEX_REVIEW_RESULT_PREFIX)


def is_codex_pr_body_reaction(payload: dict[str, Any]) -> bool:
    issue = payload.get("issue") or {}
    reaction = payload.get("reaction") or {}
    user = reaction.get("user") or payload.get("sender") or {}
    return (
        bool(issue.get("pull_request"))
        and is_codex_login(user.get("login"))
        and reaction.get("content") in CODEX_REVIEW_REACTION_CONTENTS
    )


def clearance_command_actor_authorized(payload: dict[str, Any], cfg: Any | None = None) -> bool:
    """Issue #253: ``/clearance`` is a privileged command.

    It re-runs the readiness pipeline and can apply ready-for-merge labels,
    so it gets the same actor authorization gate as Assembly and
    ``/review-fix`` (VOY-1818): allow-list or trusted author association;
    bots are always refused. Returns True when the comment author may
    invoke the command.
    """
    actor = evaluate_actor_authorization(payload, cfg)
    if not actor.ok:
        _log.warning(
            "clearance_command_denied: actor=%r association=%r sender=%r reason=%s",
            actor.actor_login,
            actor.actor_association,
            actor.actor_sender_login,
            actor.reason,
        )
    return actor.ok


def should_run_clearance(
    event: str,
    payload: dict[str, Any],
    *,
    cfg: Any | None = None,
) -> bool:
    if payload_actor_login(event, payload) == CLEARANCE_BOT_LOGIN:
        return False

    action = payload.get("action")
    if event == "pull_request" and action in PULL_REQUEST_ACTIONS:
        return True
    if event == "pull_request_review" and action in PULL_REQUEST_REVIEW_ACTIONS:
        return True
    if event == "pull_request_review_comment" and action == "created":
        return (payload.get("comment") or {}).get("in_reply_to_id") is not None
    if event == "issue_comment" and action == "created":
        issue = payload.get("issue") or {}
        if not issue.get("pull_request"):
            return False
        # Codex review-result comments are a system signal, not a command —
        # they stay ungated.
        if is_codex_review_result_comment(payload):
            return True
        body = str((payload.get("comment") or {}).get("body") or "")
        if "/clearance" not in body.lower():
            return False
        # Issue #253: the explicit /clearance command requires an authorized
        # actor (same gate as Assembly / VOY-1818).
        return clearance_command_actor_authorized(payload, cfg)
    if event == "check_suite" and action in CHECK_SUITE_ACTIONS:
        check_suite = payload.get("check_suite") or {}
        return bool(check_suite.get("pull_requests"))
    if event == "reaction" and action in REACTION_ACTIONS:
        return is_codex_pr_body_reaction(payload)
    return False


def pr_html_url_from_payload(payload: dict[str, Any], pr_number: Any) -> str | None:
    repository = payload.get("repository") or {}
    repo_html_url = repository.get("html_url")
    if not repo_html_url or not pr_number:
        return None
    return f"{repo_html_url}/pull/{pr_number}"


def check_targets_from_payload(
    payload: dict[str, Any], pull_requests: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    seen_numbers: set[Any] = set()
    for item in pull_requests:
        target = dict(item or {})
        pr_number = target.get("number")
        if not pr_number or pr_number in seen_numbers:
            continue
        seen_numbers.add(pr_number)
        target.setdefault("html_url", pr_html_url_from_payload(payload, pr_number))
        targets.append(target)
    return targets


def clearance_targets_from_payload(event: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    if event in {"pull_request", "pull_request_review", "pull_request_review_comment"}:
        pull_request = dict(payload.get("pull_request") or {})
        if not pull_request:
            return []
        return [pull_request]

    if event == "reaction":
        issue = dict(payload.get("issue") or {})
        if issue.get("pull_request"):
            return [issue]
        pull_request = dict(payload.get("pull_request") or {})
        if pull_request:
            return [pull_request]
        return []

    if event == "check_suite":
        check_suite = payload.get("check_suite") or {}
        return check_targets_from_payload(payload, check_suite.get("pull_requests") or [])

    issue = dict(payload.get("issue") or {})
    if not issue or not issue.get("pull_request"):
        return []
    return [issue]


def build_clearance_route(
    event: str, payload: dict[str, Any], target: dict[str, Any]
) -> dict[str, Any]:
    pr_number = target.get("number")
    base_ref = ((target.get("base") or {}).get("ref")) or None
    webhook_head_sha: str | None = (target.get("head") or {}).get("sha") or None
    return {
        "agent": CLEARANCE_AGENT_SLUG,
        "agent_id": CLEARANCE_AGENT_ID,
        "kind": "clearance_readiness",
        "event": event,
        "action": payload.get("action"),
        "validation": {
            "status": "clearance_pending",
            "conclusion": "neutral",
            "issue_number": pr_number,
            "pr_number": pr_number,
            "base_ref": base_ref,
            "pr_url": target.get("html_url"),
            "target_kind": "pull_request",
            "classifier": CLEARANCE_CLASSIFIER_VERSION,
            "summary": "Clearance scheduled a current PR review-readiness evaluation.",
            "webhook_head_sha": webhook_head_sha,
        },
        "writeback": {
            "dynamic": "clearance_readiness",
        },
    }


def route_clearance_event(
    event: str,
    payload: dict[str, Any],
    *,
    cfg: Any | None = None,
) -> list[dict[str, Any]]:
    if not should_run_clearance(event, payload, cfg=cfg):
        return []

    targets = clearance_targets_from_payload(event, payload)
    if not targets:
        return []

    return [build_clearance_route(event, payload, target) for target in targets]
