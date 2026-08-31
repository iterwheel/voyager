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
