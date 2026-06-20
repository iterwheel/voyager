"""Verify a review-fix commit and locally roll it back on failure."""

from __future__ import annotations

import os
import shutil

# Bandit: subprocess is required for fixed local git commands and the configured verify command.
import subprocess  # nosec B404
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from .audit_log import ReviewFixAuditLog, ReviewFixAuditRecord


class VerifyRollbackError(RuntimeError):
    """Raised when verify/rollback cannot complete safely."""


class VerifyRollbackVerdict(StrEnum):
    """Possible verify/rollback outcomes recorded in the audit log."""

    KEPT = "kept"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True, kw_only=True)
class VerifyRollbackResult:
    """Result of verifying a fix commit under the review-fix envelope."""

    commit: str
    verdict: VerifyRollbackVerdict
    verify_command: str
    verify_returncode: int
    verify_stdout: str
    verify_stderr: str
    audit_record: ReviewFixAuditRecord
    rollback_returncode: int | None = None
    rollback_stdout: str = ""
    rollback_stderr: str = ""


@dataclass(frozen=True, kw_only=True)
class _CommandResult:
    returncode: int
    stdout: str
    stderr: str


NowFactory = Callable[[], datetime]


def verify_commit_or_rollback(
    *,
    repo_path: str | Path,
    commit: str,
    verify_command: str,
    audit_log: ReviewFixAuditLog,
    round_number: int,
    finding_id: str,
    category: str,
    now: NowFactory | None = None,
    timeout_seconds: float | None = None,
) -> VerifyRollbackResult:
    """Run verification for a fix commit and locally revert it on failure.

    The function never pushes. Rollback is a local ``git revert`` commit with
    hooks disabled so repository-local hooks cannot perform remote side effects.
    """

    repo = _repo_path(repo_path)
    git = _git_executable()
    normalized_commit = _non_empty(commit, "commit")
    normalized_verify_command = _non_empty(verify_command, "verify_command")
    normalized_finding_id = _non_empty(finding_id, "finding_id")
    normalized_category = _non_empty(category, "category")
    normalized_round = _positive_round(round_number)

    _ensure_commit_exists(git, repo, normalized_commit)
    _ensure_clean_worktree(git, repo, "before verification")

    verify = _run_verify_command(
        normalized_verify_command,
        cwd=repo,
        timeout_seconds=timeout_seconds,
    )

    rollback: _CommandResult | None = None
    if verify.returncode == 0:
        verdict = VerifyRollbackVerdict.KEPT
        _ensure_clean_worktree(git, repo, "after successful verification")
    else:
        verdict = VerifyRollbackVerdict.ROLLED_BACK
        rollback = _run_git(
            git,
            ("revert", "--no-edit", "--no-gpg-sign", normalized_commit),
            cwd=repo,
        )
        if rollback.returncode != 0:
            raise VerifyRollbackError(
                "git revert failed after verification failure: "
                f"{_first_error_line(rollback.stderr) or rollback.returncode}"
            )
        _ensure_clean_worktree(git, repo, "after rollback")

    record = ReviewFixAuditRecord(
        round=normalized_round,
        ts=(now or _utcnow)(),
        commit=normalized_commit,
        finding_id=normalized_finding_id,
        category=normalized_category,
        verdict=verdict.value,
        tests=(normalized_verify_command,),
    )
    audit_log.append(record)

    return VerifyRollbackResult(
        commit=normalized_commit,
        verdict=verdict,
        verify_command=normalized_verify_command,
        verify_returncode=verify.returncode,
        verify_stdout=verify.stdout,
        verify_stderr=verify.stderr,
        audit_record=record,
        rollback_returncode=None if rollback is None else rollback.returncode,
        rollback_stdout="" if rollback is None else rollback.stdout,
        rollback_stderr="" if rollback is None else rollback.stderr,
    )


def _repo_path(raw: str | Path) -> Path:
    repo = Path(raw)
    if not repo.is_dir():
        raise VerifyRollbackError(f"repo_path must be an existing directory: {repo}")
    return repo


def _git_executable() -> str:
    git = shutil.which("git")
    if git is None:
        raise VerifyRollbackError("git executable not found on PATH")
    return git


def _ensure_commit_exists(git: str, repo: Path, commit: str) -> None:
    result = _run_git(git, ("rev-parse", "--verify", f"{commit}^{{commit}}"), cwd=repo)
    if result.returncode != 0:
        raise VerifyRollbackError(f"commit does not exist: {commit}")


def _ensure_clean_worktree(git: str, repo: Path, phase: str) -> None:
    result = _run_git(git, ("status", "--porcelain"), cwd=repo)
    if result.returncode != 0:
        raise VerifyRollbackError(
            f"git status failed {phase}: {_first_error_line(result.stderr) or result.returncode}"
        )
    if result.stdout.strip():
        raise VerifyRollbackError(f"working tree is not clean {phase}: {result.stdout.strip()}")


def _run_verify_command(
    command: str,
    *,
    cwd: Path,
    timeout_seconds: float | None,
) -> _CommandResult:
    shell = shutil.which("bash") or shutil.which("sh")
    if shell is None:
        raise VerifyRollbackError("bash or sh executable not found on PATH")
    shell_arg = "-lc" if Path(shell).name == "bash" else "-c"
    return _run_process(
        (shell, shell_arg, command),
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        env=_subprocess_env(),
    )


def _run_git(git: str, args: Sequence[str], *, cwd: Path) -> _CommandResult:
    return _run_process(
        (git, "-c", "core.hooksPath=/dev/null", *args),
        cwd=cwd,
        timeout_seconds=None,
        env=_subprocess_env(),
    )


def _run_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: float | None,
    env: dict[str, str],
) -> _CommandResult:
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )  # nosec B603
    except subprocess.TimeoutExpired as exc:
        return _CommandResult(
            returncode=124,
            stdout=_timeout_text(exc.stdout),
            stderr=_timeout_text(exc.stderr) or f"command timed out after {timeout_seconds}s",
        )
    return _CommandResult(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def _subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("GIT_ASKPASS", "/bin/false")
    env.setdefault("SSH_ASKPASS", "/bin/false")
    return env


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _non_empty(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise VerifyRollbackError(f"{name} must be a non-empty string")
    return normalized


def _positive_round(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise VerifyRollbackError(f"round_number must be an integer, got {type(value).__name__}")
    if value < 1:
        raise VerifyRollbackError(f"round_number must be >= 1, got {value!r}")
    return value


def _first_error_line(stderr: str) -> str:
    for line in stderr.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _utcnow() -> datetime:
    return datetime.now(UTC)
