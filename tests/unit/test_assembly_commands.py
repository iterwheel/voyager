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


@pytest.mark.parametrize(
    "body",
    [
        "/assemblyx",
        "/assembly-now",
        "/assembly_run",
        "/implementation",
        "/implement-now",
        "/implementor",
    ],
)
def test_token_boundary_rejects_extended_command_names(body: str) -> None:
    """Codex round-4 P1: command parser must NOT match prefixes that extend
    /assembly or /implement with extra characters. A typo like /assemblyx
    must not trigger Assembly in production."""
    assert parse_assembly_command(body) is None


@pytest.mark.parametrize(
    "body",
    [
        "/assembly",
        "/assembly ",
        "/assembly --dry-run",
        "/assembly\t--allow-missing-stack",
        "/implement",
        "/implement --dry-run --allow-missing-stack",
    ],
)
def test_token_boundary_accepts_legitimate_invocations(body: str) -> None:
    assert parse_assembly_command(body) is not None


@pytest.mark.parametrize(
    "body",
    [
        "/assembly\r\n",
        "/assembly\r\nsecond line",
        "first line\r\n/assembly\r\nthird line",
        "/implement --dry-run\r\n",
        "/assembly\r",  # bare CR with no LF (defensive)
    ],
)
def test_crlf_line_endings_accepted(body: str) -> None:
    """Codex round-5 P1: GitHub webhook bodies often use CRLF; the
    command parser must accept /assembly even when the line ends with
    \\r\\n, not just \\n."""
    assert parse_assembly_command(body) is not None
