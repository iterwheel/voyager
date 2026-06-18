"""Webhook routing for the changelog merge drafter."""

from __future__ import annotations

from typing import Any

from .constants import (
    CHANGELOG_AGENT_ID,
    CHANGELOG_AGENT_SLUG,
    CHANGELOG_BRANCH_PREFIX,
    CHANGELOG_DEFAULT_BASE,
    CHANGELOG_DYNAMIC,
)
from .draft import build_changelog_bullet, is_changelog_relevant, label_names


def _repository_name(payload: dict[str, Any]) -> str:
    value = (payload.get("repository") or {}).get("full_name")
    return value if isinstance(value, str) else ""


def _pr_url(repository: str, pr: dict[str, Any], pr_number: int) -> str:
    value = pr.get("html_url")
    if isinstance(value, str) and value:
        return value
    return f"https://github.com/{repository}/pull/{pr_number}"


def _is_self_generated_pr(pr: dict[str, Any]) -> bool:
    title = str(pr.get("title") or "").strip().lower()
    head_ref = str((pr.get("head") or {}).get("ref") or "")
    return title.startswith("chore(changelog):") or head_ref.startswith(CHANGELOG_BRANCH_PREFIX)


def route_changelog_event(event: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return changelog draft routes for merged, changelog-relevant PRs."""
    if event != "pull_request":
        return []
    if (payload.get("action") or "") != "closed":
        return []

    pr = dict(payload.get("pull_request") or {})
    if pr.get("merged") is not True:
        return []

    base_ref = str((pr.get("base") or {}).get("ref") or "")
    if base_ref != CHANGELOG_DEFAULT_BASE:
        return []

    if _is_self_generated_pr(pr):
        return []

    labels = label_names(pr.get("labels") or [])
    if not is_changelog_relevant(pr.get("labels") or []):
        return []

    pr_number = int(pr.get("number") or payload.get("number") or 0)
    if pr_number <= 0:
        return []

    repository = _repository_name(payload)
    title = str(pr.get("title") or f"Pull request #{pr_number}")
    url = _pr_url(repository, pr, pr_number)
    bullet = build_changelog_bullet(pr_number=pr_number, pr_title=title, pr_url=url)
    branch_name = f"{CHANGELOG_BRANCH_PREFIX}{pr_number}-unreleased"
    source = {
        "pr_number": pr_number,
        "pr_title": title,
        "pr_url": url,
        "base_ref": base_ref,
        "head_ref": str((pr.get("head") or {}).get("ref") or ""),
        "labels": labels,
        "branch_name": branch_name,
        "bullet": bullet,
    }
    validation: dict[str, Any] = {
        "status": "changelog_ready",
        "conclusion": "success",
        **source,
    }
    writeback: dict[str, Any] = {
        "dynamic": CHANGELOG_DYNAMIC,
        "draft": source,
    }
    return [
        {
            "agent": CHANGELOG_AGENT_SLUG,
            "agent_id": CHANGELOG_AGENT_ID,
            "kind": CHANGELOG_DYNAMIC,
            "event": event,
            "action": payload.get("action"),
            "validation": validation,
            "writeback": writeback,
        }
    ]
