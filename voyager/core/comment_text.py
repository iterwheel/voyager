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
# Non-paragraph block containers whose inline content is NOT top-level prose
# (tables, table rows/cells — round 23).
_TABLE_OPEN = frozenset({"table_open", "thead_open", "tbody_open", "tr_open", "td_open", "th_open"})
_TABLE_CLOSE = frozenset(
    {"table_close", "thead_close", "tbody_close", "tr_close", "td_close", "th_close"}
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
        if raw.startswith("<![CDATA[", i):
            end = raw.find("]]>", i + 9)
            i = n if end < 0 else end + 3
            continue
        # Raw-text elements: everything until the matching close tag is text,
        # not markup — a tag-shaped string inside <script> never counts.
        rt = re.match(r"<(script|style|textarea|title)\b", raw[i:], re.I)
        if rt:
            close = re.compile(rf"</{rt.group(1)}\s*>", re.I).search(raw, i)
            i = n if not close else close.end()
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


def _is_container_only(fragment: str) -> bool:
    """True when an inline-HTML fragment consists solely of container tags.

    '<details>' / '</details>' drive the stack; mixed or formatting tags
    ('<em>') are preserved as markup instead.
    """
    return all(
        name.lower() in _HTML_CONTAINER_TAGS for _close, name in _iter_html_tags(fragment)
    ) and bool(list(_iter_html_tags(fragment)))


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
    spans: list[tuple[str, list[str]]] = []
    depth = 0
    in_heading = False
    in_container_block = False
    html_containers: list[str] = []  # open containers; matched closers pop
    for token in _md.parse(body):
        if token.type == "html_block":
            # Raw-HTML CONTAINER tags hide the Markdown blocks they enclose.
            # A single html_block may contain BOTH the opening and closing
            # tags — process EVERY tag occurrence (round 11). HTML inside
            # OTHER markdown containers (a quoted '<details>' example)
            # cannot enclose top-level content — its state is not applied
            # (round 20).
            if depth == 0:
                _apply_container_tags(token.content or "", html_containers)
            continue
        if token.type in _TABLE_OPEN:
            in_container_block = True
            depth += 1
            continue
        if token.type in _TABLE_CLOSE:
            in_container_block = False
            depth -= 1
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
        elif (
            token.type == "inline"
            and depth == 1
            and token.children
            and not in_heading
            and not in_container_block
        ):
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
                    if "<!--" in child.content:
                        # HTML comments hide their content (P2 round 19).
                        continue
                    # Non-container inline HTML (<em>, <b>, …) is preserved
                    # verbatim — dropping it could turn enclosed text into a
                    # line-start command (P2 round 16).
                    if _is_container_only(child.content):
                        _apply_container_tags(child.content, html_containers)
                        continue
                    visible_spans.append(child.content)
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
                span_text = "".join(visible_spans)
                parts.append(span_text)
                src_start = token.map[0] if token.map else 0
                src_end = token.map[1] if token.map and len(token.map) > 1 else src_start + 1
                spans.append((span_text, body.splitlines()[src_start:src_end]))
        # fences, code blocks, html blocks emit no inline tokens — dropped
    result = "\n".join(parts)
    # Escape/entity sanitation (rounds 16-26): each visible SPAN is verified
    # against its OWN source lines (the paragraph token's .map) and sanitized
    # at its tracked offset — never located by content search. Verification
    # covers the COMMAND WORD plus recognized FLAGS (entity-decoded flags
    # must not survive); unrelated trailing prose (normalized '&' etc.) is
    # not required to be byte-identical.
    spans_with_offsets: list[tuple[str, list[str], int]] = []
    offset = 0
    for span_text, src_lines in spans:
        spans_with_offsets.append((span_text, src_lines, offset))
        offset += len(span_text) + 1  # +1 for the joining newline
    removed = 0  # total characters removed so far — later offsets adjust
    for span_text, src_lines, base in spans_with_offsets:
        src_lines_list = list(src_lines)
        for match in re.finditer(r"/(?:assembly|implement|stack|blueprint)\b", span_text, re.I):
            cmd = str(match.group(0))
            nl = span_text.find("\n", match.start())
            cmd_line = span_text[match.start() : nl if nl > 0 else len(span_text)].rstrip()
            flags = " ".join(tok for tok in cmd_line.split()[1:] if tok.startswith("--"))
            verify = f"{cmd} {flags}".strip() if flags else cmd
            # Verify against THIS occurrence's own source LINE (the paragraph
            # line whose softbreak position matches the span's line), not the
            # whole span: a harmless literal on an earlier line cannot
            # approve a decoded occurrence on a later one.
            span_line = span_text.count("\n", 0, match.start())
            own_src = (
                src_lines_list[span_line]
                if span_line < len(src_lines_list)
                else "\n".join(src_lines_list)
            )
            if (
                _line_has_live_token(own_src, verify)
                or _line_has_live_token(" ".join(own_src.split()), verify)
                or f"`{cmd}" in own_src
            ):
                continue  # live, or inside an inline code span (prose intent)
            pos = base + match.start() - removed
            result = result[:pos] + result[pos + len(cmd) :]
            removed += len(cmd)
    return result


def _line_has_live_token(source_line: str, token: str) -> bool:
    """True when this raw source line carries the token live (unescaped,
    not entity-encoded)."""
    boundary = r"\b" if token and token[-1].isalnum() else ""
    return bool(re.search(rf"(?<![\\\w`/&;]){re.escape(token)}{boundary}", source_line, re.I))


_COMMAND_WORDS = ("/assembly", "/implement", "/stack", "/blueprint")


def _source_has_live_line(body: str, line: str) -> bool:
    """True when the raw source has a line-start command line matching the
    reconstructed one (case-insensitive, escapes count as absent)."""
    target = line.lower().replace("\\", "")
    for raw_line in body.splitlines():
        stripped2 = raw_line.lstrip()
        if stripped2.startswith("\\"):
            continue  # escaped in source: not live
        if stripped2.lower().rstrip() == target.rstrip():
            return True
    return False
