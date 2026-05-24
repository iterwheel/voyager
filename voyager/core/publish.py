"""Assembly App token-based same-repo publish path.

Provides ``assembly_app_publish()`` — a reusable function that pushes
``HEAD`` to a same-repository branch using the ``iterwheel-assembly``
GitHub App installation token, then creates or updates a pull request
and posts a ``@codex review`` comment.

Intended for VOY-1822 managed-flow publishing where the local operator's
``gh`` or SSH identity lacks write access to the target repository.

**When to use this path (operator runbook):**

Use the App-token publish path when the local git remote ``origin`` is
configured with an SSH URL or a personal fork URL and the operator's
``gh`` / SSH identity lacks write access to the target repository.
The path pushes over HTTPS to the explicit target-repository URL
(``https://github.com/<owner>/<repo>.git``) using a temporary named
remote (``assembly-publish-*``), bypassing whatever ``origin`` points to.
This avoids fork PRs and personal-credential fallbacks.

Do **not** use this path in cases where the local identity already has
write access to the target repository — a plain ``git push origin
HEAD:refs/heads/<branch>`` is simpler and sufficient.  Also avoid this
path for repositories that require SSH host-key verification or are not
hosted on GitHub.

Usage (operator runbook)::

    # This function is called by the Assembly writeback path.  It can also
    # be invoked directly from a script or REPL when the operator needs to
    # push an already-implemented branch and open/update a PR as
    # ``iterwheel-assembly`` instead of using a personal ``gh`` or SSH key.
    #
    # The operator must have:
    #   - A valid voyager config with an ``iterwheel-assembly`` App entry
    #   - The App's private key at the configured path
    #   - Network access to api.github.com
    #   - Git installed and the current ``HEAD`` in a valid repository

Token safety:

    - The installation token is **never** written to logs, stdout, stderr,
      comment bodies, or result dicts.
    - The temporary ``GIT_ASKPASS`` helper script is created in a temp
      directory with ``0o700`` permissions and removed after every call.
    - The ``ASSEMBLY_GITHUB_TOKEN`` env var set for the subprocess is
      bounded to the subprocess lifetime.

Push safety:

    - Uses ``--force-with-lease`` unconditionally (refuses if the remote
      ref has moved since the last known state).
    - Uses ``--no-verify`` to bypass local pre-push hooks after configured
      verification has already run.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_GITHUB_TOKEN_RE = re.compile(r"gh[opsru]_[A-Za-z0-9_]+")

CODEX_REVIEW_TRIGGER_BODY = "@codex review"
"""Verbatim trigger body posted on the PR after each push."""

DEFAULT_APP_SLUG = "iterwheel-assembly"


@dataclass(frozen=True)
class PublishResult:
    """Structured outcome of ``assembly_app_publish()``."""

    pushed: bool
    """True when the git push command exited 0."""

    pr_number: int | None
    """PR number when open/update succeeded, else None."""

    pr_url: str | None
    """PR HTML URL when open/update succeeded, else None."""

    pr_action: str | None
    """"opened" | "updated" | None."""

    codex_comment_id: int | None
    """Comment databaseId on the ``@codex review`` trigger, else None."""

    error: str | None
    """Short error message when the overall publish failed, else None."""


def _sanitize(value: str, token: str) -> str:
    """Replace *token* and any ``gh*_`` pattern with ``[redacted]``."""
    sanitized = value.replace(token, "[redacted]") if token else value
    return _GITHUB_TOKEN_RE.sub("[redacted]", sanitized)


def _write_git_askpass(temp_dir: Path) -> Path:
    """Write the ``GIT_ASKPASS`` helper script returning the path.

    The script reads the token from ``ASSEMBLY_GITHUB_TOKEN``.  The file
    is created with ``0o700`` (owner-only read/execute).
    """
    askpass = temp_dir / "git-askpass.sh"
    askpass.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        "*Username*) printf '%s\\n' 'x-access-token' ;;\n"
        "*Password*) printf '%s\\n' \"$ASSEMBLY_GITHUB_TOKEN\" ;;\n"
        "*) printf '\\n' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    askpass.chmod(0o700)
    return askpass


def _git_auth_env(token: str, askpass: Path) -> dict[str, str]:
    """Return a subprocess env dict with the token-askpass wiring."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = str(askpass)
    env["ASSEMBLY_GITHUB_TOKEN"] = token
    return env


