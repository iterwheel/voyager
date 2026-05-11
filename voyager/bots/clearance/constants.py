"""Clearance bot constants — labels, slugs, and action sets."""

from __future__ import annotations

CLEARANCE_AGENT_SLUG = "iterwheel-clearance"
CLEARANCE_BOT_LOGIN = f"{CLEARANCE_AGENT_SLUG}[bot]"
CLEARANCE_AGENT_ID = "github-clearance-agent"
CLEARANCE_COMMENT_MARKER = "<!-- iterwheel:clearance-readiness -->"
CLEARANCE_CLASSIFIER_VERSION = "clearance-v1"

CODEX_REVIEW_BOT_LOGINS = {
    "chatgpt-codex-connector",
    "chatgpt-codex-connector[bot]",
}
CODEX_REVIEW_RESULT_PREFIX = "Codex Review:"
CODEX_REVIEW_REACTION_CONTENTS = {"+1", "eyes"}

CLEARANCE_READY_LABEL = "clearance-ready"
CLEARANCE_PENDING_LABEL = "clearance-pending"
CLEARANCE_BLOCKED_LABEL = "clearance-blocked"
CLEARANCE_LABELS = (
    CLEARANCE_READY_LABEL,
    CLEARANCE_PENDING_LABEL,
    CLEARANCE_BLOCKED_LABEL,
)
CHECKBOX_ACTION_LABELS = {
    "already_checked": "already checked",
    "flipped": "checked by Clearance",
    "left_open": "left open",
}

PULL_REQUEST_ACTIONS = {
    "opened",
    "edited",
    "reopened",
    "ready_for_review",
    "converted_to_draft",
    "synchronize",
}
PULL_REQUEST_REVIEW_ACTIONS = {"submitted", "edited", "dismissed"}
CHECK_SUITE_ACTIONS = {"completed"}
REACTION_ACTIONS = {"created", "deleted"}
CLEARANCE_CODEX_REACTION_FOLLOW_UP_EVENT = "clearance_follow_up"
CLEARANCE_CODEX_REACTION_FOLLOW_UP_ACTION = "codex_pr_body_reaction"
