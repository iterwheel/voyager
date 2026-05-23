"""Assembly bot — deterministic branch naming.

Per VOY-1817 D8: ``<issue_number>-<short-kebab-slug>``, slug length-capped,
ASCII-folded, with a ``<issue_number>-issue`` empty-slug fallback so a
title like ``[Bug]: 🚨🚨🚨`` never produces an unrefable branch name.
"""

from __future__ import annotations

import re
import unicodedata

from .constants import MAX_SLUG_LENGTH


def _ascii_fold(value: str) -> str:
    """Strip combining marks then drop anything outside ASCII."""
    normalized = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return without_marks.encode("ascii", "ignore").decode("ascii")


def _kebab(value: str) -> str:
    """Lowercase, collapse non-alphanumerics into single hyphens."""
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def _strip_bracketed_kind(title: str) -> str:
    """Drop a leading ``[Kind]:`` prefix if present (Blueprint title format).

    The kind is already captured by the issue type label; carrying it in
    every branch slug would waste characters and read awkwardly.
    """
    return re.sub(r"^\s*\[[^\]]+\]\s*:?\s*", "", title or "")


def make_branch_name(issue_number: int, issue_title: str | None) -> str:
    """Return the deterministic Assembly branch name for an issue.

    Examples
    --------
    >>> make_branch_name(69, "[Feature]: Implement Assembly bot MVP")
    '69-implement-assembly-bot-mvp'
    >>> make_branch_name(99, "[Bug]: 🚨🚨🚨")
    '99-issue'
    """
    stripped = _strip_bracketed_kind(issue_title or "")
    folded = _ascii_fold(stripped)
    slug = _kebab(folded)
    if not slug:
        return f"{int(issue_number)}-issue"
    if len(slug) > MAX_SLUG_LENGTH:
        slug = slug[:MAX_SLUG_LENGTH].rstrip("-") or "issue"
    return f"{int(issue_number)}-{slug}"
