"""Assembly bot — reusable App-token publish path for same-repo pushes.

VOY-1822 / #102: pushes ``HEAD:refs/heads/<branch>`` to the target
repository using the Assembly App installation token over HTTPS, bypassing
the local operator's ``gh`` or SSH identity.  The push target is always
the explicit HTTPS remote derived from the repository name, never a named
remote like ``origin``, so fork remotes or SSH remotes cannot bypass
App-token auth.

**When to use vs. personal auth:**

Use ``publish_branch()`` when the local git checkout's ``origin`` is an
SSH or fork URL and the operator's ``gh`` / SSH identity lacks write
access to the target repository.  The function configures a temporary
dedicated remote (``assembly-publish``) pointing to
``https://github.com/<owner>/<repo>.git``, pushes over HTTPS with the
App installation token, and always cleans up after itself (removes the
temporary remote, askpass script, and temp directory).

Do **not** use when the local identity already has write access — a
simple ``git push origin HEAD:refs/heads/<branch>`` is sufficient.  Also
avoid for non-GitHub or SSH-only repos.

Usage::

    result = await publish_branch(
        repository="owner/repo",
        branch_name="42-fix-thing",
        installation_token="ghs_...",
        checkout_dir=Path("/tmp/checkout"),
    )
    if not result.success:
        ...  # handle failure

Safety:
- ``--force-with-lease`` protects against upstream divergence on re-push.
- ``--no-verify`` bypasses local pre-push hooks (the managed flow has
  already verified the commit via configured verification commands).
- Token never appears in argv, env dumps, or command output.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PublishResult:
    """Structured outcome of a ``publish_branch`` call.

    Attributes:
        success: True when the push completed without error.
        message: Human-readable summary of the result (success or failure).
    """

    success: bool
    message: str = ""


def _github_safe_remote(repository: str) -> str:
    """Return the HTTPS clone/push URL for a GitHub repository.

    Args:
        repository: ``"owner/repo"`` format.

    Returns:
        ``"https://github.com/owner/repo.git"``
    """
    return f"https://github.com/{repository}.git"


def _write_git_askpass(directory: Path) -> Path:
    """Write a temporary GIT_ASKPASS script that supplies the App token.

    The script reads the token from ``$ASSEMBLY_GITHUB_TOKEN`` so it never
    appears in process argv or shell history.

    Args:
        directory: Writable directory for the script (caller-owned, caller
            responsible for cleanup).

    Returns:
        Path to the executable askpass script.
    """
    askpass = directory / "git-askpass.sh"
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


def _git_push_env(*, token: str, askpass: Path) -> dict[str, str]:
    """Build the environment dict for an authenticated git push.

    The token is passed via ``$ASSEMBLY_GITHUB_TOKEN`` (read by the askpass
    script), never in argv.

    Args:
        token: GitHub installation token.
        askpass: Path to the GIT_ASKPASS script.

    Returns:
        Environment dict safe for ``asyncio.create_subprocess_exec``.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = str(askpass)
    env["ASSEMBLY_GITHUB_TOKEN"] = token
    return env


async def _run_git_push(
    argv: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: dict[str, str],
) -> tuple[int, str, str]:
    """Run a git subprocess and capture its output.

    Caught ``TimeoutError`` is surfaced as a non-zero return with a
    descriptive message instead of propagating, so callers can always
    handle the result structurally.

    Returns:
        ``(returncode, stdout, stderr)``
    """
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_raw, stderr_raw = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        kill = getattr(process, "kill", None)
        if callable(kill):
            kill()
        return (1, "", f"git push timed out after {timeout_seconds}s")

    return (
        int(process.returncode or 0),
        stdout_raw.decode(errors="replace"),
        stderr_raw.decode(errors="replace"),
    )


async def publish_branch(
    *,
    repository: str,
    branch_name: str,
    installation_token: str,
    checkout_dir: Path,
    timeout_seconds: int = 300,
) -> PublishResult:
    """Push ``HEAD:refs/heads/<branch_name>`` to the target repository.

    Uses the Assembly App installation token for authentication via a
    temporary GIT_ASKPASS script.  The push target is always an explicit
    temporary named remote pointing to the target HTTPS URL, never a
    named remote like ``origin``, so fork remotes or SSH remotes cannot
    bypass App-token auth.

    Uses a temporary named remote (``assembly-publish``) instead of
    pushing directly to the URL so that ``--force-with-lease`` has a
    remote-tracking ref to check — pushing to a bare URL makes the lease
    check a no-op.

    Args:
        repository: ``"owner/repo"`` format.
        branch_name: Target branch ref (e.g. ``"42-fix-thing"``).
        installation_token: GitHub App installation token.
        checkout_dir: Existing git checkout to push from.
        timeout_seconds: Per-push timeout (default 300).

    Returns:
        ``PublishResult`` with ``success=True`` on clean push.

    The temporary askpass script, temp directory, and temporary remote
    are removed before returning in all paths (success, failure, or
    timeout).
    """
    remote_url = _github_safe_remote(repository)
    remote_name = "assembly-publish"
    askpass: Path | None = None
    temp_dir: Path | None = None

    try:
        temp_dir = Path(tempfile.mkdtemp(prefix="assembly-publish-"))
        askpass = _write_git_askpass(temp_dir)
        env = _git_push_env(token=installation_token, askpass=askpass)

        # ---- Step 1: add temporary named remote ----
        rc_add, _stdout_add, stderr_add = await _run_git_push(
            ["git", "remote", "add", remote_name, remote_url],
            cwd=checkout_dir,
            timeout_seconds=timeout_seconds,
            env=env,
        )
        if rc_add != 0:
            return PublishResult(
                success=False,
                message=(f"Failed to add temporary remote {remote_name}: {stderr_add.strip()}"),
            )

        # ---- Step 2: push via named remote ----
        # --force-with-lease is safe here because the named remote
        # establishes remote-tracking refs that the lease check can
        # verify against.
        returncode, _stdout, stderr = await _run_git_push(
            [
                "git",
                "push",
                "--force-with-lease",
                "--no-verify",
                remote_name,
                f"HEAD:refs/heads/{branch_name}",
            ],
            cwd=checkout_dir,
            timeout_seconds=timeout_seconds,
            env=env,
        )

        if returncode != 0:
            _log.warning(
                "git push failed for branch %s on %s",
                branch_name,
                repository,
                extra={"returncode": returncode, "stderr": stderr},
            )
            return PublishResult(
                success=False,
                message=f"git push failed (exit {returncode}): {stderr.strip()}",
            )

        return PublishResult(
            success=True,
            message=f"Pushed HEAD:refs/heads/{branch_name} to {remote_url}",
        )

    finally:
        # Best-effort remove temporary remote so the checkout is not
        # polluted. Uses a lightweight subprocess (not _run_git_push) to
        # avoid requiring the auth env during cleanup.
        if checkout_dir.exists():
            with contextlib.suppress(Exception):
                proc = await asyncio.create_subprocess_exec(
                    "git",
                    "remote",
                    "remove",
                    remote_name,
                    cwd=checkout_dir,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc.wait(), timeout=10)
        if askpass is not None and askpass.exists():
            askpass.unlink(missing_ok=True)
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)
