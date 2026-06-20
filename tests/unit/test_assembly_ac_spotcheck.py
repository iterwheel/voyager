"""Unit tests for Assembly acceptance-criteria exact-token spot checks."""

from __future__ import annotations

from voyager.bots.assembly.ac_spotcheck import (
    BLOCKING_FINDING_DIRECTION,
    check_acceptance_exact_tokens,
)


def test_spotcheck_catches_alfred_204_disposition_value_mismatch() -> None:
    issue_body = """## Expected Outcome

- **COR-side field** `**Disposition:**` registered in COR-0002, with three values and concrete per-value criteria:
  - `mandatory-bind` — adopting project MUST create a PRJ instance.
  - `optional-overlay` — project MAY create a PRJ instance.
  - `inherit-only` — use as-is; a PRJ instance is FORBIDDEN.

## Acceptance Criteria

- [ ] COR-0002 registers `**Disposition:**` with values `mandatory-bind`, `optional-overlay`, and `inherit-only`
- [ ] COR-0002 registers `**Instantiates:**` / `**Overlays:**` with format `COR-NNNN`
"""
    changed_text = """
DISPOSITION_CORE = "core"
DISPOSITION_OPTIONAL_OVERLAY = "optional-overlay"
DISPOSITION_LOCALIZATION_REQUIRED = "localization-required"

The `**Disposition:**` field is registered.
The `**Instantiates:**` and `**Overlays:**` fields use `COR-NNNN`.
"""

    result = check_acceptance_exact_tokens(
        issue_body=issue_body,
        acceptance_criteria=[
            "COR-0002 registers `**Disposition:**` with values `mandatory-bind`, `optional-overlay`, and `inherit-only`",
            "COR-0002 registers `**Instantiates:**` / `**Overlays:**` with format `COR-NNNN`",
        ],
        changed_text=changed_text,
    )

    assert not result.ok
    value_group = next(
        finding for finding in result.findings if "mandatory-bind" in finding.required_tokens
    )
    assert value_group.required_tokens == (
        "Disposition",
        "mandatory-bind",
        "optional-overlay",
        "inherit-only",
    )
    assert value_group.missing_tokens == ("mandatory-bind", "inherit-only")
    assert value_group.direction == BLOCKING_FINDING_DIRECTION


def test_spotcheck_required_token_missing_finding_is_blocking() -> None:
    result = check_acceptance_exact_tokens(
        issue_body="## Acceptance Criteria\n\n- [ ] Add value `mandatory-bind`\n",
        acceptance_criteria=["Add value `mandatory-bind`"],
        changed_text='SUPPORTED_VALUE = "optional-overlay"\n',
    )

    assert not result.ok
    assert result.findings[0].direction == BLOCKING_FINDING_DIRECTION
    assert result.to_dict()["findings"][0]["direction"] == BLOCKING_FINDING_DIRECTION


def test_spotcheck_direction_does_not_follow_blocking_prose_keywords() -> None:
    result = check_acceptance_exact_tokens(
        issue_body=(
            "## Acceptance Criteria\n\n"
            "- [ ] Must document false negative under-blocking guard that lets "
            "`new-mode` through\n"
        ),
        acceptance_criteria=[
            "Must document false negative under-blocking guard that lets `new-mode` through"
        ],
        changed_text="No matching exact token is present.\n",
    )

    assert not result.ok
    assert result.findings[0].missing_tokens == ("new-mode",)
    assert result.findings[0].direction == BLOCKING_FINDING_DIRECTION


def test_spotcheck_ignores_value_groups_outside_acceptance_criteria() -> None:
    issue_body = """## Expected Outcome

The document describes three values:
- `mandatory-bind`
- `optional-overlay`
- `inherit-only`

## Acceptance Criteria

- [ ] COR-0002 registers `**Disposition:**`
"""

    result = check_acceptance_exact_tokens(
        issue_body=issue_body,
        acceptance_criteria=["COR-0002 registers `**Disposition:**`"],
        changed_text="The `**Disposition:**` field is registered.",
    )

    assert result.ok


