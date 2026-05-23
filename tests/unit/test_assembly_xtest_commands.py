"""Cross-test for Assembly command parsing — independent scenarios.

Covers: multi-line bodies, leading whitespace, prose mixing,
case sensitivity, /implement alternate command.
"""

from __future__ import annotations

from voyager.bots.assembly.commands import parse_assembly_command


class TestBasicCommandMatching:
    def test_assembly_bare(self) -> None:
        cmd = parse_assembly_command("/assembly")
        assert cmd is not None
        assert cmd.command == "/assembly"
        assert cmd.dry_run is False
        assert cmd.allow_missing_stack is False

    def test_implement_bare(self) -> None:
        cmd = parse_assembly_command("/implement")
        assert cmd is not None
        assert cmd.command == "/implement"
        assert cmd.dry_run is False
        assert cmd.allow_missing_stack is False


class TestCaseInsensitivity:
    def test_uppercase_assembly(self) -> None:
        cmd = parse_assembly_command("/ASSEMBLY")
        assert cmd is not None
        assert cmd.command == "/assembly"

    def test_mixed_case_assembly(self) -> None:
        cmd = parse_assembly_command("/Assembly")
        assert cmd is not None
        assert cmd.command == "/assembly"

    def test_uppercase_implement(self) -> None:
        cmd = parse_assembly_command("/IMPLEMENT")
        assert cmd is not None
        assert cmd.command == "/implement"

    def test_case_insensitive_flags(self) -> None:
        cmd = parse_assembly_command("/assembly --Dry-Run")
        assert cmd is not None
        assert cmd.dry_run is True

    def test_case_insensitive_allow_missing(self) -> None:
        cmd = parse_assembly_command("/assembly --Allow-Missing-Stack")
        assert cmd is not None
        assert cmd.allow_missing_stack is True


class TestLeadingWhitespace:
    def test_tab_indent(self) -> None:
        cmd = parse_assembly_command("\t/assembly")
        assert cmd is not None
        assert cmd.command == "/assembly"

    def test_space_indent(self) -> None:
        cmd = parse_assembly_command("   /assembly")
        assert cmd is not None
        assert cmd.command == "/assembly"

    def test_mixed_indent(self) -> None:
        cmd = parse_assembly_command(" \t  /implement")
        assert cmd is not None
        assert cmd.command == "/implement"


class TestFlags:
    def test_dry_run_flag(self) -> None:
        cmd = parse_assembly_command("/assembly --dry-run")
        assert cmd is not None
        assert cmd.dry_run is True
        assert cmd.allow_missing_stack is False

    def test_allow_missing_stack_flag(self) -> None:
        cmd = parse_assembly_command("/assembly --allow-missing-stack")
        assert cmd is not None
        assert cmd.allow_missing_stack is True
        assert cmd.dry_run is False

    def test_both_flags(self) -> None:
        cmd = parse_assembly_command("/assembly --dry-run --allow-missing-stack")
        assert cmd is not None
        assert cmd.dry_run is True
        assert cmd.allow_missing_stack is True

    def test_both_flags_reversed_order(self) -> None:
        cmd = parse_assembly_command("/implement --allow-missing-stack --dry-run")
        assert cmd is not None
        assert cmd.dry_run is True
        assert cmd.allow_missing_stack is True

    def test_flag_with_extra_text(self) -> None:
        cmd = parse_assembly_command("/assembly --dry-run please implement this")
        assert cmd is not None
        assert cmd.dry_run is True


class TestMultiLineBodies:
    def test_command_on_second_line(self) -> None:
        cmd = parse_assembly_command("thanks for the review!\n/assembly")
        assert cmd is not None
        assert cmd.command == "/assembly"

    def test_command_on_first_line_prose_after(self) -> None:
        cmd = parse_assembly_command("/assembly\nsome extra explanation below")
        assert cmd is not None
        assert cmd.command == "/assembly"

    def test_command_surrounded_by_prose(self) -> None:
        cmd = parse_assembly_command("intro text\n/assembly\nmore text below")
        assert cmd is not None
        assert cmd.command == "/assembly"

    def test_first_matching_line_wins(self) -> None:
        cmd = parse_assembly_command("/assembly\n/implement")
        assert cmd is not None
        assert cmd.command == "/assembly"


class TestCommandWithInlineProse:
    def test_command_followed_by_prose(self) -> None:
        cmd = parse_assembly_command("/assembly please implement the feature")
        assert cmd is not None
        assert cmd.command == "/assembly"

    def test_command_with_flags_and_prose(self) -> None:
        cmd = parse_assembly_command("/assembly --dry-run please do this")
        assert cmd is not None
        assert cmd.command == "/assembly"
        assert cmd.dry_run is True


class TestNonMatching:
    def test_command_not_at_line_start(self) -> None:
        assert parse_assembly_command("please run /assembly for me") is None

    def test_command_mid_sentence(self) -> None:
        assert parse_assembly_command("I think /assembly should run") is None

    def test_no_command(self) -> None:
        assert parse_assembly_command("just a regular comment") is None

    def test_empty_string(self) -> None:
        assert parse_assembly_command("") is None

    def test_none_body(self) -> None:
        assert parse_assembly_command(None) is None

    def test_only_whitespace(self) -> None:
        assert parse_assembly_command("   \n  \n  ") is None

    def test_unrelated_slash_command(self) -> None:
        assert parse_assembly_command("/stack") is None

    def test_slash_in_middle_of_line(self) -> None:
        assert parse_assembly_command("deploy/assembly ready") is None
