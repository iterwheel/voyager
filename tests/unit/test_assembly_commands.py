"""Unit tests for Assembly command parsing (VOY-1817 Surface 13)."""

from __future__ import annotations

import pytest

from voyager.bots.assembly.commands import parse_assembly_command


def test_assembly_command_matches() -> None:
    cmd = parse_assembly_command("/assembly")
    assert cmd is not None
    assert cmd.command == "/assembly"
    assert cmd.dry_run is False
    assert cmd.allow_missing_stack is False


def test_implement_command_is_equivalent() -> None:
    cmd = parse_assembly_command("/implement")
    assert cmd is not None
    assert cmd.command == "/implement"


def test_command_is_case_insensitive() -> None:
    cmd = parse_assembly_command("/Assembly")
    assert cmd is not None
    assert cmd.command == "/assembly"


def test_command_must_start_line_not_mid_text() -> None:
    """text /assembly later — must NOT trigger Assembly."""
    assert parse_assembly_command("text /assembly later") is None


def test_command_with_leading_whitespace_matches() -> None:
    cmd = parse_assembly_command("    /assembly")
    assert cmd is not None


def test_command_on_subsequent_line_matches() -> None:
    cmd = parse_assembly_command("hello there\n/implement --dry-run")
    assert cmd is not None
    assert cmd.command == "/implement"
    assert cmd.dry_run is True


def test_dry_run_flag_parsed() -> None:
    cmd = parse_assembly_command("/assembly --dry-run")
    assert cmd is not None
    assert cmd.dry_run is True
    assert cmd.allow_missing_stack is False


def test_allow_missing_stack_flag_parsed() -> None:
    cmd = parse_assembly_command("/assembly --allow-missing-stack")
    assert cmd is not None
    assert cmd.allow_missing_stack is True
    assert cmd.dry_run is False


def test_both_flags_parsed_either_order() -> None:
    cmd = parse_assembly_command("/assembly --dry-run --allow-missing-stack")
    assert cmd is not None
    assert cmd.dry_run is True
    assert cmd.allow_missing_stack is True

    cmd2 = parse_assembly_command("/assembly --allow-missing-stack --dry-run")
    assert cmd2 is not None
    assert cmd2.dry_run is True
    assert cmd2.allow_missing_stack is True


def test_unknown_flag_does_not_set_known_flags() -> None:
    cmd = parse_assembly_command("/assembly --some-other-flag")
    assert cmd is not None
    assert cmd.dry_run is False
    assert cmd.allow_missing_stack is False


def test_unrelated_body_returns_none() -> None:
    assert parse_assembly_command("just chatting") is None
    assert parse_assembly_command("/stack") is None
    assert parse_assembly_command("/blueprint") is None


@pytest.mark.parametrize("body", ["", None])
def test_empty_or_none_returns_none(body: str | None) -> None:
    assert parse_assembly_command(body) is None