def test_spotcheck_ignores_removal_criteria_tokens() -> None:
    result = check_acceptance_exact_tokens(
        issue_body="## Acceptance Criteria\n\n- [ ] Remove deprecated `legacy-mode` value\n",
        acceptance_criteria=["Remove deprecated `legacy-mode` value"],
        changed_text="SUPPORTED_VALUES = ['modern-mode']",
    )

    assert result.ok


def test_spotcheck_ignores_do_not_criteria_tokens() -> None:
    result = check_acceptance_exact_tokens(
        issue_body="## Acceptance Criteria\n\n- [ ] Do not add `legacy-mode`\n",
        acceptance_criteria=["Do not add `legacy-mode`"],
        changed_text="SUPPORTED_VALUES = ['modern-mode']",
    )

    assert result.ok


def test_spotcheck_ignores_forbidden_value_criteria_tokens() -> None:
    result = check_acceptance_exact_tokens(
        issue_body=(
            "## Acceptance Criteria\n\n"
            "- [ ] Add `modern-mode`\n"
            "- [ ] The `legacy-mode` value is `FORBIDDEN`\n"
        ),
        acceptance_criteria=[
            "Add `modern-mode`",
            "The `legacy-mode` value is `FORBIDDEN`",
        ],
        changed_text="SUPPORTED_VALUES = ['modern-mode']",
    )

    assert result.ok


def test_spotcheck_does_not_match_tokens_inside_longer_values() -> None:
    result = check_acceptance_exact_tokens(
        issue_body="## Acceptance Criteria\n\n- [ ] Add value `mandatory-bind`\n",
        acceptance_criteria=["Add value `mandatory-bind`"],
        changed_text='SUPPORTED_VALUE = "mandatory-binding"\n',
    )

    assert not result.ok
    assert result.findings[0].missing_tokens == ("mandatory-bind",)


def test_spotcheck_does_not_match_colon_tokens_inside_longer_values() -> None:
    result = check_acceptance_exact_tokens(
        issue_body="## Acceptance Criteria\n\n- [ ] Add permission `scope:read`\n",
        acceptance_criteria=["Add permission `scope:read`"],
        changed_text='PERMISSIONS = ["scope:read:all"]\n',
    )

    assert not result.ok
    assert result.findings[0].missing_tokens == ("scope:read",)


def test_spotcheck_keeps_value_lists_with_forbidden_prose_checkable() -> None:
    issue_body = """## Acceptance Criteria

- [ ] Register values:
  - `mandatory-bind` — adopting project MUST create a PRJ instance.
  - `optional-overlay` — project MAY create a PRJ instance.
  - `inherit-only` — PRJ instance is FORBIDDEN.
"""

    result = check_acceptance_exact_tokens(
        issue_body=issue_body,
        acceptance_criteria=[
            "Register values:",
            "`mandatory-bind` — adopting project MUST create a PRJ instance.",
            "`optional-overlay` — project MAY create a PRJ instance.",
            "`inherit-only` — PRJ instance is FORBIDDEN.",
        ],
        changed_text='SUPPORTED_VALUES = ["mandatory-bind", "optional-overlay"]',
    )

    assert not result.ok
    value_group = next(
        finding for finding in result.findings if "inherit-only" in finding.required_tokens
    )
    assert value_group.missing_tokens == ("inherit-only",)


def test_spotcheck_ignores_removed_value_lists() -> None:
    result = check_acceptance_exact_tokens(
        issue_body=(
            "## Acceptance Criteria\n\n- [ ] Remove deprecated values: `legacy-mode`, `old-mode`\n"
        ),
        acceptance_criteria=["Remove deprecated values: `legacy-mode`, `old-mode`"],
        changed_text='SUPPORTED_VALUES = ["modern-mode"]',
    )

    assert result.ok


def test_spotcheck_skips_value_groups_started_by_removal_headings() -> None:
    issue_body = """## Acceptance Criteria

- [ ] Remove deprecated values:
  - `legacy-mode`
  - `old-mode`
- [ ] Add `new-mode`
"""

    result = check_acceptance_exact_tokens(
        issue_body=issue_body,
        acceptance_criteria=[
            "Remove deprecated values:",
            "`legacy-mode`",
            "`old-mode`",
            "Add `new-mode`",
        ],
        acceptance_criteria_items=[
            {"text": "Remove deprecated values:", "depth": 0},
            {"text": "`legacy-mode`", "depth": 1},
            {"text": "`old-mode`", "depth": 1},
            {"text": "Add `new-mode`", "depth": 0},
        ],
        changed_text='SUPPORTED_VALUES = ["new-mode"]',
    )

    assert result.ok


def test_spotcheck_applies_removal_context_to_nested_value_list_children() -> None:
    issue_body = """## Acceptance Criteria

- [ ] Remove deprecated values:
  - `legacy-mode`
  - `old-mode`
- [ ] Add `new-mode`
"""

    result = check_acceptance_exact_tokens(
        issue_body=issue_body,
        acceptance_criteria=[
            "Remove deprecated values:",
            "`legacy-mode`",
            "`old-mode`",
            "Add `new-mode`",
        ],
        changed_text='SUPPORTED_VALUES = ["modern-mode"]',
    )

    assert not result.ok
    assert result.findings[0].required_tokens == ("new-mode",)
    assert result.findings[0].missing_tokens == ("new-mode",)


def test_spotcheck_does_not_apply_removal_context_to_sibling_criteria() -> None:
    issue_body = """## Acceptance Criteria

- [ ] Remove deprecated values:
  - `legacy-mode`
- [ ] Document `new-mode` behavior
"""

    result = check_acceptance_exact_tokens(
        issue_body=issue_body,
        acceptance_criteria=[
            "Remove deprecated values:",
            "`legacy-mode`",
            "Document `new-mode` behavior",
        ],
        changed_text='SUPPORTED_VALUES = ["modern-mode"]',
    )

    assert not result.ok
    assert result.findings[0].required_tokens == ("new-mode",)
    assert result.findings[0].missing_tokens == ("new-mode",)


def test_spotcheck_keeps_unlisted_verb_sibling_required_from_structural_items() -> None:
    result = check_acceptance_exact_tokens(
        issue_body="",
        acceptance_criteria=[
            "Remove deprecated values:",
            "`legacy-mode`",
            "Audit `new-mode` behavior",
        ],
        acceptance_criteria_items=[
            {"text": "Remove deprecated values:", "depth": 0},
            {"text": "`legacy-mode`", "depth": 1},
            {"text": "Audit `new-mode` behavior", "depth": 0},
        ],
        changed_text='SUPPORTED_VALUES = ["modern-mode"]',
    )

    assert not result.ok
    assert result.findings[0].required_tokens == ("new-mode",)
    assert result.findings[0].missing_tokens == ("new-mode",)


def test_spotcheck_applies_removal_context_to_children_without_verb_allow_list() -> None:
    result = check_acceptance_exact_tokens(
        issue_body="",
        acceptance_criteria=[
            "Remove deprecated values:",
            "`legacy-mode` removal note",
        ],
        acceptance_criteria_items=[
            {"text": "Remove deprecated values:", "depth": 0},
            {"text": "`legacy-mode` removal note", "depth": 1},
        ],
        changed_text='SUPPORTED_VALUES = ["modern-mode"]',
    )

    assert result.ok


def test_spotcheck_keeps_required_children_under_removal_headings_required() -> None:
    result = check_acceptance_exact_tokens(
        issue_body="",
        acceptance_criteria=[
            "Remove deprecated modes:",
            "Add `new-mode`",
        ],
        acceptance_criteria_items=[
            {"text": "Remove deprecated modes:", "depth": 0},
            {"text": "Add `new-mode`", "depth": 1},
        ],
        changed_text='SUPPORTED_VALUES = ["modern-mode"]',
    )

    assert not result.ok
    assert result.findings[0].required_tokens == ("new-mode",)
    assert result.findings[0].missing_tokens == ("new-mode",)


def test_spotcheck_applies_removal_context_to_labeled_children() -> None:
    for child in (
        "legacy mode: `legacy-mode`",
        "legacy mode `legacy-mode`",
        "legacy value: `old-mode`",
        "default: `old-mode`",
        "api/v1: `legacy-mode`",
        "allow-list `legacy-mode`",
        "allow list: `legacy-mode`",
        "audit log: `legacy-mode`",
        "support matrix: `legacy-mode`",
    ):
        result = check_acceptance_exact_tokens(
            issue_body="",
            acceptance_criteria=[
                "Remove deprecated values:",
                child,
            ],
            acceptance_criteria_items=[
                {"text": "Remove deprecated values:", "depth": 0},
                {"text": child, "depth": 1},
            ],
            changed_text='SUPPORTED_VALUES = ["modern-mode"]',
        )

        assert result.ok


def test_spotcheck_keeps_required_action_labels_under_removal_headings_required() -> None:
    for child in (
        "Add mode: `new-mode`",
        "Add mode `new-mode`",
        "Add api/v1: `new-mode`",
        "Add/support `new-mode`",
        "Support mode: `new-mode`",
        "Audit mode: `new-mode`",
        "Describe mode: `new-mode`",
        "Must support `new-mode`",
        "The implementation must add `new-mode`",
        "Tests verify `new-mode`",
        "Integration tests validate `new-mode`",
        "Test suite validates `new-mode`",
    ):
        result = check_acceptance_exact_tokens(
            issue_body="",
            acceptance_criteria=[
                "Remove deprecated modes:",
                child,
            ],
            acceptance_criteria_items=[
                {"text": "Remove deprecated modes:", "depth": 0},
                {"text": child, "depth": 1},
            ],
            changed_text='SUPPORTED_VALUES = ["modern-mode"]',
        )

        assert not result.ok
        assert result.findings[0].required_tokens == ("new-mode",)
        assert result.findings[0].missing_tokens == ("new-mode",)


def test_spotcheck_stops_removal_context_at_required_child_headings() -> None:
    result = check_acceptance_exact_tokens(
        issue_body="",
        acceptance_criteria=[
            "Remove deprecated values:",
            "Add replacement values:",
            "`new-mode`",
        ],
        acceptance_criteria_items=[
            {"text": "Remove deprecated values:", "depth": 0},
            {"text": "Add replacement values:", "depth": 1},
            {"text": "`new-mode`", "depth": 2},
        ],
        changed_text='SUPPORTED_VALUES = ["modern-mode"]',
    )

    assert not result.ok
    assert result.findings[0].required_tokens == ("new-mode",)
    assert result.findings[0].missing_tokens == ("new-mode",)


def test_spotcheck_stops_removal_context_at_subject_required_headings() -> None:
    result = check_acceptance_exact_tokens(
        issue_body="",
        acceptance_criteria=[
            "Remove deprecated values:",
            "Tests verify replacement values:",
            "`new-mode`",
        ],
        acceptance_criteria_items=[
            {"text": "Remove deprecated values:", "depth": 0},
            {"text": "Tests verify replacement values:", "depth": 1},
            {"text": "`new-mode`", "depth": 2},
        ],
        changed_text='SUPPORTED_VALUES = ["modern-mode"]',
    )

    assert not result.ok
    assert result.findings[0].required_tokens == ("new-mode",)
    assert result.findings[0].missing_tokens == ("new-mode",)


def test_spotcheck_matches_values_colon_headings_in_value_groups() -> None:
    issue_body = """## Acceptance Criteria

- [ ] Register values:
  `mandatory-bind`, `optional-overlay`, and `inherit-only`
"""

    result = check_acceptance_exact_tokens(
        issue_body=issue_body,
        acceptance_criteria=["Register values:"],
        changed_text='SUPPORTED_VALUES = ["optional-overlay"]',
    )

    assert not result.ok
    value_group = next(
        finding for finding in result.findings if "mandatory-bind" in finding.required_tokens
    )
    assert value_group.required_tokens == (
        "mandatory-bind",
        "optional-overlay",
        "inherit-only",
    )
    assert value_group.missing_tokens == ("mandatory-bind", "inherit-only")


def test_spotcheck_keeps_mixed_removal_value_groups_checkable() -> None:
    for action in ("register", "document"):
        issue_body = f"""## Acceptance Criteria

- [ ] Remove legacy table and {action} values:
  `mandatory-bind`, `optional-overlay`
"""

        result = check_acceptance_exact_tokens(
            issue_body=issue_body,
            acceptance_criteria=[f"Remove legacy table and {action} values:"],
            acceptance_criteria_items=[
                {"text": f"Remove legacy table and {action} values:", "depth": 0},
            ],
            changed_text='SUPPORTED_VALUES = ["inherit-only"]',
        )

        assert not result.ok
        value_group = next(
            finding for finding in result.findings if "mandatory-bind" in finding.required_tokens
        )
        assert value_group.required_tokens == ("mandatory-bind", "optional-overlay")
        assert value_group.missing_tokens == ("mandatory-bind", "optional-overlay")


