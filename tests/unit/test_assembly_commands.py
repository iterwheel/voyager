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
    assert cmd.resume is False


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
    assert cmd.resume is False


def test_resume_flag_parsed() -> None:
    cmd = parse_assembly_command("/assembly --resume")
    assert cmd is not None
    assert cmd.resume is True
    assert cmd.dry_run is False
    assert cmd.allow_missing_stack is False


def test_both_flags_parsed_either_order() -> None:
    cmd = parse_assembly_command("/assembly --dry-run --allow-missing-stack")
    assert cmd is not None
    assert cmd.dry_run is True
    assert cmd.allow_missing_stack is True

    cmd2 = parse_assembly_command("/assembly --allow-missing-stack --dry-run")
    assert cmd2 is not None
    assert cmd2.dry_run is True
    assert cmd2.allow_missing_stack is True
    assert cmd2.resume is False


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


# ---------------------------------------------------------------------------
# CHG-1819 Surface 8 (F2, part a) — parser MUST NOT emit a ``backend`` key.
# ---------------------------------------------------------------------------
#
# Upstream regression gate: ``parse_assembly_command`` only recognizes
# ``--dry-run`` and ``--allow-missing-stack``.  No invocation — even one
# containing the literal substring ``--backend foo`` — may surface a
# ``backend`` field on the returned ``AssemblyCommand``, because the
# dispatcher's backend selection is env-only (``ASSEMBLY_EXECUTION_BACKEND``)
# per VOY-1817 D3 and CHG-1819 F2.  Wiring ``--backend`` as a real flag is
# explicitly deferred (CHG-1819 §Out of Scope).


@pytest.mark.parametrize(
    "body",
    [
        "/assembly",
        "/assembly --dry-run",
        "/assembly --allow-missing-stack",
        "/assembly --dry-run --allow-missing-stack",
        "/assembly --resume",
        "/assembly --dry-run --resume",
        # Adversarial: a flag-shaped substring that mimics the deferred
        # ``--backend`` feature.  Must NOT surface as a parsed field.
        "/assembly --backend pi-oh-my-pi-deepseek",
        "/assembly --dry-run --backend pi-oh-my-pi-deepseek",
        "/implement --dry-run --allow-missing-stack",
        "/implement --backend dry-run",
    ],
)
def test_parse_command_never_emits_backend_key(body: str) -> None:
    """F2 (part a): every parsed command's serialized fields expose
    EXACTLY ``{"dry_run", "allow_missing_stack", "resume"}`` for flag-shaped state.

    The check has three layers:
      1. The frozen dataclass has no ``backend`` attribute.
      2. ``dataclasses.asdict`` never contains a ``backend`` key.
      3. The downstream ``command_flags`` dict (built by ``routing.py``
         as ``{"dry_run": cmd.dry_run, "allow_missing_stack":
         cmd.allow_missing_stack, "resume": cmd.resume}``) has exactly that key set.
    """
    from dataclasses import asdict

    cmd = parse_assembly_command(body)
    assert cmd is not None, body

    # (1) — attribute-level check.
    assert not hasattr(cmd, "backend"), (
        f"AssemblyCommand for {body!r} unexpectedly grew a `backend` attr"
    )

    # (2) — serialization-level check.
    serialized = asdict(cmd)
    assert "backend" not in serialized, (
        f"asdict() for {body!r} surfaced a `backend` key: {serialized!r}"
    )

    # (3) — downstream `command_flags` dict shape gate (matches
    # voyager/bots/assembly/routing.py:56 and :140).  This is the wire
    # format consumed by the dispatcher; it MUST contain exactly the two
    # known flags, regardless of what extra `--foo` tokens appeared in
    # the body.
    command_flags = {
        "dry_run": cmd.dry_run,
        "allow_missing_stack": cmd.allow_missing_stack,
        "resume": cmd.resume,
    }
    assert set(command_flags.keys()) == {"dry_run", "allow_missing_stack", "resume"}, (
        f"command_flags set for {body!r}: {set(command_flags.keys())!r}"
    )
