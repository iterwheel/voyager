"""Countdown bot — event-driven trigger from Clearance resolved verdicts.

CHG-1838: Clearance posts a per-thread RESOLVED verdict reply on the PR the
moment a review thread becomes gate-approvable. This route recognizes that
reply and touches a machine-local trigger file so the Countdown adaptive
scheduler (deploy/wukong/countdown-resolve-loop-adaptive.sh) can wake early
instead of waiting out its polling interval. No payload data crosses the
boundary — the touch itself is the entire signal.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from voyager.bots.clearance.close_reason import _status_heading
from voyager.bots.clearance.constants import CLEARANCE_BOT_LOGIN, logins_equivalent

_log = logging.getLogger(__name__)

# Agent slug used only for the bridge's repository-allowlist gate (server.py
# checks this before calling route_countdown_trigger — CHG-1838 major finding
# 2). No route dict ever carries this value; the route never reaches
# _filter_routes_by_repository itself since it always returns [].
COUNTDOWN_AGENT_SLUG = "iterwheel-countdown"

DEFAULT_TRIGGER_PATH = Path.home() / ".voyager" / "countdown-resolve-loop.trigger"

# Reuse close_reason's heading renderer (D2) instead of duplicating the
# "✅ **Clearance: resolved**" literal.
RESOLVED_HEADING = _status_heading("RESOLVED")


def trigger_path() -> Path:
    """Resolve the trigger file path, honoring the COUNTDOWN_TRIGGER_PATH override."""
    override = os.environ.get("COUNTDOWN_TRIGGER_PATH")
    return Path(override) if override else DEFAULT_TRIGGER_PATH


def touch_trigger_file(path: Path | None = None) -> bool:
    """Create the trigger file or bump its mtime.

    Fails open (D6): any I/O error is logged and swallowed, never raised —
    a trigger failure must not fail webhook handling for other bots.
    """
    target = path or trigger_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # exist_ok=True already bumps mtime to now on an existing file — a
        # separate os.utime call is redundant and, worse, a delete-between-
        # calls race that can log a false failure.
        target.touch(exist_ok=True)
    except OSError:
        _log.warning("countdown_trigger_touch_failed: path=%s", target, exc_info=True)
        return False
    return True


def _is_clearance_resolved_reply(event: str, payload: dict[str, Any]) -> bool:
    if event != "pull_request_review_comment":
        return False
    if (payload.get("action") or "") != "created":
        return False

    comment = payload.get("comment") or {}
    user = comment.get("user") or {}
    if not logins_equivalent(user.get("login"), CLEARANCE_BOT_LOGIN):
        return False

    body = str(comment.get("body") or "")
    return RESOLVED_HEADING in body


def route_countdown_trigger(event: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Touch the Countdown trigger file on a Clearance RESOLVED verdict reply.

    Fires only on ``pull_request_review_comment.created`` authored by
    Clearance whose body contains the RESOLVED status heading (D1). Every
    other case is a no-op. Always returns an empty list — the file touch is
    the entire side effect; no writeback route is dispatched.

    At-least-once (D7): GitHub may redeliver the same webhook (identical
    delivery ID); this route has no delivery-ID dedup, so a redelivery
    re-touches an already-consumed trigger and costs at most one extra
    scheduler scan. Deliberately not deduped — see D7.
    """
    if _is_clearance_resolved_reply(event, payload):
        touch_trigger_file()
    return []
