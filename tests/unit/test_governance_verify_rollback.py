from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from textwrap import dedent

import pytest

from voyager.governance.audit_log import ReviewFixAuditLog
from voyager.governance.verify_rollback import (
    VerifyRollbackError,
    VerifyRollbackVerdict,
    verify_commit_or_rollback,
)

_FIXED_NOW = datetime(2026, 6, 20, 3, 10, tzinfo=UTC)


def test_verify_pass_keeps_commit_and_audits_kept(tmp_path, monkeypatch) -> None:
    repo, real_git, commit = _repo_with_fix_commit(tmp_path)
    audit_path = tmp_path / "review-fix.jsonl"
    git_log = _install_git_push_guard(tmp_path, monkeypatch, real_git)
    verify_command = _write_verify_script(
        tmp_path,
        """
        from pathlib import Path

        assert Path("tracked.txt").read_text(encoding="utf-8") == "base\\nfix\\n"
        """,
    )

    result = verify_commit_or_rollback(
        repo_path=repo,
        commit=commit,
        verify_command=verify_command,
        audit_log=ReviewFixAuditLog(audit_path),
        round_number=1,
        finding_id="codex-review:finding-1",
        category="codex-review",
        now=lambda: _FIXED_NOW,
    )

    assert result.verdict is VerifyRollbackVerdict.KEPT
    assert result.verify_returncode == 0
    assert result.rollback_returncode is None
    assert _git(repo, real_git, "rev-parse", "HEAD").strip() == commit
    assert _git(repo, real_git, "status", "--porcelain") == ""
    _assert_no_push(git_log)

    records = ReviewFixAuditLog(audit_path).read_all()
    assert len(records) == 1
    assert records[0].round == 1
    assert records[0].ts == _FIXED_NOW
    assert records[0].commit == commit
    assert records[0].finding_id == "codex-review:finding-1"
    assert records[0].category == "codex-review"
    assert records[0].verdict == "kept"
    assert records[0].tests == (verify_command,)


def test_verify_pass_uses_utcnow_by_default_and_captures_output(tmp_path) -> None:
    repo, real_git, commit = _repo_with_fix_commit(tmp_path)
    verify_command = _write_verify_script(
        tmp_path,
        """
        print("verify-ok")
        """,
    )

    result = verify_commit_or_rollback(
        repo_path=repo,
        commit=commit,
        verify_command=verify_command,
        audit_log=ReviewFixAuditLog(tmp_path / "review-fix.jsonl"),
        round_number=1,
        finding_id="codex-review:finding-default-now",
        category="codex-review",
    )

    assert result.verdict is VerifyRollbackVerdict.KEPT
    assert result.verify_stdout == "verify-ok\n"
    assert result.verify_stderr == ""
    assert result.audit_record.ts.tzinfo is UTC
    assert _git(repo, real_git, "rev-parse", "HEAD").strip() == commit


def test_verify_fail_reverts_commit_and_audits_rolled_back(tmp_path, monkeypatch) -> None:
    repo, real_git, commit = _repo_with_fix_commit(tmp_path)
    audit_path = tmp_path / "review-fix.jsonl"
    git_log = _install_git_push_guard(tmp_path, monkeypatch, real_git)
    verify_command = _write_verify_script(
        tmp_path,
        """
        raise SystemExit(7)
        """,
    )

    result = verify_commit_or_rollback(
        repo_path=repo,
        commit=commit,
        verify_command=verify_command,
        audit_log=ReviewFixAuditLog(audit_path),
        round_number=2,
        finding_id="codex-review:finding-2",
        category="codex-review",
        now=lambda: _FIXED_NOW,
    )

    assert result.verdict is VerifyRollbackVerdict.ROLLED_BACK
    assert result.verify_returncode == 7
    assert result.rollback_returncode == 0
    assert _git(repo, real_git, "show", "HEAD:tracked.txt") == "base\n"
    assert _git(repo, real_git, "status", "--porcelain") == ""
    assert _git(repo, real_git, "log", "--format=%s", "-2").splitlines() == [
        'Revert "fix"',
        "fix",
    ]
    _assert_no_push(git_log)

    records = ReviewFixAuditLog(audit_path).read_all()
    assert len(records) == 1
    assert records[0].round == 2
    assert records[0].ts == _FIXED_NOW
    assert records[0].commit == commit
    assert records[0].finding_id == "codex-review:finding-2"
    assert records[0].category == "codex-review"
    assert records[0].verdict == "rolled_back"
    assert records[0].tests == (verify_command,)


def test_verify_fail_dirty_worktree_audits_revert_failed(tmp_path) -> None:
    repo, real_git, commit = _repo_with_fix_commit(tmp_path)
    audit_path = tmp_path / "review-fix.jsonl"
    verify_command = _write_verify_script(
        tmp_path,
        """
        from pathlib import Path

        Path("tracked.txt").write_text("verify dirtied\\n", encoding="utf-8")
        raise SystemExit(7)
        """,
    )

    with pytest.raises(VerifyRollbackError, match="left the working tree dirty"):
        verify_commit_or_rollback(
            repo_path=repo,
            commit=commit,
            verify_command=verify_command,
            audit_log=ReviewFixAuditLog(audit_path),
            round_number=3,
            finding_id="codex-review:finding-dirty",
            category="codex-review",
            now=lambda: _FIXED_NOW,
        )

    records = ReviewFixAuditLog(audit_path).read_all()
    assert len(records) == 1
    assert records[0].verdict == "revert_failed"
    assert records[0].commit == commit
    assert "M tracked.txt" in _git(repo, real_git, "status", "--porcelain")


