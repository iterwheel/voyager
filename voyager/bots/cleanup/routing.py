"""Route builder for merged same-repo PR branch cleanup."""

from __future__ import annotations

from typing import Any

# Keep cleanup on its own route allow-list. Writeback still authenticates with
# the Assembly App token, but route gating must not enable Assembly execution.
CLEANUP_AGENT_SLUG = "iterwheel-cleanup"
CLEANUP_AGENT_ID = "github-cleanup-agent"
CLEANUP_DYNAMIC = "pr_branch_cleanup"
CLEANUP_TARGET_REPOSITORY = "iterwheel/voyager"


def route_pr_merge_cleanup(event: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a branch-cleanup route for merged same-repo PRs.

    Only fires on ``pull_request`` events with ``action: closed`` and
    ``merged: true`` where the head and base repositories match (same-repo PR).
    Fork-sourced PRs are ignored.
    """
    if event != "pull_request":
        return []
    if (payload.get("action") or "") != "closed":
        return []

    pr = dict(payload.get("pull_request") or {})
    if pr.get("merged") is not True:
        return []

    # Extract repository info from the PR metadata.
    repo_full_name: str = (payload.get("repository") or {}).get("full_name") or ""
    if repo_full_name != CLEANUP_TARGET_REPOSITORY:
        return []

    head_repo: str = ((pr.get("head") or {}).get("repo") or {}).get("full_name") or ""
    base_repo: str = ((pr.get("base") or {}).get("repo") or {}).get("full_name") or ""

    # Only clean up same-repo PR branches — never touch fork PRs.
    if not head_repo or not base_repo or head_repo != base_repo:
        return []

    head = pr.get("head") or {}
    head_ref: str = str(head.get("ref") or "")
    head_sha: str = str(head.get("sha") or "")
    if not head_ref or not head_sha:
        return []

    pr_number = int(pr.get("number") or payload.get("number") or 0)
    if pr_number <= 0:
        return []

    validation: dict[str, Any] = {
        "status": "cleanup_ready",
        "conclusion": "success",
        "pr_number": pr_number,
        "repository": repo_full_name,
        "head_ref": head_ref,
        "head_sha": head_sha,
    }
    writeback: dict[str, Any] = {
        "dynamic": CLEANUP_DYNAMIC,
        "pr_number": pr_number,
        "repository": repo_full_name,
        "head_ref": head_ref,
        "head_sha": head_sha,
    }
    return [
        {
            "agent": CLEANUP_AGENT_SLUG,
            "agent_id": CLEANUP_AGENT_ID,
            "kind": CLEANUP_DYNAMIC,
            "event": event,
            "action": payload.get("action"),
            "validation": validation,
            "writeback": writeback,
        }
    ]
