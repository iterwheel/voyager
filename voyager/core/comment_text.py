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

# A fence opener: optionally inside a list item (marker + indent — GFM keeps
# fences as the first block of a list item, Codex P1 round 7).
# A fence opener: 0-3 leading SPACES (a tab starts indented code, not a
# fence — Codex P2 round 8), optionally after a list marker.
_FENCE_OPEN_RE = re.compile(r"^(?:[-*+] |[0-9]+[.)] )? {0,3}(`{3,}|~{3,})")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_ATX_HEADING_RE = re.compile(r"^ {0,3}#{1,6}\s")
_NEW_BLOCK_RE = re.compile(r"^(?:#{1,6}\s|[-*+]\s|\d+[.)]\s)")


_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def _mask_inline_code(text: str) -> str:
    """Replace inline code spans with same-length fillers."""
    return _INLINE_CODE_RE.sub(lambda m: "\u0000" * len(m.group(0)), text)


def _strip_terminated_html_comments(body: str) -> str:
    """Remove <!-- ... --> regions while leaving inline-code spans intact."""
    masked = _mask_inline_code(body)
    out: list[str] = []
    i = 0
    while i < len(body):
        if masked.startswith("<!--", i):
            end = masked.find("-->", i + 4)
            if end >= 0:
                i = end + 3  # drop the whole terminated comment
                continue
        out.append(body[i])
        i += 1
    return "".join(out)


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
    # Codex P1 round 8: fence parsing runs BEFORE comment stripping — HTML
    # syntax inside fenced content is literal code and must not be able to
    # synthesize or remove fence markers.
    visible: list[str] = []
    fence_marker = ""
    in_quote = False
    in_indented_code = False
    awaiting_code_block = False  # set on blank line; cleared by prose
    for line in body.splitlines():
        if fence_marker:
            # Inside a fence: only a BARE closing marker of the same character,
            # at least the same length, indented at most three spaces (GFM),
            # ends it — no info string allowed.
            close = _FENCE_OPEN_RE.match(line)
            if (
                close
                and close.group(1)[0] == fence_marker[0]
                and len(close.group(1)) >= len(fence_marker)
                and line.strip() == close.group(1)
                and len(line) - len(line.lstrip()) <= 3
            ):
                fence_marker = ""
                # A closing fence is a block boundary: an indented line after
                # it is an indented code block (Codex P2 round 5 on #337).
                awaiting_code_block = True
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
            # A quote line whose CONTENT is a block construct (heading, list,
            # fence) cannot be lazily continued — the next line starts new
            # content (Codex P2 round 8).
            content = line.lstrip()[1:].lstrip()
            in_quote = not (
                content.startswith("#")
                or _NEW_BLOCK_RE.match(content)
                or _FENCE_OPEN_RE.match(content)
            )
            awaiting_code_block = False
            in_indented_code = False
            continue
        if in_quote:
            # Lazy continuation only applies to paragraph text: a line that
            # starts a new block construct (heading, list, fence) after the
            # quote is its own content and stays visible (Codex P1 round 2).
            # A MALFORMED hash line ("#tag" without space, or deep-indented
            # "# x") is paragraph text, not a heading — it continues the quote.
            stripped = line.lstrip()
            is_heading = (
                stripped.startswith("#")
                and len(line) - len(line.lstrip()) <= 3
                and len(stripped) > 1
                and stripped[1] in " #"
            )
            if (is_heading and not stripped.startswith("# ")) or not is_heading:
                if _NEW_BLOCK_RE.match(line) or _FENCE_OPEN_RE.match(line):
                    in_quote = False
                else:
                    continue
            # a real heading exits the quote and falls through
        if _ATX_HEADING_RE.match(line):
            # A heading is a block boundary: an indented line after it is an
            # indented code block (Codex P1 round 3). A deeper-indented '#'
            # line is indented code, not a heading (Codex P2 round 5 on #337).
            visible.append(line)
            awaiting_code_block = True
            in_indented_code = False
            in_quote = False
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
    stripped = "\n".join(visible)
    stripped = _strip_terminated_html_comments(stripped)
    # Unterminated comment: everything after the opener is hidden (GFM) —
    # but an opener inside an inline code span is literal text (round 7).
    masked = _mask_inline_code(stripped)
    if "<!--" in masked:
        stripped = stripped[: masked.index("<!--")]
    return stripped
