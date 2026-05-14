"""Clearance bot constants — labels, slugs, and action sets.

Also home to the shared Codex-identity helpers (``is_codex_login`` and the
``VOYAGER_TEST_BOT_LOGINS`` env-var bypass) so both the webhook routing layer
(``routing.py``) and the thread-classification layer (``classify.py``) resolve
identity through one source. The four convergent reviewers of PR #14 r1
flagged a P1 when the bypass lived only in ``classify.py`` — test-bot webhooks
were dropped at the routing-layer ingress before classify ever ran.
"""

from __future__ import annotations

import functools
import logging
import os

CLEARANCE_AGENT_SLUG = "iterwheel-clearance"
CLEARANCE_BOT_LOGIN = f"{CLEARANCE_AGENT_SLUG}[bot]"
CLEARANCE_AGENT_ID = "github-clearance-agent"
CLEARANCE_COMMENT_MARKER = "<!-- iterwheel:clearance-readiness -->"
CLEARANCE_CLASSIFIER_VERSION = "clearance-v1"

_CODEX_BOT_LOGIN = "chatgpt-codex-connector"
_CODEX_BOT_LOGIN_REST = "chatgpt-codex-connector[bot]"

# Public set kept for callers that need direct membership checks (e.g. tests
# that assert the canonical built-in pair without env-var bypass interference).
CODEX_REVIEW_BOT_LOGINS = frozenset({_CODEX_BOT_LOGIN, _CODEX_BOT_LOGIN_REST})
CODEX_REVIEW_RESULT_PREFIX = "Codex Review:"
CODEX_REVIEW_REACTION_CONTENTS = {"+1", "eyes"}

_log = logging.getLogger(__name__)


@functools.cache
def _extra_codex_logins() -> frozenset[str]:
    """Extra logins treated as Codex bot, from ``VOYAGER_TEST_BOT_LOGINS``.

    Comma-separated; whitespace around each login is stripped; empty parts
    are dropped (so ``"a,,b"``, ``"  ,a"``, and ``"  "`` all parse cleanly).
    Returns ``frozenset()`` when the env var is unset.

    Cached for the process lifetime — production sets the env once at startup
    (it doesn't; this is a sandbox-only signal) and reads at every classify
    site would otherwise re-split per comment. Tests that flip the env
    between scenarios must call ``_extra_codex_logins.cache_clear()`` in
    teardown; see ``tests/bdd/step_defs/test_swm_classify_steps.py``.

    Sandbox e2e harness only. Production never sets this; the ``TEST_``
    prefix in the var name signals intent. The longer-term shape is a TOML
    schema ``[voyager].review_bot_logins`` with per-bot marker dialect,
    landing alongside the GitHub Copilot integration.
    """
    raw = os.environ.get("VOYAGER_TEST_BOT_LOGINS", "")
    parsed = frozenset(s.strip() for s in raw.split(",") if s.strip())
    if parsed:
        _log.warning(
            "VOYAGER_TEST_BOT_LOGINS is set (extra Codex-equivalent logins: %s). "
            "Sandbox e2e bypass active — this must not be set in production.",
            sorted(parsed),
        )
    return parsed


def is_codex_login(login: str | None) -> bool:
    """True when ``login`` is the Codex bot or appears in the sandbox bypass.

    Matches both GraphQL (``chatgpt-codex-connector``) and REST
    (``chatgpt-codex-connector[bot]``) forms, plus any login listed in
    ``VOYAGER_TEST_BOT_LOGINS``. Used by both ``routing.py`` (webhook ingress)
    and ``classify.py`` (thread state) so the bypass is uniformly honored.
    """
    if login is None:
        return False
    if login in CODEX_REVIEW_BOT_LOGINS:
        return True
    return login in _extra_codex_logins()


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
