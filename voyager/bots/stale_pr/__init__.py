"""Stale-PR triage — label/comment on inactive open PRs (L1 advisory only).

This module is not webhook-driven; it runs as a scheduled daily task
dispatched from ``voyager.server`` alongside the deployed-version drift
check.  There is no route dispatch, no writeback envelope — the server
calls ``run_stale_pr_triage`` directly within the background loop.

L1 advisory means the bot never closes, merges, or requests changes.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from voyager.core.github_app import GitHubAppClient

_log = logging.getLogger(__name__)

STALE_AGENT_SLUG = "iterwheel-assembly"
STALE_LABEL = "stale"
STALE_COMMENT_MARKER = "<!-- voyager:stale-pr-reminder -->"
STALE_LABEL_COLOR = "cfd3d7"
STALE_LABEL_DESCRIPTION = "Stale pull request pending human attention."
_SEARCH_PAGE_SIZE = 100


def _is_older_than(updated_at: str | None, *, days: int) -> bool:
    """Return ``True`` when the ISO-8601 timestamp is older than *days* ago."""
    if not updated_at:
        return False
    try:
        updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        _log.warning("unparseable timestamp %r", updated_at)
        return False
    threshold = datetime.now(UTC) - timedelta(days=days)
    return updated < threshold


def _build_stale_comment_body(*, stale_days: int) -> str:
    """Build the L1-advisory reminder comment for a stale PR."""
    return (
        f"{STALE_COMMENT_MARKER}\n\n"
        f"⚠️ This pull request has had no activity for {stale_days} days. "
        f"It has been labeled as `{STALE_LABEL}`.\n\n"
        f"**L1 advisory only** — no automatic close or merge will occur. "
        f"If no further activity happens, this advisory will continue "
        f"on the daily triage.\n\n"
        f"_Automated triage — Iterwheel Bridge_"
    )


async def _find_open_prs(
    client: GitHubAppClient,
    app_slug: str,
    repo: str,
) -> list[dict[str, Any]]:
    """Return all open PRs for *repo* via the GitHub Search Issues API."""
    owner, name = repo.split("/", 1)
    prs: list[dict[str, Any]] = []
    page = 1
    while True:
        path = (
            f"/search/issues"
            f"?q=repo%3A{owner}%2F{name}+type%3Apr+state%3Aopen"
            f"&per_page={_SEARCH_PAGE_SIZE}&sort=updated&order=asc&page={page}"
        )
        data = await client.request(app_slug, "GET", path, repository=repo)
        items = list((data or {}).get("items") or [])
        prs.extend(items)
        if len(items) < _SEARCH_PAGE_SIZE:
            return prs
        page += 1


async def _has_stale_label(pr: dict[str, Any]) -> bool:
    """Return ``True`` when the PR already carries the ``stale`` label."""
    labels = pr.get("labels") or []
    return any(
        (isinstance(label, dict) and label.get("name") == STALE_LABEL) or label == STALE_LABEL
        for label in labels
    )


async def _has_recent_reminder_comment(
    client: GitHubAppClient,
    app_slug: str,
    repo: str,
    issue_number: int,
    *,
    within_days: int,
) -> bool:
    """Return ``True`` when a bot comment with the staleness marker exists and
    is newer than *within_days*."""
    comments = await client.issue_comments(app_slug, repo, issue_number)
    bot_login = f"{app_slug}[bot]"
    for comment in comments:
        user = comment.get("user") or {}
        if user.get("login") != bot_login:
            continue
        body = comment.get("body") or ""
        if STALE_COMMENT_MARKER not in body:
            continue
        created_at = comment.get("created_at") or ""
        if not created_at:
            continue
        if not _is_older_than(created_at, days=within_days):
            return True
    return False


async def run_stale_pr_triage(
    client: GitHubAppClient,
    app_slug: str,
    repo: str,
    *,
    stale_days: int,
) -> dict[str, Any]:
    """Run one stale-PR triage cycle: find open PRs, label stale ones, add
    at most one reminder comment per staleness window.

    Returns a summary dict with counts and lists of affected PR numbers.
    Never mutates beyond labeling and commenting (L1 advisory).
    """
    prs = await _find_open_prs(client, app_slug, repo)
    labeled: list[int] = []
    commented: list[int] = []
    already_labeled: list[int] = []
    skipped_fresh: list[int] = []
    stale_label_ensured = False

    for pr in prs:
        number = pr.get("number")
        if not isinstance(number, int):
            continue

        updated_at = pr.get("updated_at")
        if not _is_older_than(updated_at, days=stale_days):
            skipped_fresh.append(number)
            continue

        # Stale PR — ensure the stale label is present
        stale_label_present = await _has_stale_label(pr)
        if not stale_label_present:
            try:
                if not stale_label_ensured:
                    await client.ensure_label(
                        app_slug,
                        repo,
                        STALE_LABEL,
                        color=STALE_LABEL_COLOR,
                        description=STALE_LABEL_DESCRIPTION,
                    )
                    stale_label_ensured = True
                await client.add_labels(app_slug, repo, number, [STALE_LABEL])
                labeled.append(number)
            except Exception:
                _log.exception("Failed to add stale label to PR #%d", number)
                continue
        else:
            already_labeled.append(number)

        # At most one reminder comment per staleness window
        # If a recent bot reminder comment exists, skip
        recent_reminder = await _has_recent_reminder_comment(
            client,
            app_slug,
            repo,
            number,
            within_days=stale_days,
        )
        if not recent_reminder:
            try:
                body = _build_stale_comment_body(stale_days=stale_days)
                await client.create_issue_comment(
                    app_slug,
                    repo,
                    number,
                    body=body,
                )
                commented.append(number)
            except Exception:
                _log.exception("Failed to add stale reminder comment to PR #%d", number)

    return {
        "checked": len(prs),
        "labeled": labeled,
        "already_labeled": already_labeled,
        "commented": commented,
        "skipped_fresh": skipped_fresh,
    }
