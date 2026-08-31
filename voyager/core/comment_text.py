"""Visible comment text — strip regions that cannot contain a real command.

GitHub comment bodies routinely quote commands inside code blocks (fenced or
indented: documentation, pasted logs, transcripts) and block quotes. Trigger
detection that plain-matches against the raw body turns those quotes into real
runs (issue #256): a maintainer documenting ``/assembly`` usage in a code fence
triggered a full implementation run. This module removes those regions so
triggers only see text a human actually wrote as prose.
"""

from __future__ import annotations

import re

_FENCE_OPEN_RE = re.compile(r"^\s*(`{3,}|~{3,})")


def visible_comment_text(body: str | None) -> str:
    """Return the body with code blocks and block quotes removed.

    Fenced code blocks (``` / ~~~, any length) are dropped, and a closing
    fence must be a *bare* marker line — a same-length marker with an info
    string (```` ```python ````) does not close a fence, matching GitHub
    Flavored Markdown (Codex P1 on #336). Indented code blocks (4 spaces or a
    tab) are dropped too (Codex P1). Block-quote lines (``>``) are dropped,
    and — per Markdown's lazy continuation — so are the non-blank lines that
    follow a quote line until a blank line separates them (Codex P1:
    ``> docs say`` / ``/assembly`` renders as one quoted paragraph). Inline
    code spans are kept — a human writing ``run `/assembly` now`` means the
    command.
    """
    if not body:
        return ""
    visible: list[str] = []
    fence_marker = ""
    in_quote = False
    in_indented_code = False
    awaiting_code_block = False  # set on blank line; cleared by prose
    for line in body.splitlines():
        if fence_marker:
            # Inside a fence: only a BARE closing marker of the same character
            # and at least the same length ends it — no info string allowed.
            close = _FENCE_OPEN_RE.match(line)
            if (
                close
                and close.group(1)[0] == fence_marker[0]
                and len(close.group(1)) >= len(fence_marker)
                and line.strip() == close.group(1)
            ):
                fence_marker = ""
            continue
        open_ = _FENCE_OPEN_RE.match(line)
        if open_:
            fence_marker = open_.group(1)
            in_quote = False
            continue
        if not line.strip():
            # Blank line ends a lazy quote continuation (and keeps paragraphs)
            # and may start an indented code block on the next line.
            in_quote = False
            awaiting_code_block = True
            visible.append(line)
            continue
        if line.lstrip().startswith(">"):
            in_quote = True
            awaiting_code_block = False
            in_indented_code = False
            continue
        if in_quote:
            # Lazy continuation of the preceding block quote.
            continue
        if line.startswith("    ") or line.startswith("\t"):
            # Indented code block — but only when a blank line separates it
            # from preceding prose (GFM). A document-start indented command or
            # a mid-paragraph indent is a lazy continuation / leading-space
            # tolerance, which the /assembly parser deliberately supports
            # (Codex round-5 CRLF feature) and stays visible.
            if awaiting_code_block:
                in_indented_code = True
            if not in_indented_code:
                visible.append(line)
            continue
        in_indented_code = False
        awaiting_code_block = False
        visible.append(line)
    return "\n".join(visible)