def test_spotcheck_keeps_replacement_value_groups_under_removal_headings_checkable() -> None:
    issue_body = """## Acceptance Criteria

- [ ] Remove deprecated values:
  Add replacement values:
  `new-mode`, `modern-mode`
"""

    result = check_acceptance_exact_tokens(
        issue_body=issue_body,
        acceptance_criteria=["Remove deprecated values:"],
        acceptance_criteria_items=[
            {"text": "Remove deprecated values:", "depth": 0},
        ],
        changed_text='SUPPORTED_VALUES = ["legacy-mode"]',
    )

    assert not result.ok
    value_group = next(
        finding for finding in result.findings if "new-mode" in finding.required_tokens
    )
    assert value_group.required_tokens == ("new-mode", "modern-mode")
    assert value_group.missing_tokens == ("new-mode", "modern-mode")


def test_spotcheck_keeps_neutral_replacement_value_groups_checkable() -> None:
    issue_body = """## Acceptance Criteria

- [ ] Remove deprecated values:
  Replacement:
  `new-mode`, `modern-mode`
"""

    result = check_acceptance_exact_tokens(
        issue_body=issue_body,
        acceptance_criteria=["Remove deprecated values:"],
        acceptance_criteria_items=[
            {"text": "Remove deprecated values:", "depth": 0},
        ],
        changed_text='SUPPORTED_VALUES = ["legacy-mode"]',
    )

    assert not result.ok
    value_group = next(
        finding for finding in result.findings if "new-mode" in finding.required_tokens
    )
    assert value_group.required_tokens == ("new-mode", "modern-mode")
    assert value_group.missing_tokens == ("new-mode", "modern-mode")


def test_spotcheck_keeps_same_line_replacement_values_checkable() -> None:
    issue_body = """## Acceptance Criteria

- [ ] Remove deprecated values:
  Replacement: `new-mode`, `modern-mode`
"""

    result = check_acceptance_exact_tokens(
        issue_body=issue_body,
        acceptance_criteria=["Remove deprecated values:"],
        acceptance_criteria_items=[
            {"text": "Remove deprecated values:", "depth": 0},
        ],
        changed_text='SUPPORTED_VALUES = ["legacy-mode"]',
    )

    assert not result.ok
    value_group = next(
        finding for finding in result.findings if "new-mode" in finding.required_tokens
    )
    assert value_group.required_tokens == ("new-mode", "modern-mode")
    assert value_group.missing_tokens == ("new-mode", "modern-mode")


def test_spotcheck_keeps_bullet_replacement_values_checkable() -> None:
    issue_body = """## Acceptance Criteria

- [ ] Remove deprecated values:
  Replacement:
  - `new-mode`
  - `modern-mode`
"""

    result = check_acceptance_exact_tokens(
        issue_body=issue_body,
        acceptance_criteria=["Remove deprecated values:"],
        acceptance_criteria_items=[
            {"text": "Remove deprecated values:", "depth": 0},
        ],
        changed_text='SUPPORTED_VALUES = ["new-mode"]',
    )

    assert not result.ok
    value_group = next(
        finding for finding in result.findings if "new-mode" in finding.required_tokens
    )
    assert value_group.required_tokens == ("new-mode", "modern-mode")
    assert value_group.missing_tokens == ("modern-mode",)


def test_spotcheck_filters_removed_children_from_replacement_value_groups() -> None:
    issue_body = """## Acceptance Criteria

- [ ] Remove deprecated values:
  - `legacy-mode`
  - `old-mode`
  Replacement:
  `new-mode`, `modern-mode`
"""

    result = check_acceptance_exact_tokens(
        issue_body=issue_body,
        acceptance_criteria=["Remove deprecated values:"],
        acceptance_criteria_items=[
            {"text": "Remove deprecated values:", "depth": 0},
        ],
        changed_text='SUPPORTED_VALUES = ["new-mode"]',
    )

    assert not result.ok
    value_group = next(
        finding for finding in result.findings if "new-mode" in finding.required_tokens
    )
    assert value_group.required_tokens == ("new-mode", "modern-mode")
    assert value_group.missing_tokens == ("modern-mode",)


