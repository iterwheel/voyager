"""CI-failing sweep — find open PRs with red CI and flag them.

This module is not webhook-driven; it runs as a scheduled daily task
dispatched from ``voyager.server`` alongside the deployed-version drift
check and stale-PR triage.  There is no route dispatch, no writeback
envelope — the server calls ``run_ci_failing_sweep`` directly within the
background loop.

Each open PR whose latest commit has a failing required check gets a
``ci-failing`` label and a reminder comment.  Required checks include
GitHub Checks API runs and legacy Commit Status API contexts from the
status-check rollup.  At most one comment per failing run/status id is
created — the comment body embeds a marker
``<!-- voyager:ci-failing-run-{run_id} -->`` so re-runs of the same check
produce at most one comment.

PRs whose latest required checks are all green (or have no required checks)
have the ``ci-failing`` label removed if present.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from voyager.core.github_app import GitHubAppClient

_log = logging.getLogger(__name__)

# The scheduled job uses this feature-specific slug for repository allow-listing
# while authenticating GitHub writes with BRIDGE_CI_FAILING_APP_SLUG.
CI_FAILING_AGENT_SLUG = "iterwheel-ci-failing"
CI_FAILING_LABEL = "ci-failing"
CI_FAILING_LABEL_COLOR = "dc3545"
CI_FAILING_LABEL_DESCRIPTION = "Latest CI run is failing on this pull request."
CI_FAILING_COMMENT_MARKER_PREFIX = "<!-- voyager:ci-failing-run-"
_SEARCH_PAGE_SIZE = 100

# Check conclusion values that count as "red".
_FAILING_CONCLUSIONS = frozenset(
    {
        "failure",
        "error",
        "timed_out",
        "cancelled",
        "action_required",
        "startup_failure",
        "stale",
    }
)
_GREEN_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})


def _ci_failing_marker(run_id: int | str) -> str:
    """Return the HTML-comment marker for a specific check-run/status id."""
    token = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(run_id)).strip("-") or "unknown"
    return f"{CI_FAILING_COMMENT_MARKER_PREFIX}{token} -->"


def _check_outcome(check: dict[str, Any]) -> str:
    return str(check.get("conclusion") or check.get("state") or "").lower()


def _is_failing_check(check: dict[str, Any]) -> bool:
    return _check_outcome(check) in _FAILING_CONCLUSIONS


def _is_green_check(check: dict[str, Any]) -> bool:
    return _check_outcome(check) in _GREEN_CONCLUSIONS


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


async def _has_ci_failing_label(pr: dict[str, Any]) -> bool:
    """Return ``True`` when the PR already carries the ``ci-failing`` label."""
    labels = pr.get("labels") or []
    return any(
        (isinstance(label, dict) and label.get("name") == CI_FAILING_LABEL)
        or label == CI_FAILING_LABEL
        for label in labels
    )


async def _existing_ci_failing_comment(
    client: GitHubAppClient,
    app_slug: str,
    repo: str,
    issue_number: int,
    run_id: int | str,
) -> bool:
    """Return ``True`` when a bot comment with the given run-id marker already exists."""
    comments = await client.issue_comments(app_slug, repo, issue_number)
    marker = _ci_failing_marker(run_id)
    bot_login = f"{app_slug}[bot]"
    for comment in comments:
        user = comment.get("user") or {}
        if user.get("login") != bot_login:
            continue
        body = str(comment.get("body") or "")
        if marker in body:
            return True
    return False


async def run_ci_failing_sweep(
    client: GitHubAppClient,
    app_slug: str,
    repo: str,
) -> dict[str, Any]:
    """Run one CI-failing sweep cycle: find open PRs, check latest CI, flag failures.

    Returns a summary dict with counts and lists of affected PR numbers.
    """
    prs = await _find_open_prs(client, app_slug, repo)
    flagged: list[int] = []
    cleared: list[int] = []
    skipped_no_checks: list[int] = []
    already_failing: list[int] = []
    label_ensured = False

    for pr in prs:
        pr_number = pr.get("number")
        if not isinstance(pr_number, int):
            continue

        # Fetch required rollup contexts for the latest PR commit. The rollup
        # contains both Checks API runs and legacy Commit Status API contexts.
        try:
            required_checks = await client.pull_request_required_status_checks(
                app_slug,
                repo,
                pr_number,
            )
        except Exception:
            _log.exception("Failed to fetch required status checks for PR #%d", pr_number)
            continue

        has_ci_failing_label = await _has_ci_failing_label(pr)
        if not required_checks:
            if has_ci_failing_label:
                try:
                    await client.remove_label(app_slug, repo, pr_number, CI_FAILING_LABEL)
                    cleared.append(pr_number)
                except Exception:
                    _log.exception("Failed to remove ci-failing label from PR #%d", pr_number)
            else:
                skipped_no_checks.append(pr_number)
            continue

        failing_runs = [check for check in required_checks if _is_failing_check(check)]
        all_required_green = all(_is_green_check(check) for check in required_checks)

        if not failing_runs and not all_required_green:
            skipped_no_checks.append(pr_number)
            continue

        if failing_runs:
            # At least one required check is failing — flag the PR.
            if not label_ensured:
                try:
                    await client.ensure_label(
                        app_slug,
                        repo,
                        CI_FAILING_LABEL,
                        color=CI_FAILING_LABEL_COLOR,
                        description=CI_FAILING_LABEL_DESCRIPTION,
                    )
                    label_ensured = True
                except Exception:
                    _log.exception("Failed to ensure ci-failing label exists")
                    continue

            if not has_ci_failing_label:
                try:
                    await client.add_labels(app_slug, repo, pr_number, [CI_FAILING_LABEL])
                except Exception:
                    _log.exception("Failed to add ci-failing label to PR #%d", pr_number)
                    continue
            else:
                already_failing.append(pr_number)

            # Comment on the first failing run only (idempotent per run/check id).
            first_failing = failing_runs[0]
            run_id = first_failing.get("id")
            run_name = str(first_failing.get("name", "unknown"))
            run_url = str(first_failing.get("html_url", ""))
            if isinstance(run_id, int | str) and str(run_id):
                try:
                    already_commented = await _existing_ci_failing_comment(
                        client,
                        app_slug,
                        repo,
                        pr_number,
                        run_id,
                    )
                except Exception:
                    _log.exception(
                        "Failed to inspect ci-failing comments for PR #%d run %s",
                        pr_number,
                        run_id,
                    )
                    already_commented = True
                if not already_commented:
                    try:
                        marker = _ci_failing_marker(run_id)
                        body = (
                            f"{marker}\n\n"
                            f"🔴 Required CI check **{run_name}** is failing on this pull request.\n\n"
                            f"See [{run_name}]({run_url}) for details.\n\n"
                            f"_Automated CI sweep — Iterwheel Bridge_"
                        )
                        await client.create_issue_comment(
                            app_slug,
                            repo,
                            pr_number,
                            body=body,
                        )
                    except Exception:
                        _log.exception(
                            "Failed to add ci-failing comment to PR #%d for run %s",
                            pr_number,
                            run_id,
                        )

            flagged.append(pr_number)

        elif all_required_green and has_ci_failing_label:
            # PR was red before but now green — remove the label
            try:
                await client.remove_label(app_slug, repo, pr_number, CI_FAILING_LABEL)
                cleared.append(pr_number)
            except Exception:
                _log.exception("Failed to remove ci-failing label from PR #%d", pr_number)

        else:
            skipped_no_checks.append(pr_number)

    return {
        "checked": len(prs),
        "flagged": flagged,
        "cleared": cleared,
        "already_failing": already_failing,
        "skipped_no_checks": skipped_no_checks,
    }
