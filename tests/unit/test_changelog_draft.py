from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import voyager.bots.changelog.writeback as changelog_writeback
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
    assert route["agent"] == "iterwheel-changelog"
    assert route["kind"] == "changelog_draft"
    assert route["validation"]["status"] == "changelog_ready"
    assert route["writeback"]["dynamic"] == "changelog_draft"
    assert route["writeback"]["draft"]["branch_name"] == "changelog/pr-123-unreleased"
    assert (
        route["writeback"]["draft"]["bullet"]
        == "- Add export workflow ([#123](https://github.com/iterwheel/voyager/pull/123))."
    )


def test_route_changelog_event_allows_unlabeled_shippable_titles() -> None:
    routes = route_changelog_event(
        "pull_request",
        _pull_request_payload(labels=[], title="[task]: Add export workflow"),
    )

    assert len(routes) == 1
    assert routes[0]["agent"] == "iterwheel-changelog"
    assert routes[0]["writeback"]["draft"]["labels"] == []


def test_route_changelog_event_honors_skip_labels_for_shippable_titles() -> None:
    routes = route_changelog_event(
        "pull_request",
        _pull_request_payload(labels=["changelog-skip"], title="[task]: Add export workflow"),
    )

    assert routes == []


def test_changelog_route_uses_distinct_allowlist_gate(monkeypatch) -> None:
    from voyager.server import _filter_routes_by_repository

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.delenv("BRIDGE_ALLOWED_REPOSITORIES", raising=False)
    monkeypatch.delenv("BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CHANGELOG", raising=False)
    monkeypatch.setenv("BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_ASSEMBLY", "iterwheel/voyager")
    routes = route_changelog_event(
        "pull_request",
        _pull_request_payload(labels=["enhancement"]),
    )

    allowed, denied = _filter_routes_by_repository(routes, "iterwheel/voyager")
    assert allowed == []
    assert denied == routes

    monkeypatch.setenv("BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CHANGELOG", "iterwheel/voyager")
    allowed, denied = _filter_routes_by_repository(routes, "iterwheel/voyager")
    assert allowed == routes
    assert denied == []


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


def test_append_unreleased_bullet_does_not_treat_issue_link_as_source_pr() -> None:
    text = """# Changelog

## [Unreleased]

- Track blueprint scope ([#12](https://github.com/iterwheel/voyager/issues/12)).

## [0.1.0]
"""
    result = append_unreleased_bullet(
        text,
        bullet="- Earlier work ([#12](https://github.com/iterwheel/voyager/pull/12)).",
        source_pr_number=12,
    )

    assert result.changed is True
    assert "- Earlier work ([#12](https://github.com/iterwheel/voyager/pull/12))." in (result.text)


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


def test_append_unreleased_bullet_matches_manual_entry_by_title_with_issue_link() -> None:
    text = """# Changelog

## [Unreleased]

- Add export workflow ([#12](https://github.com/iterwheel/voyager/issues/12)).

## [0.1.0]
"""
    result = append_unreleased_bullet(
        text,
        bullet="- Add export workflow ([#12](https://github.com/iterwheel/voyager/pull/12)).",
        source_pr_number=12,
    )

    assert result.changed is False
    assert result.reason == "already_present"


def test_append_unreleased_bullet_does_not_match_manual_entry_without_source_signal() -> None:
    text = """# Changelog

## [Unreleased]

- Add export workflow.

## [0.1.0]
"""
    result = append_unreleased_bullet(
        text,
        bullet="- Add export workflow ([#12](https://github.com/iterwheel/voyager/pull/12)).",
        source_pr_number=12,
    )

    assert result.changed is True
    assert result.reason is None


def test_append_unreleased_bullet_does_not_match_same_title_with_different_reference() -> None:
    text = """# Changelog

## [Unreleased]

- Add export workflow ([#13](https://github.com/iterwheel/voyager/pull/13)).

## [0.1.0]
"""
    result = append_unreleased_bullet(
        text,
        bullet="- Add export workflow ([#12](https://github.com/iterwheel/voyager/pull/12)).",
        source_pr_number=12,
    )

    assert result.changed is True
    assert result.reason is None


def test_append_unreleased_bullet_inserts_before_unreleased_subsections() -> None:
    text = """# Changelog

## [Unreleased]

### Added

- Existing categorized note.

## [0.1.0]
"""
    result = append_unreleased_bullet(
        text,
        bullet="- Add export workflow ([#123](https://github.com/iterwheel/voyager/pull/123)).",
        source_pr_number=123,
    )

    assert result.changed is True
    assert (
        "## [Unreleased]\n"
        "- Add export workflow ([#123](https://github.com/iterwheel/voyager/pull/123)).\n\n"
        "### Added"
    ) in result.text


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


