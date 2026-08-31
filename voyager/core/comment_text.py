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

import re
from collections.abc import Iterator

from markdown_it import MarkdownIt

# GFM tables are containers on GitHub: enable the table rule so table rows
# never reach the command surface (P1 round 10).
_md = MarkdownIt("commonmark").enable("table")

_CONTAINER_OPEN = frozenset({"blockquote_open", "list_open", "list_item_open", "paragraph_open"})
_CONTAINER_CLOSE = frozenset(
    {"blockquote_close", "list_close", "list_item_close", "paragraph_close"}
)

# Standard HTML/GFM block containers (P1 round 12: the complete supported set,
# not a partial allowlist).
_HTML_CONTAINER_TAGS = frozenset(
    {
        "details",
        "summary",
        "blockquote",
        "figure",
        "figcaption",
        "center",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "td",
        "th",
        "div",
        "section",
        "article",
        "aside",
        "header",
        "footer",
        "main",
        "nav",
        "form",
        "fieldset",
        "dl",
        "ol",
        "ul",
        "li",
        "dd",
        "dt",
        "address",
        "pre",
        "canvas",
        "template",
    }
)
# Elements whose end tag may be implied (unclosed <li>/<p>/<td>/... are closed
# by their parent's closer, as browsers do).
_IMPLIED_END_TAGS = frozenset(
    {"li", "p", "td", "th", "tr", "dt", "dd", "option", "thead", "tbody", "tfoot", "summary"}
)

_HTML_COMMENT_BLOCK_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _iter_html_tags(raw: str) -> Iterator[tuple[bool, str]]:
    """Yield (is_close, tag_name) for each real tag token in an HTML fragment.

    A tiny state machine that skips quoted attribute values, so tag-shaped
    text inside attributes ('</details>' in title="...") and HTML comments
    are never counted as tags.
    """
    i = 0
    n = len(raw)
    while i < n:
        if raw.startswith("<!--", i):
            end = raw.find("-->", i + 4)
            i = n if end < 0 else end + 3
            continue
        if raw[i] == "<":
            j = i + 1
            is_close = j < n and raw[j] == "/"
            if is_close:
                j += 1
            m = re.match(r"[a-zA-Z][a-zA-Z0-9-]*", raw[j:])
            if not m:
                i += 1
                continue
            name = m.group(0)
            # Skip to the tag's '>' ignoring quoted attribute values.
            k = j + len(name)
            quote = ""
            while k < n:
                ch = raw[k]
                if quote:
                    if ch == quote:
                        quote = ""
                elif ch in ("'", '"'):
                    quote = ch
                elif ch == ">":
                    break
                k += 1
            yield is_close, name
            i = k + 1
            continue
        i += 1


def _apply_container_tags(raw: str, stack: list[str]) -> None:
    """Apply an HTML fragment's container tags to an open-tag stack.

    Closers only pop when they MATCH the innermost open container — a
    mismatched closer ('</div>' inside '<details>') is ignored, exactly as
    browsers do (P2 round 14). HTML comments are skipped and only real tag
    tokens count — attribute text never does.
    """
    for is_close, name in _iter_html_tags(raw):
        tag = name.lower()
        if tag not in _HTML_CONTAINER_TAGS:
            continue
        if is_close:
            # A closer pops its OWN open tag; any unclosed IMPLIED-end
            # elements above it ('<ul><li>x</ul>') are implicitly closed
            # first, exactly as browsers do.
            if tag in stack:
                while stack and stack[-1] != tag:
                    if stack[-1] not in _IMPLIED_END_TAGS:
                        break  # mismatched non-optional inner: ignore closer
                    stack.pop()
                if stack and stack[-1] == tag:
                    stack.pop()
        else:
            stack.append(tag)


def visible_comment_text(body: str | None) -> str:
    """Return the inline text of top-level paragraphs/headings only.

    Content inside fences, indented code blocks, block quotes, list items
    (any nesting), and HTML blocks/inline HTML (comments) is dropped by the
    parser's token structure — no bespoke grammar cases.
    """
    if not body:
        return ""
    parts: list[str] = []
    depth = 0
    in_heading = False
    html_containers: list[str] = []  # open containers; matched closers pop
    for token in _md.parse(body):
        if token.type == "html_block":
            # Raw-HTML CONTAINER tags hide the Markdown blocks they enclose.
            # A single html_block may contain BOTH the opening and closing
            # tags ('<table>...</table>' on one token) — process EVERY tag
            # occurrence and apply the net balance (P2 round 11).
            _apply_container_tags(token.content or "", html_containers)
            continue
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
            # Inline container tags inside the paragraph ('intro <details>…')
            # hide GitHub-rendered content: drop such paragraphs entirely
            # (P1 round 13) — their text is inside the HTML construct.
            # Walk children IN ORDER (P1/P2 round 15): inline tags are
            # always applied to the stack; text is emitted only while the
            # stack is empty; emphasis markers are preserved so formatted
            # documentation ('**/assembly**') never becomes a line-start
            # command.
            visible_spans: list[str] = []
            for child in token.children:
                if child.type == "html_inline":
                    _apply_container_tags(child.content, html_containers)
                    continue
                if html_containers:
                    continue  # inside a container: text is hidden
                if child.type in ("softbreak", "hardbreak"):
                    visible_spans.append("\n")
                elif child.type == "text":
                    visible_spans.append(child.content)
                elif child.type == "code_inline":
                    visible_spans.append(f"`{child.content}`")
                elif child.type in ("em_open", "em_close", "strong_open", "strong_close"):
                    visible_spans.append(child.markup or "**")
                elif child.type == "link_open":
                    visible_spans.append("[")
                elif child.type == "link_close":
                    visible_spans.append("](")
            if visible_spans:
                parts.append("".join(visible_spans))
        # fences, code blocks, html blocks emit no inline tokens — dropped
    return "\n".join(parts)
