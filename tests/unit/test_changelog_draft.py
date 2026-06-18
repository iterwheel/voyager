from __future__ import annotations

import asyncio
from typing import Any

from voyager.bots.changelog import (
    append_unreleased_bullet,
    build_changelog_bullet,
    is_changelog_relevant,
    route_changelog_event,
)
from voyager.core.writeback import dispatch_route_writeback


def _pull_request_payload(
    *,
    labels: list[str],
    merged: bool = True,
    base_ref: str = "main",
    head_ref: str = "feature/changelog-source",
    title: str = "Add export workflow",
) -> dict[str, Any]:
    return {
        "action": "closed",
        "number": 123,
        "repository": {"full_name": "iterwheel/voyager"},
        "pull_request": {
            "number": 123,
            "title": title,
            "html_url": "https://github.com/iterwheel/voyager/pull/123",
            "merged": merged,
            "base": {"ref": base_ref},
            "head": {"ref": head_ref},
            "labels": [{"name": name} for name in labels],
        },
    }


def test_changelog_relevance_requires_matching_label_and_honors_skip() -> None:
    assert is_changelog_relevant([{"name": "enhancement"}]) is True
    assert is_changelog_relevant([{"name": "stack-type-feature"}]) is True
    assert is_changelog_relevant([{"name": "bug"}, {"name": "chore"}]) is False
    assert is_changelog_relevant([{"name": "stack-type-bug"}, {"name": "stack-type-docs"}]) is False


def test_route_changelog_event_for_merged_labeled_pr() -> None:
    routes = route_changelog_event(
        "pull_request",
        _pull_request_payload(labels=["enhancement"]),
    )

    assert len(routes) == 1
    route = routes[0]
    assert route["agent"] == "iterwheel-assembly"
    assert route["kind"] == "changelog_draft"
    assert route["validation"]["status"] == "changelog_ready"
    assert route["writeback"]["dynamic"] == "changelog_draft"
    assert route["writeback"]["draft"]["branch_name"] == "changelog/pr-123-unreleased"
    assert (
        route["writeback"]["draft"]["bullet"]
        == "- Add export workflow ([#123](https://github.com/iterwheel/voyager/pull/123))."
    )


def test_route_changelog_event_ignores_unmerged_skip_and_self_generated_prs() -> None:
    assert (
        route_changelog_event(
            "pull_request",
            _pull_request_payload(labels=["enhancement"], merged=False),
        )
        == []
    )
    assert (
        route_changelog_event(
            "pull_request",
            _pull_request_payload(labels=["bug"], base_ref="release"),
        )
        == []
    )
    assert (
        route_changelog_event(
            "pull_request",
            _pull_request_payload(labels=["chore", "bug"]),
        )
        == []
    )
    assert (
        route_changelog_event(
            "pull_request",
            _pull_request_payload(
                labels=["enhancement"],
                head_ref="changelog/pr-122-unreleased",
                title="chore(changelog): draft entry for #122",
            ),
        )
        == []
    )


def test_append_unreleased_bullet_before_next_version() -> None:
    text = """# Changelog

## [Unreleased]

## [0.1.0]

- Existing release note.
"""
    result = append_unreleased_bullet(
        text,
        bullet="- Add export workflow ([#123](https://github.com/iterwheel/voyager/pull/123)).",
        source_pr_number=123,
    )

    assert result.changed is True
    assert result.reason is None
    assert (
        "## [Unreleased]\n"
        "- Add export workflow ([#123](https://github.com/iterwheel/voyager/pull/123)).\n\n"
        "## [0.1.0]"
    ) in result.text


def test_append_unreleased_bullet_is_idempotent_for_source_pr() -> None:
    text = """# Changelog

## [Unreleased]

- Add export workflow ([#123](https://github.com/iterwheel/voyager/pull/123)).

## [0.1.0]
"""
    result = append_unreleased_bullet(
        text,
        bullet="- Add export workflow ([#123](https://github.com/iterwheel/voyager/pull/123)).",
        source_pr_number=123,
    )

    assert result.changed is False
    assert result.reason == "already_present"
    assert result.text == text


def test_append_unreleased_bullet_does_not_treat_prefix_pr_url_as_duplicate() -> None:
    text = """# Changelog

## [Unreleased]

- Later work (https://github.com/iterwheel/voyager/pull/123).

## [0.1.0]
"""
    result = append_unreleased_bullet(
        text,
        bullet="- Earlier work ([#12](https://github.com/iterwheel/voyager/pull/12)).",
        source_pr_number=12,
    )

    assert result.changed is True
    assert "Earlier work" in result.text


def test_append_unreleased_bullet_matches_source_pr_url_with_path_boundary() -> None:
    text = """# Changelog

## [Unreleased]

- Earlier work (https://github.com/iterwheel/voyager/pull/12/files).

## [0.1.0]
"""
    result = append_unreleased_bullet(
        text,
        bullet="- Earlier work ([#12](https://github.com/iterwheel/voyager/pull/12)).",
        source_pr_number=12,
    )

    assert result.changed is False
    assert result.reason == "already_present"


def test_simulated_labeled_merge_produces_expected_unreleased_bullet() -> None:
    routes = route_changelog_event(
        "pull_request",
        _pull_request_payload(labels=["bug"], title="Fix release gate"),
    )
    bullet = routes[0]["writeback"]["draft"]["bullet"]
    result = append_unreleased_bullet(
        "# Changelog\n\n## [Unreleased]\n\n## [0.1.0]\n",
        bullet=bullet,
        source_pr_number=123,
    )

    assert result.changed is True
    assert "- Fix release gate ([#123](https://github.com/iterwheel/voyager/pull/123))." in (
        result.text
    )


def test_dispatch_route_writeback_for_changelog_dry_run(monkeypatch) -> None:
    monkeypatch.setenv("DRY_RUN", "true")
    route = route_changelog_event(
        "pull_request",
        _pull_request_payload(labels=["enhancement"]),
    )[0]

    result = asyncio.run(
        dispatch_route_writeback(
            object(),
            route,
            repository="iterwheel/voyager",
        )
    )

    assert result["applied"] is False
    assert result["dry_run"] is True
    assert result["planned"]["branch"] == "changelog/pr-123-unreleased"


def test_build_changelog_bullet_normalizes_title_spacing_and_punctuation() -> None:
    assert (
        build_changelog_bullet(
            pr_number=7,
            pr_title="  Add   export workflow.  ",
            pr_url="https://github.com/iterwheel/voyager/pull/7",
        )
        == "- Add export workflow ([#7](https://github.com/iterwheel/voyager/pull/7))."
    )