def test_git_revert_failure_is_audited_and_aborted(tmp_path) -> None:
    repo, real_git, commit = _repo_with_conflicting_middle_commit(tmp_path)
    audit_path = tmp_path / "review-fix.jsonl"
    verify_command = _write_verify_script(
        tmp_path,
        """
        raise SystemExit(7)
        """,
    )

    with pytest.raises(VerifyRollbackError, match=r"git revert failed.*git revert --abort"):
        verify_commit_or_rollback(
            repo_path=repo,
            commit=commit,
            verify_command=verify_command,
            audit_log=ReviewFixAuditLog(audit_path),
            round_number=4,
            finding_id="codex-review:finding-conflict",
            category="codex-review",
            now=lambda: _FIXED_NOW,
        )

    records = ReviewFixAuditLog(audit_path).read_all()
    assert len(records) == 1
    assert records[0].verdict == "revert_failed"
    assert records[0].commit == commit
    assert _git(repo, real_git, "status", "--porcelain") == ""
    assert _git(repo, real_git, "show", "HEAD:tracked.txt") == "tip\n"


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"repo_path": "missing"}, "repo_path must be an existing directory"),
        ({"commit": "   "}, "commit must be a non-empty string"),
        ({"commit": "deadbeef"}, "commit does not exist"),
        ({"verify_command": "   "}, "verify_command must be a non-empty string"),
        ({"finding_id": "   "}, "finding_id must be a non-empty string"),
        ({"category": "   "}, "category must be a non-empty string"),
        ({"round_number": True}, "round_number must be an integer"),
        ({"round_number": 0}, "round_number must be >= 1"),
    ],
)
def test_rejects_invalid_inputs(
    tmp_path,
    override: dict[str, object],
    message: str,
) -> None:
    repo, _, commit = _repo_with_fix_commit(tmp_path)
    verify_command = _write_verify_script(tmp_path, "print('ok')")
    params: dict[str, object] = {
        "repo_path": repo,
        "commit": commit,
        "verify_command": verify_command,
        "audit_log": ReviewFixAuditLog(tmp_path / "review-fix.jsonl"),
        "round_number": 1,
        "finding_id": "codex-review:finding-invalid",
        "category": "codex-review",
        "now": lambda: _FIXED_NOW,
    }
    params.update(override)

    with pytest.raises(VerifyRollbackError, match=message):
        verify_commit_or_rollback(**params)


def test_dirty_worktree_before_verify_is_rejected(tmp_path) -> None:
    repo, _, commit = _repo_with_fix_commit(tmp_path)
    (repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    verify_command = _write_verify_script(tmp_path, "print('ok')")

    with pytest.raises(VerifyRollbackError, match="working tree is not clean before verification"):
        verify_commit_or_rollback(
            repo_path=repo,
            commit=commit,
            verify_command=verify_command,
            audit_log=ReviewFixAuditLog(tmp_path / "review-fix.jsonl"),
            round_number=1,
            finding_id="codex-review:finding-dirty-pre",
            category="codex-review",
            now=lambda: _FIXED_NOW,
        )


def _repo_with_fix_commit(tmp_path: Path) -> tuple[Path, str, str]:
    real_git = shutil.which("git")
    assert real_git is not None

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, real_git, "init")
    _git(repo, real_git, "config", "user.email", "codex@example.com")
    _git(repo, real_git, "config", "user.name", "Codex")

    tracked = repo / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    _git(repo, real_git, "add", "tracked.txt")
    _git(repo, real_git, "commit", "-m", "base")

    tracked.write_text("base\nfix\n", encoding="utf-8")
    _git(repo, real_git, "add", "tracked.txt")
    _git(repo, real_git, "commit", "-m", "fix")
    commit = _git(repo, real_git, "rev-parse", "HEAD").strip()

    return repo, real_git, commit


def _repo_with_conflicting_middle_commit(tmp_path: Path) -> tuple[Path, str, str]:
    real_git = shutil.which("git")
    assert real_git is not None

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, real_git, "init")
    _git(repo, real_git, "config", "user.email", "codex@example.com")
    _git(repo, real_git, "config", "user.name", "Codex")

    tracked = repo / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    _git(repo, real_git, "add", "tracked.txt")
    _git(repo, real_git, "commit", "-m", "base")

    tracked.write_text("middle\n", encoding="utf-8")
    _git(repo, real_git, "add", "tracked.txt")
    _git(repo, real_git, "commit", "-m", "middle")
    middle_commit = _git(repo, real_git, "rev-parse", "HEAD").strip()

    tracked.write_text("tip\n", encoding="utf-8")
    _git(repo, real_git, "add", "tracked.txt")
    _git(repo, real_git, "commit", "-m", "tip")

    return repo, real_git, middle_commit


def _write_verify_script(directory: Path, source: str) -> str:
    script = directory / "verify.py"
    script.write_text(dedent(source).strip() + "\n", encoding="utf-8")
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"


def _install_git_push_guard(tmp_path: Path, monkeypatch, real_git: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "git-commands.log"
    git_wrapper = bin_dir / "git"
    git_wrapper.write_text(
        dedent(
            f"""\
            #!/bin/sh
            printf '%s\\n' "$*" >> {shlex.quote(str(log_path))}
            for arg in "$@"; do
              if [ "$arg" = "push" ]; then
                echo "git push is forbidden in verify rollback tests" >&2
                exit 97
              fi
            done
            exec {shlex.quote(real_git)} "$@"
            """
        ),
        encoding="utf-8",
    )
    git_wrapper.chmod(git_wrapper.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return log_path


def _assert_no_push(log_path: Path) -> None:
    commands = log_path.read_text(encoding="utf-8").splitlines()
    assert commands
    assert all("push" not in command.split() for command in commands)


def _git(repo: Path, git: str, *args: str) -> str:
    result = subprocess.run(
        [git, *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout
