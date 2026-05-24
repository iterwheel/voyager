"""Unit tests for the Assembly Job Contract (VOY-1817 Surface 16)."""

from __future__ import annotations

from voyager.bots.assembly.constants import (
    ASSEMBLY_VERIFICATION_COMMANDS_ENV,
    FORBIDDEN_OPERATIONS,
    VERIFICATION_COMMANDS,
)
from voyager.bots.assembly.job_contract import build_job_contract

_ISSUE_BODY = """## Problem / Goal

Implement Assembly bot to write code from blueprint-ready issues.

## Acceptance Criteria

- [ ] Assembly responds to /assembly on blueprint-ready issues
- [ ] Refuses on non-allow-listed repos
- [ ] Posts @codex review after every push
"""


def _build() -> dict:
    contract = build_job_contract(
        issue={
            "number": 69,
            "title": "[Feature]: Implement Assembly bot MVP",
            "html_url": "https://github.com/iterwheel/voyager-sandbox/issues/69",
            "body": _ISSUE_BODY,
        },
        repository="iterwheel/voyager-sandbox",
        branch_name="69-implement-assembly-bot-mvp",
        delivery_id="abc-123",
    )
    return contract.to_dict()


def test_contract_fields_match_schema() -> None:
    data = _build()
    assert data["repository"] == "iterwheel/voyager-sandbox"
    assert data["issue_number"] == 69
    assert data["issue_url"].endswith("/issues/69")
    assert data["branch_name"] == "69-implement-assembly-bot-mvp"
    assert data["base_branch"] == "main"
    assert data["delivery_id"] == "abc-123"
    assert data["requested_at"]  # iso utc string present


def test_forbidden_operations_match_canonical_constant() -> None:
    """Per D9: contract surfaces VOY-1805 §5 Deny column verbatim."""
    data = _build()
    assert tuple(data["forbidden_operations"]) == FORBIDDEN_OPERATIONS
    assert "Merge pull requests" in data["forbidden_operations"]
    assert "Approve its own pull requests" in data["forbidden_operations"]
    assert "Resolve review threads as a reviewer" in data["forbidden_operations"]
    assert (
        "Apply `clearance-4-ready-for-merge` or `countdown-go` labels"
        in data["forbidden_operations"]
    )


def test_verification_commands_present() -> None:
    data = _build()
    assert tuple(data["verification_commands"]) == VERIFICATION_COMMANDS
    assert "pytest tests/" in data["verification_commands"]


def test_verification_commands_can_be_overridden_per_repository(monkeypatch) -> None:
    monkeypatch.setenv(
        "ASSEMBLY_VERIFICATION_COMMANDS_FRANKYXHL__TRINITY",
        "make verify-built;;af validate --root .",
    )
    monkeypatch.setenv(ASSEMBLY_VERIFICATION_COMMANDS_ENV, "should not run")
    contract = build_job_contract(
        issue={
            "number": 79,
            "title": "[Refactor]: Manifest-driven install file lists",
            "html_url": "https://github.com/frankyxhl/trinity/issues/79",
            "body": _ISSUE_BODY,
        },
        repository="frankyxhl/trinity",
        branch_name="79-manifest-driven-install-file-lists",
        delivery_id="abc-123",
    ).to_dict()
    assert tuple(contract["verification_commands"]) == (
        "make verify-built",
        "af validate --root .",
    )


def test_verification_commands_can_be_overridden_globally(monkeypatch) -> None:
    monkeypatch.setenv(
        ASSEMBLY_VERIFICATION_COMMANDS_ENV,
        "make verify-built\naf validate --root .",
    )
    contract = build_job_contract(
        issue={
            "number": 79,
            "title": "[Refactor]: Manifest-driven install file lists",
            "html_url": "https://github.com/frankyxhl/trinity/issues/79",
            "body": _ISSUE_BODY,
        },
        repository="frankyxhl/trinity",
        branch_name="79-manifest-driven-install-file-lists",
        delivery_id="abc-123",
    ).to_dict()
    assert tuple(contract["verification_commands"]) == (
        "make verify-built",
        "af validate --root .",
    )


def test_verification_commands_empty_repository_override_is_explicit(monkeypatch) -> None:
    monkeypatch.setenv("ASSEMBLY_VERIFICATION_COMMANDS_FRANKYXHL__TRINITY", "")
    contract = build_job_contract(
        issue={
            "number": 79,
            "title": "[Refactor]: Manifest-driven install file lists",
            "html_url": "https://github.com/frankyxhl/trinity/issues/79",
            "body": _ISSUE_BODY,
        },
        repository="frankyxhl/trinity",
        branch_name="79-manifest-driven-install-file-lists",
        delivery_id="abc-123",
    ).to_dict()
    assert tuple(contract["verification_commands"]) == ()


