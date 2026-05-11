"""Codex thread state classification per SWM-1101 ('Thread State Classification').

A — fresh: not outdated, no author reply yet
B — outdated: author pushed code that invalidated the diff anchor
C — replied: not outdated, author posted an in-thread reply

A thread can be both B and C in practice (author fixed code AND replied). This
module returns the dominant state — B if outdated, otherwise C, otherwise A —
and downstream callers can still inspect the full thread for additional signal.
"""

from __future__ import annotations

from enum import StrEnum

CODEX_BOT_LOGIN = "chatgpt-codex-connector"
CODEX_BOT_LOGIN_REST = "chatgpt-codex-connector[bot]"

# Voyager writes the new prefix; the old SWM prefix is kept in the read-side
# filter so that PRs already carrying sweeping-monk's old conclusion comments
# do not promote those comments to "author replies" after the rename and flip
# the thread state from A to C. The user explicitly chose the new prefix and
# said the old comments are frozen; this is purely defensive on the read path.
CLEARANCE_MARKER_PREFIX = "<!-- clearance-"
_LEGACY_SWM_MARKER_PREFIX = "<!-- swm-"
_RECOGNIZED_CONCLUSION_PREFIXES = (CLEARANCE_MARKER_PREFIX, _LEGACY_SWM_MARKER_PREFIX)


class ThreadState(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class CodexBodySignal(StrEnum):
    REVIEWING = "reviewing"
    APPROVED = "approved"


def _is_bot_conclusion_comment(body: str | None) -> bool:
    """True when the comment body carries any recognized bot-conclusion marker."""
    if not body:
        return False
    return any(body.startswith(prefix) for prefix in _RECOGNIZED_CONCLUSION_PREFIXES)


def codex_pr_body_signal(reactions: list[dict]) -> CodexBodySignal | None:
    """Codex bot signals its review state by reacting to the PR body itself:
      EYES (👀)        — currently reviewing this head
      THUMBS_UP (👍)  — reviewed; approves / no new issues

    A single bot reactor; if both ever appear (transition window), THUMBS_UP wins.
    Returns None when Codex hasn't reacted yet.
    """
    has_thumbs = False
    has_eyes = False
    for r in reactions or []:
        login = (r.get("user") or {}).get("login") or ""
        if login not in (CODEX_BOT_LOGIN, CODEX_BOT_LOGIN_REST):
            continue
        if r.get("content") == "THUMBS_UP":
            has_thumbs = True
        elif r.get("content") == "EYES":
            has_eyes = True
    if has_thumbs:
        return CodexBodySignal.APPROVED
    if has_eyes:
        return CodexBodySignal.REVIEWING
    return None


def _login(comment: dict) -> str | None:
    return ((comment or {}).get("author") or {}).get("login")


def _comment_nodes(thread: dict) -> list[dict]:
    return (thread.get("comments") or {}).get("nodes") or []


def is_codex_thread(thread: dict) -> bool:
    """A thread is 'Codex' iff its first comment was authored by the Codex bot."""
    comments = _comment_nodes(thread)
    return bool(comments) and _login(comments[0]) == CODEX_BOT_LOGIN


def codex_comment_id(thread: dict) -> int | None:
    comments = _comment_nodes(thread)
    return comments[0].get("databaseId") if comments else None


def author_replies(thread: dict) -> list[dict]:
    """All comments after the first one (Codex's) — i.e., replies."""
    return [c for c in _comment_nodes(thread)[1:] if c]


def latest_author_reply(thread: dict, *, author_login: str | None = None) -> dict | None:
    """The most recent reply by the PR author, or None.

    When ``author_login`` is None (legacy / test fixtures), falls back to
    the older behavior of "any non-Codex, non-bot-conclusion reply."
    Production callers should always pass the PR author so a reviewer's
    or maintainer's comment with a code identifier doesn't get judged
    substantive.

    Bot-conclusion comments (Clearance's own, and the legacy SWM prefix from
    sweeping-monk before the rename) are filtered out so they cannot be
    mistaken for author engagement.
    """
    replies = [
        c
        for c in author_replies(thread)
        if _login(c) != CODEX_BOT_LOGIN and not _is_bot_conclusion_comment(c.get("body"))
    ]
    if author_login is not None:
        replies = [c for c in replies if _login(c) == author_login]
    return replies[-1] if replies else None


def latest_codex_followup(thread: dict) -> dict | None:
    """A Codex follow-up comment after its initial review — used for 👍/👎 detection."""
    followups = [c for c in author_replies(thread) if _login(c) == CODEX_BOT_LOGIN]
    return followups[-1] if followups else None


def classify_thread(thread: dict) -> ThreadState:
    """Return A/B/C per the SWM-1101 taxonomy. Outdated wins over replied."""
    if thread.get("isOutdated"):
        return ThreadState.B
    if latest_author_reply(thread) is not None:
        return ThreadState.C
    return ThreadState.A
