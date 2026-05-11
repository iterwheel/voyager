"""Stack bot — issue classification and routing."""

from __future__ import annotations

from typing import Any

from .classifier import classify_stack_target
from .comment import build_stack_comment
from .constants import (
    STACK_AGENT_ID,
    STACK_AGENT_SLUG,
    STACK_COMMENT_MARKER,
)

__all__ = [
    "STACK_AGENT_SLUG",
    "STACK_COMMENT_MARKER",
    "build_stack_comment",
    "classify_stack_target",
    "route_stack_event",
]


def should_run_stack(event: str, payload: dict[str, Any]) -> bool:
    action = payload.get("action")
    if event == "issues" and action in {"opened", "edited", "reopened"}:
        return True
    if event == "issue_comment" and action == "created":
        # NOTE: "/stack" substring match also fires on "/stacktrace" — this
        # matches openclaw source behavior and is intentional for now.
        body = str((payload.get("comment") or {}).get("body") or "")
        return "/stack" in body.lower()
    return False


def stack_target_from_payload(_event: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    issue = dict(payload.get("issue") or {})
    if not issue:
        return None
    if issue.get("pull_request"):
        return None
    issue["target_kind"] = "issue"
    return issue


def route_stack_event(event: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not should_run_stack(event, payload):
        return []

    target = stack_target_from_payload(event, payload)
    if not target:
        return []

    classification = classify_stack_target(target)
    comment_body = build_stack_comment(classification)
    return [
        {
            "agent": STACK_AGENT_SLUG,
            "agent_id": STACK_AGENT_ID,
            "kind": "stack_classification",
            "event": event,
            "action": payload.get("action"),
            "validation": classification,
            "writeback": {
                "comment_marker": STACK_COMMENT_MARKER,
                "comment_body": comment_body,
                "labels": classification["labels"],
                "reactions": classification["reactions"],
            },
        }
    ]
