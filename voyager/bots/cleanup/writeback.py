"""Writeback handler for merged same-repo PR branch deletion."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from voyager.core.writeback import dry_run_enabled

_log = logging.getLogger(__name__)

# App slug used for API calls.  Must have an installation on the target repo.
# Reuses the assembly slug since it is always installed on iterwheel/voyager.
_CLEANUP_APP_SLUG = "iterwheel-assembly"


def _branch_protection_checker(client: Any) -> Any:
    instance_attrs = getattr(client, "__dict__", {})
    if isinstance(instance_attrs, dict) and "branch_protected_or_raise" in instance_attrs:
        return instance_attrs["branch_protected_or_raise"]
    if getattr(type(client), "branch_protected_or_raise", None) is not None:
        return client.branch_protected_or_raise
    return client.branch_protected


def _branch_head_sha_checker(client: Any) -> Any | None:
    instance_attrs = getattr(client, "__dict__", {})
    if isinstance(instance_attrs, dict) and "branch_head_sha_or_raise" in instance_attrs:
        return instance_attrs["branch_head_sha_or_raise"]
    if getattr(type(client), "branch_head_sha_or_raise", None) is not None:
        return client.branch_head_sha_or_raise
    return None


async def dispatch_pr_branch_cleanup(
    client: Any,
    route: dict[str, Any],
    *,
    repository: str | None,
) -> dict[str, Any]:
    """Delete the head branch of a merged same-repo PR.

    Protected branches are never deleted.  Fork-sourced PRs are filtered out by
    ``route_pr_merge_cleanup`` and should never reach this handler.
    """
    writeback = route.get("writeback") or {}
    head_ref: str = str(writeback.get("head_ref") or "")
    expected_head_sha: str = str(writeback.get("head_sha") or "")
    repo: str = str(writeback.get("repository") or repository or "")
    pr_number = writeback.get("pr_number") or 0

    if not head_ref or not repo:
        return {
            "applied": False,
            "reason": "missing head_ref or repository in writeback payload",
        }

    if dry_run_enabled():
        _log.info(
            "DRY_RUN: would delete branch %s in %s (merged PR #%s)",
            head_ref,
            repo,
            pr_number,
        )
        return {
            "applied": True,
            "dry_run": True,
            "branch": head_ref,
            "repository": repo,
            "pr_number": pr_number,
        }

    if not expected_head_sha:
        return {
            "applied": False,
            "reason": "missing head_sha in writeback payload",
            "branch": head_ref,
            "repository": repo,
            "pr_number": pr_number,
        }

    # Check protection before deletion.  Never delete protected branches
    # (e.g. ``main``, ``develop``).
    try:
        # ``GitHubAppClient.branch_protected`` intentionally fail-safes to True
        # for Clearance severity decisions.  Cleanup needs the strict form so
        # GitHub lookup failures become failed writebacks and can be retried.
        branch_protected = _branch_protection_checker(client)
        protected = await branch_protected(_CLEANUP_APP_SLUG, repo, head_ref)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status == 404:
            _log.info(
                "branch_cleanup: branch %s/%s already gone before protection lookup completed "
                "(PR #%s)",
                repo,
                head_ref,
                pr_number,
            )
            return {
                "applied": True,
                "deleted": False,
                "reason": "already_gone",
                "branch": head_ref,
                "repository": repo,
                "pr_number": pr_number,
            }
        _log.warning(
            "branch_cleanup: branch_protected check failed for %s branch=%s "
            "PR #%s (class=%s status=%s) — skipping deletion to be safe",
            repo,
            head_ref,
            pr_number,
            exc.__class__.__name__,
            status,
        )
        return {
            "applied": False,
            "reason": f"branch_protected check failed: {exc.__class__.__name__}",
        }
    except (httpx.HTTPError, TimeoutError) as exc:
        lookup_status = (
            exc.response.status_code
            if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None
            else None
        )
        _log.warning(
            "branch_cleanup: branch_protected check failed for %s branch=%s "
            "PR #%s (class=%s status=%s) — skipping deletion to be safe",
            repo,
            head_ref,
            pr_number,
            exc.__class__.__name__,
            lookup_status,
        )
        return {
            "applied": False,
            "reason": f"branch_protected check failed: {exc.__class__.__name__}",
        }

    if protected:
        _log.info(
            "branch_cleanup: skipped protected branch %s/%s (PR #%s)",
            repo,
            head_ref,
            pr_number,
        )
        return {
            "applied": True,
            "skipped": "protected",
            "branch": head_ref,
            "repository": repo,
            "pr_number": pr_number,
        }

    branch_head_sha = _branch_head_sha_checker(client)
    if branch_head_sha is None:
        return {
            "applied": False,
            "reason": "branch head SHA check unavailable",
            "branch": head_ref,
            "repository": repo,
            "pr_number": pr_number,
        }

    try:
        current_head_sha = await branch_head_sha(_CLEANUP_APP_SLUG, repo, head_ref)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status == 404:
            _log.info(
                "branch_cleanup: branch %s/%s already gone before ref SHA check completed (PR #%s)",
                repo,
                head_ref,
                pr_number,
            )
            return {
                "applied": True,
                "deleted": False,
                "reason": "already_gone",
                "branch": head_ref,
                "repository": repo,
                "pr_number": pr_number,
            }
        _log.warning(
            "branch_cleanup: ref SHA check failed for %s branch=%s PR #%s "
            "(class=%s status=%s) — skipping deletion to be safe",
            repo,
            head_ref,
            pr_number,
            exc.__class__.__name__,
            status,
        )
        return {
            "applied": False,
            "reason": f"branch head SHA check failed: {exc.__class__.__name__}",
            "branch": head_ref,
            "repository": repo,
            "pr_number": pr_number,
        }
    except (httpx.HTTPError, TimeoutError) as exc:
        _log.warning(
            "branch_cleanup: ref SHA transport error for %s branch=%s PR #%s "
            "(class=%s) — skipping deletion to be safe",
            repo,
            head_ref,
            pr_number,
            exc.__class__.__name__,
        )
        return {
            "applied": False,
            "reason": f"branch head SHA transport error: {exc.__class__.__name__}",
            "branch": head_ref,
            "repository": repo,
            "pr_number": pr_number,
        }

    if not current_head_sha:
        return {
            "applied": False,
            "reason": "branch head SHA check returned empty SHA",
            "branch": head_ref,
            "repository": repo,
            "pr_number": pr_number,
        }

    if current_head_sha != expected_head_sha:
        _log.info(
            "branch_cleanup: skipped branch %s/%s because current SHA differs from merged "
            "PR head (PR #%s)",
            repo,
            head_ref,
            pr_number,
        )
        return {
            "applied": True,
            "skipped": "head_sha_changed",
            "branch": head_ref,
            "repository": repo,
            "pr_number": pr_number,
            "expected_head_sha": expected_head_sha,
            "current_head_sha": current_head_sha,
        }

    # Attempt deletion.  404 means the branch was already deleted by someone
    # else — treat as success (idempotent).
    try:
        await client.delete_branch(_CLEANUP_APP_SLUG, repo, head_ref)
        _log.info(
            "branch_cleanup: deleted branch %s/%s (merged PR #%s)",
            repo,
            head_ref,
            pr_number,
        )
        return {
            "applied": True,
            "deleted": True,
            "branch": head_ref,
            "repository": repo,
            "pr_number": pr_number,
        }
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            _log.info(
                "branch_cleanup: branch %s/%s already gone (PR #%s)",
                repo,
                head_ref,
                pr_number,
            )
            return {
                "applied": True,
                "deleted": False,
                "reason": "already_gone",
                "branch": head_ref,
                "repository": repo,
                "pr_number": pr_number,
            }
        status = exc.response.status_code if exc.response is not None else None
        _log.warning(
            "branch_cleanup: delete failed for %s branch=%s PR #%s (status=%s) — skipping",
            repo,
            head_ref,
            pr_number,
            status,
        )
        return {
            "applied": False,
            "reason": f"delete failed (status={status})",
            "branch": head_ref,
            "repository": repo,
            "pr_number": pr_number,
        }
    except (httpx.HTTPError, TimeoutError) as exc:
        _log.warning(
            "branch_cleanup: delete transport error for %s branch=%s PR #%s (class=%s) — skipping",
            repo,
            head_ref,
            pr_number,
            exc.__class__.__name__,
        )
        return {
            "applied": False,
            "reason": f"delete transport error: {exc.__class__.__name__}",
        }
