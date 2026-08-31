"""Visible comment text — strip regions that cannot contain a real command.

GitHub comment bodies routinely quote commands inside fenced code blocks
(documentation, pasted logs, transcripts). Trigger detection that plain-matches
against the raw body turns those quotes into real runs (issue #256): a
maintainer documenting ``/assembly`` usage in a code fence triggered a full
implementation run. This module removes fenced code blocks (``` / ~~~, any
length, info-string tolerated) and block-quote lines (``> ``) so triggers only
see text a human actually wrote as prose.
"""

from __future__ import annotations

import re

_FENCE_OPEN_RE = re.compile(r"^\s*(`{3,}|~{3,})")


def visible_comment_text(body: str | None) -> str:
    """Return the body with fenced code blocks and block quotes removed.

    Lines inside a fence are dropped entirely; fence markers themselves are
    dropped too. Block-quote lines (``>``) are dropped so quoted replies that
    include command lines cannot fire either. Inline code spans are kept —
    a human writing ``run `/assembly` now`` means the command.
    """
    if not body:
        return ""
    visible: list[str] = []
    fence_marker = ""
    for line in body.splitlines():
        if fence_marker:
            # Inside a fence: only a closing marker of the same character and
            # at least the same length ends it.
            close = _FENCE_OPEN_RE.match(line)
            if (
                close
                and close.group(1)[0] == fence_marker[0]
                and len(close.group(1)) >= len(fence_marker)
            ):
                fence_marker = ""
            continue
        open_ = _FENCE_OPEN_RE.match(line)
        if open_:
            fence_marker = open_.group(1)
            continue
        if line.lstrip().startswith(">"):
            continue
        visible.append(line)
    return "\n".join(visible)