def test_verification_commands_repository_keys_do_not_collide(monkeypatch) -> None:
    monkeypatch.setenv(
        "ASSEMBLY_VERIFICATION_COMMANDS_FOO_DBAR__BAZ",
        "make verify-built",
    )
    monkeypatch.setenv(
        "ASSEMBLY_VERIFICATION_COMMANDS_FOO__BAR_UBAZ",
        "af validate --root .",
    )
    hyphen_contract = build_job_contract(
        issue={
            "number": 1,
            "title": "A",
            "html_url": "https://example/issues/1",
            "body": _ISSUE_BODY,
        },
        repository="foo-bar/baz",
        branch_name="1-a",
        delivery_id="abc-123",
    ).to_dict()
    underscore_contract = build_job_contract(
        issue={
            "number": 2,
            "title": "B",
            "html_url": "https://example/issues/2",
            "body": _ISSUE_BODY,
        },
        repository="foo/bar_baz",
        branch_name="2-b",
        delivery_id="abc-123",
    ).to_dict()
    assert tuple(hyphen_contract["verification_commands"]) == ("make verify-built",)
    assert tuple(underscore_contract["verification_commands"]) == ("af validate --root .",)


def test_acceptance_criteria_extracted_from_bullets() -> None:
    data = _build()
    assert data["acceptance_criteria_source"] == "section"
    assert any("Assembly responds to /assembly" in item for item in data["acceptance_criteria"])
    assert len(data["acceptance_criteria"]) == 3


def test_task_summary_extracted_from_section() -> None:
    data = _build()
    assert data["task_summary_source"] == "section"
    assert "Assembly" in data["task_summary"]


def test_d14_fallback_when_sections_missing() -> None:
    """D14: when AC / Problem sections are absent, fall back to title."""
    contract = build_job_contract(
        issue={
            "number": 70,
            "title": "[Bug]: Crash on startup",
            "html_url": "https://example/issues/70",
            "body": "Just a free-form description, no sections.",
        },
        repository="iterwheel/voyager-sandbox",
        branch_name="70-crash-on-startup",
        delivery_id="d",
    ).to_dict()
    assert contract["acceptance_criteria_source"] == "title_fallback"
    assert contract["acceptance_criteria"] == ["[Bug]: Crash on startup"]
    assert contract["task_summary_source"] == "title_fallback"
    assert contract["task_summary"] == "[Bug]: Crash on startup"


def test_to_dict_returns_list_for_tuples() -> None:
    data = _build()
    assert isinstance(data["forbidden_operations"], list)
    assert isinstance(data["verification_commands"], list)


def test_acceptance_criteria_empty_when_title_and_body_empty() -> None:
    """CHG-1819 F4 / D7 / D8: when BOTH the body's AC section AND the title
    are empty, ``acceptance_criteria`` must be ``[]`` (NOT ``[""]``) and
    ``acceptance_criteria_source`` must be ``"empty_fallback"``.

    Rationale (D7): the downstream comment renderer iterates
    ``acceptance_criteria`` and bullets each entry. ``[""]`` renders as a
    blank bullet (visible empty-bullet bug); ``[]`` renders as no bullets
    (honest "no data"). The new ``empty_fallback`` source string lets the
    audit log distinguish "title-derived" from "no-data" cases without
    re-deriving from the contract body.

    Asymmetry guard (D8): the matching ``_extract_task_summary`` branch is
    intentionally NOT changed — an empty ``task_summary`` renders
    harmlessly ("Task summary: " disappears under markdown), while an
    empty-bulleted criterion looks like a Blueprint failure. Pin the
    asymmetry here so a future cleanup that unifies both fallbacks under
    one helper makes a deliberate, reviewer-visible choice rather than a
    silent change in behavior.
    """
    contract = build_job_contract(
        issue={
            "number": 91,
            "title": "",
            "html_url": "https://example/issues/91",
            "body": "",
        },
        repository="iterwheel/voyager-sandbox",
        branch_name="91-empty",
        delivery_id="d",
    ).to_dict()
    # F4 / D7 — acceptance_criteria branch changed to empty_fallback.
    assert contract["acceptance_criteria"] == []
    assert contract["acceptance_criteria_source"] == "empty_fallback"
    # D8 asymmetry guard — task_summary still falls back to the title (the
    # empty string here) and tags itself ``title_fallback``.  If a future
    # refactor changes this without a corresponding CHG, the assertion
    # below fires and surfaces the omission.
    assert contract["task_summary"] == ""
    assert contract["task_summary_source"] == "title_fallback"


def test_section_extractor_returns_first_matching_block_only() -> None:
    """Codex round-3 P2: when an issue body repeats a matching heading
    (e.g. a quoted template appended below the real section), the
    extractor must return only the FIRST matching block — not the merged
    concatenation of every matching block."""
    body = """## Problem / Goal

The real summary line.

## Acceptance Criteria

- [ ] Real criterion one
- [ ] Real criterion two

---

> Quoted from the issue template for reference:
>
## Acceptance Criteria

- [ ] Template placeholder
- [ ] Should not appear in the contract
"""
    contract = build_job_contract(
        issue={
            "number": 90,
            "title": "[Feature]: Repeated heading test",
            "html_url": "https://example/issues/90",
            "body": body,
        },
        repository="iterwheel/voyager-sandbox",
        branch_name="90-repeated-heading",
        delivery_id="d",
    ).to_dict()
    assert contract["acceptance_criteria_source"] == "section"
    # Only the first block's bullets should appear
    assert contract["acceptance_criteria"] == [
        "Real criterion one",
        "Real criterion two",
    ]
    assert "Template placeholder" not in " ".join(contract["acceptance_criteria"])