def test_spotcheck_filters_removed_continuations_from_replacement_value_groups() -> None:
    issue_body = """## Acceptance Criteria

- [ ] Remove deprecated values:
  `legacy-mode`, `old-mode`
  Replacement:
  `new-mode`, `modern-mode`
"""

    result = check_acceptance_exact_tokens(
        issue_body=issue_body,
        acceptance_criteria=["Remove deprecated values:"],
        acceptance_criteria_items=[
            {"text": "Remove deprecated values:", "depth": 0},
        ],
        changed_text='SUPPORTED_VALUES = ["new-mode"]',
    )

    assert not result.ok
    value_group = next(
        finding for finding in result.findings if "new-mode" in finding.required_tokens
    )
    assert value_group.required_tokens == ("new-mode", "modern-mode")
    assert value_group.missing_tokens == ("modern-mode",)


def test_spotcheck_filters_removed_value_lines_without_skipping_required_values() -> None:
    issue_body = """## Acceptance Criteria

- [ ] Register values:
  - `mandatory-bind` — adopting project MUST create a PRJ instance.
  - Remove deprecated `legacy-mode`.
"""

    result = check_acceptance_exact_tokens(
        issue_body=issue_body,
        acceptance_criteria=[
            "Register values:",
            "`mandatory-bind` — adopting project MUST create a PRJ instance.",
            "Remove deprecated `legacy-mode`.",
        ],
        changed_text='SUPPORTED_VALUES = ["modern-mode"]',
    )

    assert not result.ok
    value_group = next(
        finding for finding in result.findings if "mandatory-bind" in finding.required_tokens
    )
    assert value_group.required_tokens == ("mandatory-bind",)
    assert value_group.missing_tokens == ("mandatory-bind",)


def test_spotcheck_keeps_replacement_targets_required_in_mixed_criteria() -> None:
    result = check_acceptance_exact_tokens(
        issue_body="## Acceptance Criteria\n\n- [ ] Remove `old-mode` and add `new-mode`\n",
        acceptance_criteria=["Remove `old-mode` and add `new-mode`"],
        changed_text='SUPPORTED_VALUES = ["modern-mode"]',
    )

    assert not result.ok
    assert result.findings[0].required_tokens == ("new-mode",)
    assert result.findings[0].missing_tokens == ("new-mode",)


def test_spotcheck_ignores_change_update_sources_but_requires_targets() -> None:
    result = check_acceptance_exact_tokens(
        issue_body=(
            "## Acceptance Criteria\n\n"
            "- [ ] Change `old-mode` to `new-mode`\n"
            "- [ ] Update `legacy-mode` to `modern-mode`\n"
        ),
        acceptance_criteria=[
            "Change `old-mode` to `new-mode`",
            "Update `legacy-mode` to `modern-mode`",
        ],
        changed_text='SUPPORTED_VALUES = ["modern-mode"]',
    )

    assert not result.ok
    assert result.findings[0].required_tokens == ("new-mode",)
    assert result.findings[0].missing_tokens == ("new-mode",)


def test_spotcheck_resets_removal_scope_after_punctuation_separated_adds() -> None:
    for criterion in (
        "Remove `old-mode`; add `new-mode`",
        "Remove `old-mode`, add `new-mode`",
    ):
        result = check_acceptance_exact_tokens(
            issue_body=f"## Acceptance Criteria\n\n- [ ] {criterion}\n",
            acceptance_criteria=[criterion],
            changed_text='SUPPORTED_VALUES = ["modern-mode"]',
        )

        assert not result.ok
        assert result.findings[0].required_tokens == ("new-mode",)
        assert result.findings[0].missing_tokens == ("new-mode",)


def test_spotcheck_passes_when_exact_tokens_are_present() -> None:
    result = check_acceptance_exact_tokens(
        issue_body="## Acceptance Criteria\n\n- [ ] Document `**Overlays:**` with format `COR-NNNN`\n",
        acceptance_criteria=["Document `**Overlays:**` with format `COR-NNNN`"],
        changed_text="The `**Overlays:**` field uses the `COR-NNNN` format.",
    )

    assert result.ok
