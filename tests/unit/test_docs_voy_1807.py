"""Doc-level test: VOY-1807 GitHub App Registry must reference all numbered clearance labels."""

from __future__ import annotations

import re
from pathlib import Path


def test_voy_1807_references_new_clearance_labels():
    text = Path("rules/VOY-1807-REF-GitHub-App-Registry.md").read_text(encoding="utf-8")
    for label in (
        "clearance-1-pending",
        "clearance-2-blocked",
        "clearance-3-ready-for-approval",
        "clearance-4-ready-for-merge",
    ):
        assert label in text, f"VOY-1807 missing {label}"


def test_voy_1807_main_registry_table_uses_numbered_labels():
    """VOY-1807 §Operational Labels (main registry table) must list the 4 numbered
    labels. Legacy names may appear ONLY in the explicit migration sub-section.
    Codex round-2 delta review finding (PR #25 panel)."""
    text = Path("rules/VOY-1807-REF-GitHub-App-Registry.md").read_text(encoding="utf-8")

    # Split on ## headings to isolate each top-level section body.
    # We include the heading text so we can find the right section.
    sections = re.split(r"(?m)^## ", text)
    # sections[0] is the frontmatter before any ## heading; sections[1..] start with the heading text.

    # Locate the section that describes the bridge write-back table (contains "iterwheel-clearance").
    bridge_section = None
    for section in sections[1:]:
        heading = section.split("\n", 1)[0].strip().lower()
        if (
            ("iterwheel-clearance" in section and "clearance" in heading)
            or ("clearance" not in heading and "iterwheel-clearance" in section[:500])
        ) and ("content" in heading or "iterwheel-clearance" in section[:300]):
            # The Content section contains the bridge table
            bridge_section = section

    # Fallback: use "Content" or the section containing the clearance write-back row.
    if bridge_section is None:
        for section in sections[1:]:
            if "iterwheel-clearance" in section:
                bridge_section = section
                break

    assert bridge_section is not None, (
        "Could not locate the main registry table section in VOY-1807"
    )

    numbered_labels = (
        "clearance-1-pending",
        "clearance-2-blocked",
        "clearance-3-ready-for-approval",
        "clearance-4-ready-for-merge",
    )
    legacy_labels = (
        "clearance-pending",
        "clearance-blocked",
        "clearance-ready",
    )

    # The bridge write-back row for iterwheel-clearance must list numbered labels.
    # The bridge write-back table is identifiable by having "Trigger" or "Write-back" columns.
    # Find the table that describes what each agent writes back (has "Write-back" in header).
    writeback_table_match = re.search(
        r"(?m)^\|[^\n]*Write-back[^\n]*\n[^\n]*\n((?:\|[^\n]*\n)+)",
        bridge_section,
    )
    assert writeback_table_match is not None, (
        "Could not find the bridge write-back table (with 'Write-back' column) in VOY-1807"
    )
    writeback_table_rows = writeback_table_match.group(1)

    # Find the clearance row within the write-back table
    clearance_row_match = re.search(
        r"\|\s*`iterwheel-clearance`\s*\|[^\n]*",
        writeback_table_rows,
    )
    assert clearance_row_match is not None, (
        "Could not find the iterwheel-clearance row in the bridge write-back table"
    )
    clearance_row = clearance_row_match.group(0)

    for label in numbered_labels:
        assert label in clearance_row, (
            f"Main registry table clearance row missing numbered label {label!r}; "
            f"row text: {clearance_row!r}"
        )

    for label in legacy_labels:
        # Legacy labels must NOT appear in the clearance row of the main table.
        # They are only allowed in the migration sub-section.
        # We match the bare label name but exclude the numbered variant:
        # e.g. "clearance-pending" but not "clearance-1-pending".
        # Use a word-boundary-style check: label must not be preceded/followed by digit or letter
        # that would make it part of a numbered label name.
        pattern = re.compile(r"(?<!\d-)(?<!\w)" + re.escape(label) + r"(?!\w|-\w)")
        assert not pattern.search(clearance_row), (
            f"Legacy label {label!r} found in main registry clearance row — "
            f"it must only appear in the migration sub-section. Row: {clearance_row!r}"
        )
