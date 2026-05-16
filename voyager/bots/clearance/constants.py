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

# Module-level guard so the "bypass active" warning fires exactly once per
# process, no matter how many times the cache is cleared (e.g. by a test
# suite that flips the env between scenarios). Gemini r2 P3 — warning was
# emitting on every cache miss, spamming test logs ~20 times per run.
_BYPASS_WARNED_ONCE = False


_BOT_SUFFIX = "[bot]"


def _expand_login_forms(login: str) -> tuple[str, str]:
    """Return both GraphQL (bare) and REST (``[bot]``) forms of a login.

    GitHub Apps surface as ``app-slug`` in GraphQL responses (thread author,
    reaction user via GraphQL) and as ``app-slug[bot]`` in REST webhook
    payloads (issue_comment.user, reaction.user via REST). The built-in
    ``CODEX_REVIEW_BOT_LOGINS`` carries both forms so identity matches
    uniformly; operator-supplied extras must auto-expand the same way or
    REST webhooks for the test app get silently dropped at routing ingress.

    Idempotent on either form:
      "voyager-e2e-bot"       → ("voyager-e2e-bot", "voyager-e2e-bot[bot]")
      "voyager-e2e-bot[bot]"  → ("voyager-e2e-bot", "voyager-e2e-bot[bot]")
    """
    if login.endswith(_BOT_SUFFIX):
        bare = login[: -len(_BOT_SUFFIX)]
        return bare, login
    return login, f"{login}{_BOT_SUFFIX}"


@functools.cache
def _extra_codex_logins() -> frozenset[str]:
    """Extra logins treated as Codex bot, from ``VOYAGER_TEST_BOT_LOGINS``.

    Comma-separated; whitespace around each login is stripped; empty parts
    are dropped (so ``"a,,b"``, ``"  ,a"``, and ``"  "`` all parse cleanly).
    Each parsed login is auto-expanded to BOTH its bare (GraphQL) and
    ``[bot]``-suffixed (REST) forms, matching how the built-in Codex pair
    carries both — operators list each app once and the bypass honors
    both webhook surfaces (Codex r3 P2 on commit 5ebe56c).

    Returns ``frozenset()`` when the env var is unset.

    Cached for the process lifetime — production sets the env once at startup
    (it doesn't; this is a sandbox-only signal) and reads at every classify
    site would otherwise re-split per comment. Tests that flip the env
    between scenarios call ``reset_test_bot_login_cache()`` (a public
    test-facing helper) instead of reaching into the private ``cache_clear``.

    Sandbox e2e harness only. Production never sets this; the ``TEST_``
    prefix in the var name signals intent. The longer-term shape is a TOML
    schema ``[voyager].review_bot_logins`` with per-bot marker dialect,
    landing alongside the GitHub Copilot integration.
    """
    global _BYPASS_WARNED_ONCE
    raw = os.environ.get("VOYAGER_TEST_BOT_LOGINS", "")
    parsed_raw = frozenset(s.strip() for s in raw.split(",") if s.strip())
    expanded: set[str] = set()
    for login in parsed_raw:
        expanded.update(_expand_login_forms(login))
    parsed = frozenset(expanded)
    if parsed and not _BYPASS_WARNED_ONCE:
        _log.warning(
            "VOYAGER_TEST_BOT_LOGINS is set (extra Codex-equivalent logins: %s). "
            "Sandbox e2e bypass active — this must not be set in production.",
            sorted(parsed),
        )
        _BYPASS_WARNED_ONCE = True
    return parsed


def reset_test_bot_login_cache() -> None:
    """Clear the VOYAGER_TEST_BOT_LOGINS cache. Test-only.

    Tests that flip the env var between scenarios call this in setup/teardown
    so the next read picks up the new value. Production callers should never
    need this — the env is read once per process at first lookup.

    Also resets the "warned once" guard so a fresh test fixture observing the
    bypass-set state can assert the warning fires.
    """
    global _BYPASS_WARNED_ONCE
    _extra_codex_logins.cache_clear()
    _BYPASS_WARNED_ONCE = False


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


CLEARANCE_PENDING_LABEL = "clearance-1-pending"
CLEARANCE_BLOCKED_LABEL = "clearance-2-blocked"
CLEARANCE_READY_FOR_APPROVAL_LABEL = "clearance-3-ready-for-approval"
CLEARANCE_READY_LABEL = "clearance-4-ready-for-merge"
CLEARANCE_LABELS = (
    CLEARANCE_PENDING_LABEL,
    CLEARANCE_BLOCKED_LABEL,
    CLEARANCE_READY_FOR_APPROVAL_LABEL,
    CLEARANCE_READY_LABEL,
)
LEGACY_CLEARANCE_LABELS = (
    "clearance-pending",
    "clearance-blocked",
    "clearance-ready",
)
ALL_CLEARANCE_LABELS = CLEARANCE_LABELS + LEGACY_CLEARANCE_LABELS


@functools.cache
def configured_review_request_users() -> tuple[str, ...]:
    """Parses VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS: comma-sep, whitespace-stripped,
    empty parts dropped. Returns empty tuple when unset (pre-#25 fallback behavior).
    Tuple-typed so configured order is preserved in the operator-facing comment."""
    raw = os.environ.get("VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS", "")
    return tuple(s.strip() for s in raw.split(",") if s.strip())


def reset_review_request_users_cache() -> None:
    """Test-only cache reset. Mirrors reset_test_bot_login_cache."""
    configured_review_request_users.cache_clear()


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
