"""Assembly bot — actor authorization gate.

Per VOY-1818: evaluate whether the comment author is authorized to trigger
Assembly.  The gate runs at routing time, before precondition checks and
before any backend dispatch.  Bots are always refused regardless of
allow-list membership.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from .constants import (
    AUTHORIZED_ACTORS_ENV,
    AUTHORIZED_ASSOCIATIONS_ENV,
    DEFAULT_AUTHORIZED_ASSOCIATIONS,
    REFUSAL_UNAUTHORIZED_ACTOR,
)

_log = logging.getLogger(__name__)

# Bot-login suffix per D7.
_BOT_SUFFIX = "[bot]"


def _parse_token_list(raw: str, *, case: str = "lower") -> list[str]:
    """Split a whitespace/comma-separated env value into a normalized list.

    Mirrors the ``BRIDGE_ALLOWED_REPOSITORIES_*`` parsing pattern.
    """
    tokens = re.split(r"[,\s]+", raw)
    normalized: list[str] = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        normalized.append(token.lower() if case == "lower" else token.upper())
    return normalized


def _resolve_authorized_actors() -> set[str]:
    """Return the canonical-lowercase set of authorized actor logins."""
    raw = os.environ.get(AUTHORIZED_ACTORS_ENV)
    if raw is None:
        return set()
    return set(_parse_token_list(raw, case="lower"))


def _resolve_trusted_associations() -> set[str]:
    """Return the canonical-uppercase set of trusted associations.

    Semantics per D6:
      - unset → empty set (no association is trusted)
      - set-but-empty → default set (OWNER, MEMBER, COLLABORATOR)
      - set-to-value → exact parsed values
    """
    raw = os.environ.get(AUTHORIZED_ASSOCIATIONS_ENV)
    if raw is None:
        return set()
    tokens = _parse_token_list(raw, case="upper")
    if not tokens:
        return set(DEFAULT_AUTHORIZED_ASSOCIATIONS)
    return set(tokens)


@dataclass(frozen=True)
class ActorAuthorization:
    """Outcome of the actor authorization gate."""

    ok: bool
    reason: str | None
    actor_login: str | None
    actor_association: str | None
    actor_type: str | None
    actor_sender_login: str | None
    sender_divergent: bool
    matched_signal: str | None


def evaluate_actor_authorization(payload: dict[str, Any]) -> ActorAuthorization:
    """Evaluate whether the comment actor is authorized per VOY-1818.

    Steps
    -----
    1. Extract ``comment.user.*`` and ``sender.login`` from the payload.
    2. Deny on malformed / missing metadata.
    3. D7 bot precedence: deny bots before allow-list / association checks.
    4. D5 allow-list / association: either signal is sufficient.
    5. D13 sender divergence: warn but do not change the decision.
    """
    # --- Step 1: extract actor metadata ---
    comment = payload.get("comment")
    if not isinstance(comment, dict):
        return ActorAuthorization(
            ok=False,
            reason=REFUSAL_UNAUTHORIZED_ACTOR,
            actor_login=None,
            actor_association=None,
            actor_type=None,
            actor_sender_login=None,
            sender_divergent=False,
            matched_signal=None,
        )

    user = comment.get("user")
    if not isinstance(user, dict):
        return ActorAuthorization(
            ok=False,
            reason=REFUSAL_UNAUTHORIZED_ACTOR,
            actor_login=None,
            actor_association=None,
            actor_type=None,
            actor_sender_login=None,
            sender_divergent=False,
            matched_signal=None,
        )

    raw_login = user.get("login")
    if not isinstance(raw_login, str) or not raw_login:
        return ActorAuthorization(
            ok=False,
            reason=REFUSAL_UNAUTHORIZED_ACTOR,
            actor_login=None,
            actor_association=None,
            actor_type=None,
            actor_sender_login=None,
            sender_divergent=False,
            matched_signal=None,
        )

    comment_login = raw_login.lower()
    raw_type = user.get("type")
    actor_type: str | None = raw_type if isinstance(raw_type, str) else None
    raw_assoc = comment.get("author_association")
    actor_association: str | None = raw_assoc.upper() if isinstance(raw_assoc, str) else None

    # sender.login
    sender = payload.get("sender")
    sender_login_raw = (sender if isinstance(sender, dict) else {}).get("login")
    sender_login: str | None = (
        sender_login_raw.lower() if isinstance(sender_login_raw, str) else None
    )

    # --- Step 2 is handled above (malformed → deny) ---

    # --- Step 3: D7 bot precedence ---
    is_bot = (actor_type == "Bot") or comment_login.endswith(_BOT_SUFFIX.lower())
    if is_bot:
        return ActorAuthorization(
            ok=False,
            reason=REFUSAL_UNAUTHORIZED_ACTOR,
            actor_login=comment_login,
            actor_association=actor_association,
            actor_type=actor_type,
            actor_sender_login=sender_login,
            sender_divergent=False,
            matched_signal=None,
        )

    # --- Step 4: allow-list / association check (D5) ---
    authorized_actors = _resolve_authorized_actors()
    trusted_associations = _resolve_trusted_associations()

    matched_signal: str | None = None

    if comment_login in authorized_actors:
        matched_signal = "allow_list"
    elif actor_association is not None and actor_association in trusted_associations:
        matched_signal = "association"

    ok = matched_signal is not None

    # --- Step 5: D13 sender divergence ---
    sender_divergent = False
    if sender_login is not None and sender_login != comment_login:
        sender_divergent = True
        _log.warning(
            "assembly_actor_sender_divergence: comment=%r sender=%r",
            comment_login,
            sender_login,
        )

    if not ok:
        return ActorAuthorization(
            ok=False,
            reason=REFUSAL_UNAUTHORIZED_ACTOR,
            actor_login=comment_login,
            actor_association=actor_association,
            actor_type=actor_type,
            actor_sender_login=sender_login,
            sender_divergent=sender_divergent,
            matched_signal=None,
        )

    return ActorAuthorization(
        ok=True,
        reason=None,
        actor_login=comment_login,
        actor_association=actor_association,
        actor_type=actor_type,
        actor_sender_login=sender_login,
        sender_divergent=sender_divergent,
        matched_signal=matched_signal,
    )
