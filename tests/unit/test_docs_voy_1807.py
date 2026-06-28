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


def test_voy_1807_user_to_server_route_fails_closed_without_plaintext_recovery():
    text = Path("rules/VOY-1807-REF-GitHub-App-Registry.md").read_text(encoding="utf-8")
    route_row_match = re.search(
        r"(?m)^\| User-to-server refresh route \|[^\n]*$",
        text,
    )
    assert route_row_match is not None, "VOY-1807 missing user-to-server route row"
    route_row = route_row_match.group(0)

    assert "--expected-viewer-login-env" in route_row
    assert "--repository-id" in route_row
    assert "viewer_login_matches_expected" in route_row
    assert "fails closed" in route_row
    assert "does not write plaintext token material" in route_row
    assert "countdown-refresh-token" not in route_row
    assert "~/.voyager/recovery" not in route_row
    assert "recovery file" not in route_row.lower()


def test_voy_1807_countdown_registry_row_mentions_scheduled_resolve_loop():
    text = Path("rules/VOY-1807-REF-GitHub-App-Registry.md").read_text(encoding="utf-8")
    rows = re.findall(r"(?m)^\| `iterwheel-countdown` \|[^\n]*$", text)
    row = next((candidate for candidate in rows if "resolve" in candidate), None)

    assert row is not None, "VOY-1807 missing iterwheel-countdown resolver registry row"

    assert "Scheduled `vyg countdown resolve-loop`" in row
    assert "Manual `vyg countdown resolve-conversation`" in row
    assert "VOY-1835" in row
    assert "iterwheel-countdown-bot" in row


def test_voy_1807_user_to_server_route_records_live_refresh_evidence():
    text = Path("rules/VOY-1807-REF-GitHub-App-Registry.md").read_text(encoding="utf-8")
    route_row_match = re.search(
        r"(?m)^\| User-to-server refresh route \|[^\n]*$",
        text,
    )
    assert route_row_match is not None, "VOY-1807 missing user-to-server route row"
    route_row = route_row_match.group(0)

    assert "Verified for non-repository-scoped Device Flow" in route_row
    assert "live authorization pending operator" not in route_row
    assert "v0.7.3" in route_row
    assert "user-refresh-check" in route_row
    assert "refreshed successfully" in route_row
    assert "No plaintext token artifacts" in route_row
    assert "repository-scoped refresh token continued to return GitHub HTTP 500" in route_row
    assert "client secret" in route_row
    assert "repository id" in route_row
