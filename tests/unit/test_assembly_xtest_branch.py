"""Cross-test for Assembly branch naming — independent edge cases.

Covers: very long titles, emoji-only titles (empty-slug fallback per D8),
leading numbers, repeated-call idempotency.  Does not duplicate GLM's
unicode-normalization coverage.
"""

from __future__ import annotations

from voyager.bots.assembly.branch import make_branch_name


class TestNormalTitles:
    def test_standard_title(self) -> None:
        name = make_branch_name(42, "[Feature]: Add user authentication")
        assert name == "42-add-user-authentication"

    def test_title_without_kind_prefix(self) -> None:
        name = make_branch_name(7, "Fix login timeout bug")
        assert name == "7-fix-login-timeout-bug"


class TestVeryLongTitles:
    def test_title_exceeding_50_char_slug(self) -> None:
        long_title = (
            "Implement a comprehensive refactoring of the entire "
            "authentication subsystem including OAuth2, SAML, and "
            "WebAuthn passkey support across all client platforms"
        )
        name = make_branch_name(101, long_title)
        prefix, slug = name.split("-", 1)
        assert prefix == "101"
        assert len(slug) <= 50

    def test_long_title_does_not_end_with_hyphen(self) -> None:
        long_title = "A" * 60
        name = make_branch_name(1, long_title)
        assert not name.endswith("-")
        assert name.startswith("1-a")

    def test_slug_truncation_preserves_readable_prefix(self) -> None:
        long_title = (
            "Implement-the-quick-brown-fox-jumps-over-the-lazy-dog-and-then-some-more-words"
        )
        name = make_branch_name(200, long_title)
        assert name.startswith("200-implement-the-quick-brown-fox-jumps-over-the")
        # The exact length depends on kebab folding; just check it's capped
        slug = name.split("-", 1)[1]
        assert len(slug) <= 50


class TestEmojiOnlyTitles:
    def test_only_emojis_falls_back(self) -> None:
        name = make_branch_name(99, "🚨🚨🚨")
        assert name == "99-issue"

    def test_emoji_with_brackets_falls_back(self) -> None:
        name = make_branch_name(42, "[Bug]: 🚨🚨🚨")
        assert name == "42-issue"

    def test_mixed_emoji_and_ascii(self) -> None:
        name = make_branch_name(10, "Fix 🐛 in login")
        # Emoji stripped, "fix in login" -> "fix-in-login"
        assert "fix-in-login" in name


class TestLeadingNumbers:
    def test_leading_number_in_title(self) -> None:
        name = make_branch_name(5, "2026 Q1 roadmap planning")
        assert name == "5-2026-q1-roadmap-planning"

    def test_only_numbers(self) -> None:
        name = make_branch_name(3, "12345")
        assert name.startswith("3-")

    def test_numbers_with_special_chars(self) -> None:
        name = make_branch_name(8, "Version 2.0 release checklist")
        assert "version-2-0-release-checklist" in name


class TestIdempotency:
    def test_same_input_same_output(self) -> None:
        title = "[Feature]: Implement Assembly bot MVP"
        a = make_branch_name(69, title)
        b = make_branch_name(69, title)
        assert a == b

    def test_repeated_calls_identical(self) -> None:
        title = "Fix: handle edge case in URL parser"
        results = {make_branch_name(12, title) for _ in range(100)}
        assert len(results) == 1

    def test_different_numbers_produce_different_names(self) -> None:
        title = "Add logging"
        a = make_branch_name(1, title)
        b = make_branch_name(2, title)
        assert a != b


class TestSpecialCharacters:
    def test_special_chars_collapsed(self) -> None:
        name = make_branch_name(1, "Fix: login / signup flow")
        assert "fix-login-signup-flow" in name

    def test_multiple_hyphens_collapsed(self) -> None:
        name = make_branch_name(1, "Add --verbose flag support")
        assert "add-verbose-flag-support" in name

    def test_unicode_accents_folded(self) -> None:
        name = make_branch_name(1, "René's café order form")
        assert "cafe-order-form" in name

    def test_none_title(self) -> None:
        name = make_branch_name(42, None)
        assert name == "42-issue"
