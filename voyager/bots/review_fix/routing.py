"""Webhook routing for the governed PR review-fix bot."""

from __future__ import annotations

from typing import Any

from voyager.bots.assembly.actor import evaluate_actor_authorization
from voyager.bots.assembly.constants import REFUSAL_UNAUTHORIZED_ACTOR

from .commands import ReviewFixCommand, parse_review_fix_command
from .constants import (
    REVIEW_FIX_AGENT_ID,
    REVIEW_FIX_AGENT_SLUG,
    REVIEW_FIX_COMMENT_MARKER,
    REVIEW_FIX_DYNAMIC,
    REVIEW_FIX_KIND,
)


def should_run_review_fix(event: str, payload: dict[str, Any]) -> bool:
    """Return True when a PR issue comment explicitly asks review-fix to run."""
    if event != "issue_comment":
        return False
    if (payload.get("action") or "") != "created":
        return False
    if not (payload.get("issue") or {}).get("pull_request"):
        return False
    body = str((payload.get("comment") or {}).get("body") or "")
    return parse_review_fix_command(body) is not None


def _command_or_none(payload: dict[str, Any]) -> ReviewFixCommand | None:
    body = str((payload.get("comment") or {}).get("body") or "")
    return parse_review_fix_command(body)


def route_review_fix_event(
    event: str,
    payload: dict[str, Any],
    *,
    cfg: Any | None = None,
) -> list[dict[str, Any]]:
    """Return review-fix route(s) for an incoming webhook payload."""
    if not should_run_review_fix(event, payload):
        return []

    command = _command_or_none(payload)
    if command is None:
        return []

    issue = dict(payload.get("issue") or {})
    actor = evaluate_actor_authorization(payload, cfg)
    command_flags = {"dry_run": command.dry_run}
    validation: dict[str, Any] = {
        "status": "review_fix_ready" if actor.ok else "review_fix_refused",
        "conclusion": "success" if actor.ok else "neutral",
        "issue_number": issue.get("number"),
        "pr_number": issue.get("number"),
        "issue_url": issue.get("html_url"),
        "command": command.command,
        "command_flags": command_flags,
        "actor": {
            "login": actor.actor_login,
            "association": actor.actor_association,
            "type": actor.actor_type,
            "matched_signal": actor.matched_signal,
        },
    }

    refusal: dict[str, Any] | None = None
    if not actor.ok:
        refusal = {
            "reason": REFUSAL_UNAUTHORIZED_ACTOR,
            "actor_login": actor.actor_login,
            "actor_association": actor.actor_association,
        }
        validation["refusal"] = refusal

    return [
        {
            "agent": REVIEW_FIX_AGENT_SLUG,
            "agent_id": REVIEW_FIX_AGENT_ID,
            "kind": REVIEW_FIX_KIND,
            "event": event,
            "action": payload.get("action"),
            "validation": validation,
            "writeback": {
                "dynamic": REVIEW_FIX_DYNAMIC,
                "command": command.command,
                "command_flags": command_flags,
                "comment_marker": REVIEW_FIX_COMMENT_MARKER,
                "refusal": refusal,
            },
        }
    ]
