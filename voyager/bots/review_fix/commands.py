"""Slash-command parsing for the governed PR review-fix bot."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .constants import REVIEW_FIX_COMMANDS

_COMMAND_RE = re.compile(
    r"^[ \t]*(/(?:review-fix|pr-review-fix))(?=[ \t\r]|$)(?P<rest>[^\n]*)$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class ReviewFixCommand:
    """Parsed review-fix command invocation."""

    command: str
    dry_run: bool


def parse_review_fix_command(body: str | None) -> ReviewFixCommand | None:
    if not body:
        return None
    match = _COMMAND_RE.search(body)
    if not match:
        return None
    command = match.group(1).lower()
    if command not in REVIEW_FIX_COMMANDS:
        return None
    flags = {
        token for token in (match.group("rest") or "").lower().split() if token.startswith("--")
    }
    return ReviewFixCommand(command=command, dry_run="--dry-run" in flags)
