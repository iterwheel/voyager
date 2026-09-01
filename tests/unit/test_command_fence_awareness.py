"""Issue #256: slash-command and trigger detection must ignore quoted content.

A maintainer documenting ``/assembly`` usage in a fenced code block, or pasting
a log containing a ``/assembly`` line, must not trigger a real run. Same for
the ``/stack`` and ``/blueprint`` substring triggers. ``visible_comment_text``
strips fenced blocks and block quotes; all three triggers match only against
the visible prose.
"""

from __future__ import annotations

from voyager.bots.assembly.commands import parse_assembly_command
from voyager.bots.assembly.constants import ASSEMBLY_COMMANDS  # noqa: F401 — sanity import
from voyager.bots.blueprint import should_run_blueprint
from voyager.bots.stack import should_run_stack
from voyager.core.comment_text import visible_comment_text


def _comment(body: str) -> dict:
    return {"action": "created", "comment": {"body": body}, "issue": {"pull_request": {}}}


# ---------------------------------------------------------------------------
# visible_comment_text
# ---------------------------------------------------------------------------


def test_fenced_blocks_are_stripped():
    body = "before\n```python\n/assembly --dry-run\n```\nafter"
    assert "/assembly" not in visible_comment_text(body)
    assert "before" in visible_comment_text(body)
    assert "after" in visible_comment_text(body)


def test_tilde_fences_and_longer_markers():
    assert "/stack" not in visible_comment_text("~~~\n/stack\n~~~")
    assert "/stack" not in visible_comment_text("````\n``` \n/stack\n```\n````")


def test_closing_fence_must_be_bare():
    """Codex P1: a same-length marker with an info string does NOT close a
    fence - the rest of the block stays invisible."""
    body = "```\n/assembly --dry-run\n```python\nstill inside the fence\n```"
    visible = visible_comment_text(body)
    assert "/assembly" not in visible
    assert "still inside the fence" not in visible


def test_indented_code_blocks_are_stripped():
    """Codex P1: 4-space-indented documentation blocks cannot fire."""
    assert "/assembly" not in visible_comment_text("Usage:\n\n    /assembly --dry-run\n")


def test_lazy_quote_continuation_is_stripped():
    """Codex P1: Markdown lazy continuation - the un-prefixed line after a
    quote line renders inside the same quoted paragraph."""
    body = "> the docs say run this\n/assembly --dry-run"
    assert "/assembly" not in visible_comment_text(body)
    # A blank line separates: the following prose is visible again.
    body2 = "> quoted\n\n/assembly --dry-run"
    assert "/assembly" in visible_comment_text(body2)


def test_block_quotes_are_stripped():
    assert "/blueprint" not in visible_comment_text("> /blueprint\n> more quote")
    assert "/blueprint" in visible_comment_text("please run /blueprint")


def test_inline_code_spans_survive():
    assert "/assembly" in visible_comment_text("run `/assembly` now please")


# ---------------------------------------------------------------------------
# /assembly command parsing
# ---------------------------------------------------------------------------


def test_assembly_command_inside_fence_is_ignored():
    body = "Usage:\n```bash\n/assembly --dry-run\n```"
    assert parse_assembly_command(body) is None


def test_assembly_command_in_block_quote_is_ignored():
    assert parse_assembly_command("> /assembly") is None


def test_assembly_command_in_prose_still_parses():
    cmd = parse_assembly_command("/assembly --dry-run")
    assert cmd is not None
    assert cmd.dry_run is True


def test_assembly_command_line_start_still_required_in_visible_text():
    # Line-start anchoring is unchanged — only fenced/quoted regions are gone.
    assert parse_assembly_command("run `/assembly --dry-run` when ready") is None
    assert parse_assembly_command("/assembly --dry-run") is not None


# ---------------------------------------------------------------------------
# /stack and /blueprint routing
# ---------------------------------------------------------------------------


def test_stack_trigger_inside_fence_is_ignored():
    assert should_run_stack("issue_comment", _comment('```json\n"cmd": "/stack"\n```')) is False
    assert should_run_stack("issue_comment", _comment("please /stack this")) is True


def test_blueprint_trigger_inside_fence_is_ignored():
    assert should_run_blueprint("issue_comment", _comment("```\n/blueprint\n```")) is False
    assert should_run_blueprint("issue_comment", _comment("/blueprint please")) is True


def test_stack_trigger_in_quoted_reply_is_ignored():
    assert should_run_stack("issue_comment", _comment("> old comment said /stack\nagreed")) is False


def test_html_comments_are_stripped():
    """Codex P1 round 3: hidden HTML-comment content cannot carry a command."""
    assert "/assembly" not in visible_comment_text("<!--\n/assembly --dry-run\n-->")
    assert "/stack" not in visible_comment_text("<!-- example: /stack -->")
    assert "/stack" in visible_comment_text("please run /stack <!-- todo -->")


def test_indented_code_after_heading_is_stripped():
    """Codex P1 round 3: heading + 4-space indent is an indented code block."""
    assert "/assembly" not in visible_comment_text("# Usage\n    /assembly --dry-run")
    # Parser-delegated contract (#336 class-closing): document-start
    # indentation is an indented code block too (CommonMark).
    assert "/assembly" not in visible_comment_text("    /assembly")


def test_unterminated_html_comment_strips_to_end():
    """Parser-delegated contract (#336 class-closing): CommonMark HTML blocks
    (comments) must START the line to hide content; a mid-paragraph '<!--' is
    inline HTML and the following command stays visible prose."""
    assert "/assembly" not in visible_comment_text("<!-- hidden\n/assembly --dry-run")
    assert "/assembly" in visible_comment_text("intro <!-- hidden\n/assembly --dry-run")


def test_malformed_hash_line_continues_quote():
    assert "/assembly" not in visible_comment_text("> quoted\n#tag /assembly")


def test_fence_close_rearms_indented_code():
    assert "/assembly" not in visible_comment_text("```\ninside\n```\n    /assembly --dry-run")


def test_deep_indented_hash_is_indented_code_not_heading():
    # After a blank-line boundary, a deep-indented '#' line is indented code.
    assert "# note" not in visible_comment_text("intro\n\n    # note inside indented block")
    # Document-start indented lines stay visible (standing parser contract),
    # and a deep-indented '#' does NOT arm the heading block-boundary rule.
    assert "/assembly" in visible_comment_text("    # note\n/assembly --dry-run")


def test_fences_nested_in_list_items_are_stripped():
    """Codex P1 round 7: a fenced sample as the first block of a list item
    is a code block — documentation in lists cannot carry commands."""
    body = "- ```bash\n  /assembly --dry-run\n  ```"
    assert "/assembly" not in visible_comment_text(body)


def test_html_opener_inside_inline_code_is_literal():
    """Codex P2 round 7: a literal `<!--` in an inline code span does not
    start a comment — the following command stays visible."""
    body = "Literal `<!--` marker.\n/assembly --dry-run"
    assert "/assembly" in visible_comment_text(body)
    # A real unterminated comment still hides the rest.
    assert "/assembly" not in visible_comment_text("<!-- hidden\n/assembly --dry-run")


def test_malformed_hash_paragraph_does_not_arm_indented_code():
    """Codex P2 round 7: '#tag' is paragraph text, not an ATX heading — the
    indented continuation stays visible."""
    assert "/assembly" in visible_comment_text("#tag\n    /assembly --dry-run")
    assert "/assembly" not in visible_comment_text("## Usage\n    /assembly --dry-run")
