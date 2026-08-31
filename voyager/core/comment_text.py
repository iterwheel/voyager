"""Visible comment text — prose a human actually wrote as a command surface.

GitHub comment bodies routinely quote commands inside code blocks (fenced or
indented), block quotes, nested lists, and HTML comments. Trigger detection
that plain-matches the raw body turns those quotes into real runs (issue
#256). Class-closing ruling on #336: hand-parsing an unbounded grammar
(CommonMark) invites infinite edge rounds — this module delegates to a real
Markdown parser (markdown-it-py, already in the dependency tree) and keeps
ONLY the inline text of top-level paragraphs and headings. Everything inside
any container (fence, indented code block, block quote, list item — at any
nesting depth) or HTML construct is invisible by construction; inline code
spans are prose and stay visible. Commands inside quoted/nested content are
unreachable, definitionally.
"""

from __future__ import annotations

from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark")

_CONTAINER_OPEN = frozenset({"blockquote_open", "list_open", "list_item_open", "paragraph_open"})
_CONTAINER_CLOSE = frozenset(
    {"blockquote_close", "list_close", "list_item_close", "paragraph_close"}
)


# Inline children whose content is kept (prose, inline code, line breaks).
def visible_comment_text(body: str | None) -> str:
    """Return the inline text of top-level paragraphs/headings only.

    Content inside fences, indented code blocks, block quotes, list items
    (any nesting), and HTML blocks/inline HTML (comments) is dropped by the
    parser's token structure — no bespoke grammar cases.
    """
    if not body:
        return ""
    parts: list[str] = []
    paragraph: list[str] = []
    depth = 0
    in_heading = False
    for token in _md.parse(body):
        if token.type == "heading_open":
            in_heading = True
            depth += 1
        elif token.type == "heading_close":
            in_heading = False
            depth -= 1
        elif token.type in _CONTAINER_OPEN:
            depth += 1
        elif token.type in _CONTAINER_CLOSE:
            depth -= 1
        elif token.type == "inline" and depth == 1 and token.children and not in_heading:
            # An inline token follows its paragraph_open/heading_open; it is
            # top-level prose only when the enclosing depth is exactly the
            # block's own level (1). Heading content is NOT a command surface
            # (P1 round 9: '# /assembly' is a documentation heading, never a
            # command — the '#' prefix must not vanish).
            paragraph = []
            for child in token.children:
                if child.type in ("softbreak", "hardbreak"):
                    paragraph.append("\n")
                elif child.type in ("text", "code_inline"):
                    paragraph.append(child.content)
            parts.append("".join(paragraph))
        # fences, code blocks, html blocks emit no inline tokens — dropped
    return "\n".join(parts)
