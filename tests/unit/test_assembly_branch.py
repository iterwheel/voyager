"""Unit tests for Assembly branch naming (VOY-1817 Surface 15)."""

from __future__ import annotations

from voyager.bots.assembly.branch import make_branch_name
from voyager.bots.assembly.constants import MAX_SLUG_LENGTH


def test_typical_blueprint_title_produces_short_kebab_slug() -> None:
    assert make_branch_name(69, "[Feature]: Implement Assembly Bot MVP") == (
        "69-implement-assembly-bot-mvp"
    )


def test_branch_naming_is_deterministic_idempotent() -> None:
    a = make_branch_name(42, "[Task]: Add SOP for Liftoff")
    b = make_branch_name(42, "[Task]: Add SOP for Liftoff")
    assert a == b == "42-add-sop-for-liftoff"


def test_empty_slug_falls_back_to_issue() -> None:
    """[Bug]: 🚨🚨🚨 — slug after ASCII fold is empty per D8."""
    assert make_branch_name(99, "[Bug]: 🚨🚨🚨") == "99-issue"


def test_pure_emoji_title_falls_back() -> None:
    assert make_branch_name(100, "🚀🚀🚀") == "100-issue"


def test_none_title_falls_back() -> None:
    assert make_branch_name(1, None) == "1-issue"


def test_unicode_is_ascii_folded() -> None:
    # Combining accents collapse into the base letter.
    assert make_branch_name(7, "[Docs]: añade café résumé").endswith("anade-cafe-resume")


def test_slug_is_length_capped() -> None:
    long_title = "[Feature]: " + "abcd " * 30
    name = make_branch_name(123, long_title)
    issue_prefix, slug = name.split("-", 1)
    assert issue_prefix == "123"
    assert len(slug) <= MAX_SLUG_LENGTH
    assert not slug.endswith("-")


def test_bracketed_kind_prefix_is_stripped() -> None:
    name = make_branch_name(8, "[Refactor]: Tidy Assembly module")
    assert name == "8-tidy-assembly-module"


def test_special_characters_collapse_into_single_hyphen() -> None:
    name = make_branch_name(2, "[Task]: foo // bar -- baz")
    assert name == "2-foo-bar-baz"
