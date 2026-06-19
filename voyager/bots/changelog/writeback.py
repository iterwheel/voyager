"""Writeback path for changelog merge draft routes."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import tempfile
from pathlib import Path
from typing import Any

from voyager.core.publish import (
    CODEX_REVIEW_TRIGGER_BODY,
    _git_auth_env,
    _write_git_askpass,
)
from voyager.core.writeback import dry_run_enabled

from .constants import (
    CHANGELOG_APP_SLUG,
    CHANGELOG_DEFAULT_BASE,
    CHANGELOG_FILE,
)
from .draft import append_unreleased_bullet

_log = logging.getLogger(__name__)


async def _run_git(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout_seconds: int = 120,
) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_raw, stderr_raw = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
        rc = process.returncode or 0
    except TimeoutError:
        kill = getattr(process, "kill", None)
        if callable(kill):
            kill()
        return -1, "timeout"
    return rc, (stderr_raw or stdout_raw).decode(errors="replace").strip()


def _pr_body(draft: dict[str, Any]) -> str:
    pr_number = int(draft.get("pr_number") or 0)
    pr_title = str(draft.get("pr_title") or f"Pull request #{pr_number}")
    pr_url = str(draft.get("pr_url") or "")
    bullet = str(draft.get("bullet") or "")
    return (
        "Auto-drafted an `[Unreleased]` changelog entry for the merged source PR.\n\n"
        f"Source: [{pr_title}]({pr_url})\n\n"
        "Entry:\n\n"
        f"{bullet}\n\n"
        f"Refs #{pr_number}."
    )


async def _publish_new_changelog_pr(
    client: Any,
    *,
    repository: str,
    branch: str,
    base: str,
    title: str,
    body: str,
    cwd: Path,
    env: dict[str, str],
) -> dict[str, Any]:
    rc, stderr = await _run_git(
        [
            "git",
            "push",
            f"--force-with-lease=refs/heads/{branch}:",
            "--no-verify",
            "origin",
            f"HEAD:refs/heads/{branch}",
        ],
        cwd=cwd,
        env=env,
    )
    if rc != 0:
        _log.warning("changelog branch push failed for %s:%s: %s", repository, branch, stderr)
        return {"applied": False, "reason": "git push failed"}

    return await _create_changelog_pr(
        client,
        repository=repository,
        branch=branch,
        base=base,
        title=title,
        body=body,
    )


async def _create_changelog_pr(
    client: Any,
    *,
    repository: str,
    branch: str,
    base: str,
    title: str,
    body: str,
) -> dict[str, Any]:
    try:
        pr = await client.create_pull_request(
            CHANGELOG_APP_SLUG,
            repository,
            title=title,
            head=branch,
            base=base,
            body=body,
        )
    except Exception as exc:
        _log.warning(
            "changelog pull request creation failed for %s:%s: %s",
            repository,
            branch,
            exc.__class__.__name__,
        )
        return {"applied": False, "reason": f"create_pull_request failed: {exc.__class__.__name__}"}

    pr_number = int(pr.get("number") or 0) if isinstance(pr, dict) else 0
    pr_url = pr.get("html_url") if isinstance(pr, dict) else None
    codex_id: int | None = None
    if pr_number:
        try:
            comment = await client.create_issue_comment(
                CHANGELOG_APP_SLUG,
                repository,
                pr_number,
                body=CODEX_REVIEW_TRIGGER_BODY,
            )
            codex_id = (
                int(comment["id"]) if isinstance(comment, dict) and comment.get("id") else None
            )
        except Exception as exc:
            _log.warning(
                "changelog codex trigger failed for %s#%s: %s",
                repository,
                pr_number,
                exc.__class__.__name__,
            )

    return {
        "applied": True,
        "reason": None,
        "pr_number": pr_number or None,
        "pr_url": pr_url,
        "pr_action": "opened",
        "codex_comment_id": codex_id,
    }


async def _recover_existing_changelog_branch(
    client: Any,
    *,
    repository: str,
    branch: str,
    base: str,
    title: str,
    body: str,
    planned: dict[str, Any],
) -> dict[str, Any]:
    """Reuse an existing changelog branch without pushing over its commits."""
    existing: dict[str, Any] | None = None
    with contextlib.suppress(Exception):
        maybe_existing = await client.find_pull_request_by_head(
            CHANGELOG_APP_SLUG,
            repository,
            branch,
        )
        if isinstance(maybe_existing, dict):
            existing = maybe_existing

    result: dict[str, Any] = {
        "applied": False,
        "reason": "existing changelog draft branch",
        "planned": planned,
        "preserved_existing_branch": True,
    }
    if existing and existing.get("number"):
        result["pr_number"] = int(existing["number"])
        result["pr_url"] = existing.get("html_url")
        return result

    created = await _create_changelog_pr(
        client,
        repository=repository,
        branch=branch,
        base=base,
        title=title,
        body=body,
    )
    return {
        **result,
        **created,
        "planned": planned,
        "preserved_existing_branch": True,
    }


async def dispatch_changelog_writeback(
    client: Any,
    route: dict[str, Any],
    *,
    repository: str | None,
) -> dict[str, Any]:
    """Open or update a follow-up PR containing the generated changelog bullet."""
    if not repository:
        return {"applied": False, "reason": "missing repository"}

    writeback = route.get("writeback") or {}
    draft = dict(writeback.get("draft") or {})
    pr_number = int(draft.get("pr_number") or 0)
    if pr_number <= 0:
        return {"applied": False, "reason": "missing source PR number"}

    branch = str(draft.get("branch_name") or f"changelog/pr-{pr_number}-unreleased")
    base = str(draft.get("base_ref") or CHANGELOG_DEFAULT_BASE)
    title = f"chore(changelog): draft entry for #{pr_number}"
    bullet = str(draft.get("bullet") or "")
    if not bullet:
        return {"applied": False, "reason": "missing changelog bullet"}

    planned = {
        "repository": repository,
        "branch": branch,
        "base": base,
        "source_pr_number": pr_number,
        "bullet": bullet,
    }
    if dry_run_enabled():
        return {"applied": False, "dry_run": True, "planned": planned}

    try:
        branch_exists = await client.branch_ref_exists(
            CHANGELOG_APP_SLUG,
            repository,
            branch,
        )
    except Exception as exc:
        _log.warning(
            "changelog existing branch lookup failed for %s:%s: %s",
            repository,
            branch,
            exc.__class__.__name__,
        )
        return {
            "applied": False,
            "reason": f"existing branch lookup failed: {exc.__class__.__name__}",
            "planned": planned,
        }
    if branch_exists:
        return await _recover_existing_changelog_branch(
            client,
            repository=repository,
            branch=branch,
            base=base,
            title=title,
            body=_pr_body(draft),
            planned=planned,
        )

    try:
        token = await client.installation_token(CHANGELOG_APP_SLUG, repository=repository)
    except Exception as exc:
        _log.warning(
            "changelog installation token failed for %s: %s",
            repository,
            exc.__class__.__name__,
        )
        return {
            "applied": False,
            "reason": f"installation token failed: {exc.__class__.__name__}",
            "planned": planned,
        }
    if not token:
        return {"applied": False, "reason": "empty installation token"}

    with tempfile.TemporaryDirectory(prefix="changelog-draft-") as tmp:
        tmp_path = Path(tmp)
        askpass = _write_git_askpass(tmp_path)
        env = _git_auth_env(token, askpass)
        checkout = tmp_path / "repo"

        rc, stderr = await _run_git(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                base,
                f"https://github.com/{repository}.git",
                str(checkout),
            ],
            cwd=tmp_path,
            env=env,
        )
        if rc != 0:
            _log.warning("changelog clone failed for %s: %s", repository, stderr)
            return {"applied": False, "reason": "git clone failed"}

        changelog_path = checkout / CHANGELOG_FILE
        if not changelog_path.exists():
            return {"applied": False, "reason": f"missing {CHANGELOG_FILE}"}

        current = changelog_path.read_text(encoding="utf-8")
        update = append_unreleased_bullet(
            current,
            bullet=bullet,
            source_pr_number=pr_number,
        )
        if not update.changed:
            return {
                "applied": False,
                "reason": update.reason or "no changelog change",
                "planned": planned,
            }

        changelog_path.write_text(update.text, encoding="utf-8")
        for argv in (
            ["git", "config", "user.name", "iterwheel-assembly[bot]"],
            [
                "git",
                "config",
                "user.email",
                "41898282+github-actions[bot]@users.noreply.github.com",
            ],
            ["git", "checkout", "-B", branch],
            ["git", "add", CHANGELOG_FILE],
            ["git", "commit", "-m", title],
        ):
            rc, stderr = await _run_git(argv, cwd=checkout, env=env)
            if rc != 0:
                _log.warning("changelog git command failed for %s: %s", repository, stderr)
                return {"applied": False, "reason": f"{argv[1]} failed"}

        publish = await _publish_new_changelog_pr(
            client,
            repository=repository,
            branch=branch,
            base=base,
            title=title,
            body=_pr_body(draft),
            cwd=checkout,
            env=env,
        )
        return {
            **publish,
            "planned": planned,
        }