async def _run_exec(
    argv: list[str], *, cwd: str, timeout_seconds: int, env: dict[str, str]
) -> tuple[int, str]:
    """Run *argv* and return ``(exit_code, stderr_text)``.

    A timeout returns ``(-1, "")`` so callers can normalize subprocess
    failures into ``PublishResult`` without catching ``TimeoutError``.
    """
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stderr_raw = b""
    try:
        _stdout_raw, stderr_raw = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
        rc = process.returncode or 0
    except TimeoutError:
        kill = getattr(process, "kill", None)
        if callable(kill):
            kill()
        rc = -1
    # Log at debug with token redacted; never surface the raw token.
    _log.debug(
        "publish subprocess exited %s: %s",
        rc,
        " ".join(argv),
    )
    return rc, stderr_raw.decode(errors="replace")


async def assembly_app_publish(
    *,
    repository: str,
    branch: str,
    base: str,
    pr_title: str,
    pr_body: str = "",
    pr_number: int | None = None,
    app_slug: str = DEFAULT_APP_SLUG,
    client: Any,
    cwd: str | Path | None = None,
    timeout_seconds: int = 120,
) -> PublishResult:
    """Publish ``HEAD`` to the target repository via GitHub App token.

    Parameters
    ----------
    repository
        ``owner/name`` of the target repository.
    branch
        Target branch name (e.g. ``102-my-feature``).
    base
        Base branch name (e.g. ``main``).
    pr_title
        Pull request title.
    pr_body
        Pull request body markdown.
    pr_number
        When provided, update this existing PR instead of creating a new one.
    app_slug
        GitHub App slug (default ``iterwheel-assembly``).
    client
        An instance of ``voyager.core.github_app.GitHubAppClient``.
    cwd
        Repository working directory (defaults to ``os.getcwd()``).
    timeout_seconds
        Per-subprocess timeout.

    Returns
    -------
    PublishResult
    """
    work_dir = str(Path(cwd or os.getcwd()).resolve())

    # ---- Step 1: mint installation token ----
    token: str | None = None
    try:
        token = await client.installation_token(app_slug, repository=repository)
    except Exception as exc:
        return PublishResult(
            pushed=False,
            pr_number=None,
            pr_url=None,
            pr_action=None,
            codex_comment_id=None,
            error=f"installation_token failed: {exc.__class__.__name__}",
        )

    if not token:
        return PublishResult(
            pushed=False,
            pr_number=None,
            pr_url=None,
            pr_action=None,
            codex_comment_id=None,
            error="empty installation token",
        )

    # ---- Step 2: create temp dir and askpass helper ----
    tmp_dir: Path | None = None
    askpass: Path | None = None
    remote_name: str | None = None
    remote_created = False
    try:
        tmp_dir_obj = Path(tempfile.mkdtemp(prefix="assembly-publish-"))
        tmp_dir = tmp_dir_obj
        remote_name = tmp_dir_obj.name
        askpass = _write_git_askpass(tmp_dir_obj)
        auth_env = _git_auth_env(token, askpass)

        # ---- Step 3: add temporary named remote ----
        remote_url = f"https://github.com/{repository}.git"
        rc, _stderr = await _run_exec(
            ["git", "remote", "add", remote_name, remote_url],
            cwd=work_dir,
            timeout_seconds=timeout_seconds,
            env=auth_env,
        )
        if rc != 0:
            return PublishResult(
                pushed=False,
                pr_number=None,
                pr_url=None,
                pr_action=None,
                codex_comment_id=None,
                error=f"Failed to add temporary remote {remote_name}",
            )
        remote_created = True

        # ---- Step 3b: fetch target branch for lease baseline ----
        rc_fetch, stderr_fetch = await _run_exec(
            [
                "git",
                "fetch",
                "--no-tags",
                remote_name,
                f"refs/heads/{branch}:refs/remotes/{remote_name}/{branch}",
            ],
            cwd=work_dir,
            timeout_seconds=timeout_seconds,
            env=auth_env,
        )
        if rc_fetch != 0:
            if rc_fetch < 0:
                return PublishResult(
                    pushed=False,
                    pr_number=None,
                    pr_url=None,
                    pr_action=None,
                    codex_comment_id=None,
                    error=f"git fetch timed out ({timeout_seconds}s)",
                )

            fetch_stderr_lower = stderr_fetch.strip().lower()
            missing_remote_ref = (
                "couldn't find remote ref" in fetch_stderr_lower
                or "could not find remote ref" in fetch_stderr_lower
            )
            if not missing_remote_ref:
                return PublishResult(
                    pushed=False,
                    pr_number=None,
                    pr_url=None,
                    pr_action=None,
                    codex_comment_id=None,
                    error=f"git fetch failed: {stderr_fetch.strip()}",
                )

        # ---- Step 4: push via named remote ----
        # --force-with-lease now has a remote-tracking ref to check because
        # the fetch above (when the branch already exists on the remote)
        # populated refs/remotes/<remote_name>/<branch>.
        argv = [
            "git",
            "push",
            "--force-with-lease",
            "--no-verify",
            remote_name,
            f"HEAD:refs/heads/{branch}",
        ]
        rc, _stderr = await _run_exec(
            argv, cwd=work_dir, timeout_seconds=timeout_seconds, env=auth_env
        )

        if rc != 0:
            if rc < 0:
                error_msg = f"git push timed out ({timeout_seconds}s)"
            else:
                error_msg = f"git push failed (exit {rc})"
            return PublishResult(
                pushed=False,
                pr_number=None,
                pr_url=None,
                pr_action=None,
                codex_comment_id=None,
                error=error_msg,
            )

        # ---- Step 4: create or update PR ----
        if pr_number is not None:
            # Update existing PR
            try:
                update_result = await client.update_pull_request(
                    app_slug,
                    repository,
                    pr_number,
                    body=pr_body,
                    title=pr_title,
                )
                pr_url = update_result.get("html_url") if isinstance(update_result, dict) else None
                pr_action = "updated"
            except Exception as exc:
                return PublishResult(
                    pushed=True,
                    pr_number=None,
                    pr_url=None,
                    pr_action=None,
                    codex_comment_id=None,
                    error=f"update_pull_request failed: {exc.__class__.__name__}",
                )
        else:
            # Find existing PR by head branch
            existing = None
            with contextlib.suppress(Exception):
                existing = await client.find_pull_request_by_head(app_slug, repository, branch)

            if existing and isinstance(existing, dict) and existing.get("number"):
                pr_num = int(existing["number"])
                try:
                    await client.update_pull_request(
                        app_slug,
                        repository,
                        pr_num,
                        body=pr_body,
                        title=pr_title,
                    )
                    pr_url = existing.get("html_url")
                    pr_action = "updated"
                    pr_number = pr_num
                except Exception as exc:
                    return PublishResult(
                        pushed=True,
                        pr_number=None,
                        pr_url=None,
                        pr_action=None,
                        codex_comment_id=None,
                        error=f"update_pull_request failed: {exc.__class__.__name__}",
                    )
            else:
                try:
                    create_result = await client.create_pull_request(
                        app_slug,
                        repository,
                        title=pr_title,
                        head=branch,
                        base=base,
                        body=pr_body,
                    )
                    pr_number = (
                        create_result.get("number") if isinstance(create_result, dict) else None
                    )
                    pr_url = (
                        create_result.get("html_url") if isinstance(create_result, dict) else None
                    )
                    pr_action = "opened"
                except Exception as exc:
                    return PublishResult(
                        pushed=True,
                        pr_number=None,
                        pr_url=None,
                        pr_action=None,
                        codex_comment_id=None,
                        error=f"create_pull_request failed: {exc.__class__.__name__}",
                    )

        # ---- Step 5: post @codex review comment ----
        codex_id: int | None = None
        if pr_number is not None:
            try:
                comment = await client.create_issue_comment(
                    app_slug,
                    repository,
                    pr_number,
                    body=CODEX_REVIEW_TRIGGER_BODY,
                )
                codex_id = comment.get("id") if isinstance(comment, dict) else None
            except Exception as exc:
                _log.warning(
                    "codex trigger comment failed on %s#%s: %s",
                    repository,
                    pr_number,
                    exc.__class__.__name__,
                )

        return PublishResult(
            pushed=True,
            pr_number=pr_number,
            pr_url=pr_url,
            pr_action=pr_action,
            codex_comment_id=codex_id,
            error=None,
        )

    finally:
        # ---- Cleanup: remove temporary remote ----
        # Best-effort; uses a lightweight direct subprocess to avoid
        # requiring the auth env during cleanup.
        with contextlib.suppress(Exception):
            if remote_created and remote_name is not None:
                proc = await asyncio.create_subprocess_exec(
                    "git",
                    "remote",
                    "remove",
                    remote_name,
                    cwd=work_dir,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc.wait(), timeout=10)
        # ---- Cleanup: remove askpass ----
        if askpass is not None and askpass.exists():
            with contextlib.suppress(OSError):
                askpass.unlink()
        if tmp_dir is not None and tmp_dir.exists():
            import shutil

            shutil.rmtree(tmp_dir, ignore_errors=True)