def test_dispatch_route_writeback_preserves_existing_changelog_pr(monkeypatch) -> None:
    monkeypatch.setenv("DRY_RUN", "false")
    route = route_changelog_event(
        "pull_request",
        _pull_request_payload(labels=["enhancement"]),
    )[0]
    client = MagicMock()
    client.branch_ref_exists = AsyncMock(return_value=True)
    client.find_pull_request_by_head = AsyncMock(
        return_value={
            "number": 456,
            "html_url": "https://github.com/iterwheel/voyager/pull/456",
        }
    )
    client.installation_token = AsyncMock(return_value="ghs_should_not_be_used")

    result = asyncio.run(
        dispatch_route_writeback(
            client,
            route,
            repository="iterwheel/voyager",
        )
    )

    assert result["applied"] is False
    assert result["reason"] == "existing changelog draft branch"
    assert result["pr_number"] == 456
    assert result["preserved_existing_branch"] is True
    client.installation_token.assert_not_awaited()


def test_dispatch_route_writeback_returns_structured_branch_lookup_failure(monkeypatch) -> None:
    monkeypatch.setenv("DRY_RUN", "false")
    route = route_changelog_event(
        "pull_request",
        _pull_request_payload(labels=["enhancement"]),
    )[0]
    client = MagicMock()
    client.branch_ref_exists = AsyncMock(side_effect=RuntimeError("no key"))

    result = asyncio.run(
        dispatch_route_writeback(
            client,
            route,
            repository="iterwheel/voyager",
        )
    )

    assert result["applied"] is False
    assert result["reason"] == "existing branch lookup failed: RuntimeError"
    assert result["planned"]["branch"] == "changelog/pr-123-unreleased"


def test_dispatch_route_writeback_opens_pr_for_existing_branch_without_open_pr(monkeypatch) -> None:
    monkeypatch.setenv("DRY_RUN", "false")
    route = route_changelog_event(
        "pull_request",
        _pull_request_payload(labels=["enhancement"]),
    )[0]
    client = MagicMock()
    client.branch_ref_exists = AsyncMock(return_value=True)
    client.find_pull_request_by_head = AsyncMock(return_value=None)
    client.create_pull_request = AsyncMock(
        return_value={
            "number": 456,
            "html_url": "https://github.com/iterwheel/voyager/pull/456",
        }
    )
    client.create_issue_comment = AsyncMock(return_value={"id": 789})
    client.installation_token = AsyncMock(return_value="ghs_should_not_be_used")

    result = asyncio.run(
        dispatch_route_writeback(
            client,
            route,
            repository="iterwheel/voyager",
        )
    )

    assert result["applied"] is True
    assert result["pr_number"] == 456
    assert result["preserved_existing_branch"] is True
    client.create_pull_request.assert_awaited_once()
    client.installation_token.assert_not_awaited()


def test_dispatch_route_writeback_returns_structured_token_failure(monkeypatch) -> None:
    monkeypatch.setenv("DRY_RUN", "false")
    route = route_changelog_event(
        "pull_request",
        _pull_request_payload(labels=["enhancement"]),
    )[0]
    client = MagicMock()
    client.branch_ref_exists = AsyncMock(return_value=False)
    client.installation_token = AsyncMock(side_effect=RuntimeError("no key"))

    result = asyncio.run(
        dispatch_route_writeback(
            client,
            route,
            repository="iterwheel/voyager",
        )
    )

    assert result["applied"] is False
    assert result["reason"] == "installation token failed: RuntimeError"
    assert result["planned"]["branch"] == "changelog/pr-123-unreleased"


def test_publish_new_changelog_pr_uses_empty_branch_lease(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_run_git(argv: list[str], **kwargs: Any) -> tuple[int, str]:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return 0, ""

    monkeypatch.setattr(changelog_writeback, "_run_git", fake_run_git)
    client = MagicMock()
    client.create_pull_request = AsyncMock(
        return_value={
            "number": 456,
            "html_url": "https://github.com/iterwheel/voyager/pull/456",
        }
    )
    client.create_issue_comment = AsyncMock(return_value={"id": 789})

    result = asyncio.run(
        changelog_writeback._publish_new_changelog_pr(
            client,
            repository="iterwheel/voyager",
            branch="changelog/pr-123-unreleased",
            base="main",
            title="chore(changelog): draft entry for #123",
            body="body",
            cwd=Path("."),
            env={},
        )
    )

    assert result["applied"] is True
    assert "--force-with-lease=refs/heads/changelog/pr-123-unreleased:" in captured["argv"]
    assert "HEAD:refs/heads/changelog/pr-123-unreleased" in captured["argv"]
    assert result["codex_comment_id"] == 789


def test_build_changelog_bullet_normalizes_title_spacing_and_punctuation() -> None:
    assert (
        build_changelog_bullet(
            pr_number=7,
            pr_title="  Add   export workflow.  ",
            pr_url="https://github.com/iterwheel/voyager/pull/7",
        )
        == "- Add export workflow ([#7](https://github.com/iterwheel/voyager/pull/7))."
    )
