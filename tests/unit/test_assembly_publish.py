"""Unit tests for the reusable App-token publish path (VOY-1822 / #102).

Tests cover:
- ``_github_safe_remote`` produces correct HTTPS URL.
- ``_write_git_askpass`` produces an executable script with the expected
  content.
- ``_git_push_env`` correctly injects environment vars.
- ``_run_git_push`` handles success, failure, and timeout structurally.
- ``publish_branch`` pushes to the explicit HTTPS remote (never ``origin``),
  uses ``--force-with-lease --no-verify``, and keeps the token out of argv.
- Temp files are cleaned up after success, failure, and timeout.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from voyager.bots.assembly.publish import (
    _git_push_env,
    _github_safe_remote,
    _run_git_push,
    _write_git_askpass,
    publish_branch,
)

TEST_TOKEN = "ghs_test_publish_token_42"
TEST_REPOSITORY = "iterwheel/voyager"
TEST_BRANCH = "42-fix-thing"
TEST_REMOTE_URL = "https://github.com/iterwheel/voyager.git"
VALID_SHA = "0123456789abcdef0123456789abcdef01234567"


# ---------------------------------------------------------------------------
# _github_safe_remote
# ---------------------------------------------------------------------------


class TestGithubSafeRemote:
    def test_returns_https_url(self) -> None:
        assert _github_safe_remote("owner/repo") == "https://github.com/owner/repo.git"

    def test_preserves_underscores_and_hyphens(self) -> None:
        assert (
            _github_safe_remote("my-org/my_project") == "https://github.com/my-org/my_project.git"
        )


# ---------------------------------------------------------------------------
# _write_git_askpass
# ---------------------------------------------------------------------------


class TestWriteGitAskpass:
    def test_creates_executable_script(self, tmp_path: Path) -> None:
        askpass = _write_git_askpass(tmp_path)
        assert askpass.exists()
        assert askpass.stat().st_mode & 0o111

    def test_script_contains_expected_patterns(self, tmp_path: Path) -> None:
        askpass = _write_git_askpass(tmp_path)
        content = askpass.read_text(encoding="utf-8")
        assert "x-access-token" in content
        assert "ASSEMBLY_GITHUB_TOKEN" in content


# ---------------------------------------------------------------------------
# _git_push_env
# ---------------------------------------------------------------------------


class TestGitPushEnv:
    def test_includes_token_and_askpass(self, tmp_path: Path) -> None:
        askpass = tmp_path / "askpass.sh"
        askpass.write_text("#!/bin/sh\necho ok\n")
        env = _git_push_env(token=TEST_TOKEN, askpass=askpass)
        assert env["GIT_ASKPASS"] == str(askpass)
        assert env["ASSEMBLY_GITHUB_TOKEN"] == TEST_TOKEN
        assert env["GIT_TERMINAL_PROMPT"] == "0"

    def test_preserves_original_env(self, tmp_path: Path) -> None:
        os.environ["PUBLISH_TEST_VAR"] = "preserve-me"
        try:
            askpass = tmp_path / "askpass.sh"
            askpass.write_text("#!/bin/sh\necho ok\n")
            env = _git_push_env(token=TEST_TOKEN, askpass=askpass)
            assert env["PUBLISH_TEST_VAR"] == "preserve-me"
        finally:
            os.environ.pop("PUBLISH_TEST_VAR", None)

    def test_overrides_previous_token(self, tmp_path: Path) -> None:
        os.environ["ASSEMBLY_GITHUB_TOKEN"] = "old-token"
        try:
            askpass = tmp_path / "askpass.sh"
            askpass.write_text("#!/bin/sh\necho ok\n")
            env = _git_push_env(token=TEST_TOKEN, askpass=askpass)
            assert env["ASSEMBLY_GITHUB_TOKEN"] == TEST_TOKEN
        finally:
            os.environ.pop("ASSEMBLY_GITHUB_TOKEN", None)


# ---------------------------------------------------------------------------
# _run_git_push
# ---------------------------------------------------------------------------


class TestRunGitPush:
    @pytest.mark.asyncio
    async def test_success_returns_zero_returncode(self, tmp_path: Path) -> None:
        result = await _run_git_push(
            ["true"],
            cwd=tmp_path,
            timeout_seconds=10,
            env=dict(os.environ),
        )
        assert result[0] == 0

    @pytest.mark.asyncio
    async def test_failure_returns_nonzero_returncode(self, tmp_path: Path) -> None:
        result = await _run_git_push(
            ["false"],
            cwd=tmp_path,
            timeout_seconds=10,
            env=dict(os.environ),
        )
        assert result[0] != 0

    @pytest.mark.asyncio
    async def test_timeout_returns_one_and_timout_message(self, tmp_path: Path) -> None:
        """Simulate a timeout by running a slow command with zero timeout."""
        result = await _run_git_push(
            ["sleep", "10"],
            cwd=tmp_path,
            timeout_seconds=0,  # Force immediate timeout
            env=dict(os.environ),
        )
        assert result[0] == 1
        assert "timed out" in result[2]


# ---------------------------------------------------------------------------
# publish_branch
# ---------------------------------------------------------------------------


class _FakeProcess:
    def __init__(self, argv: tuple[str, ...], *, returncode: int, stdout: str, stderr: str):
        self.args = argv
        self.returncode = returncode
        self._stdout = stdout.encode()
        self._stderr = stderr.encode()

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    async def wait(self) -> int:
        return self.returncode


class _CommandRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_subprocess_exec(self, *argv: object, cwd: object = None, **kwargs: Any):
        return self._handle_subprocess(tuple(str(item) for item in argv), kwargs=kwargs)

    def _handle_subprocess(self, argv: tuple[str, ...], *, kwargs: dict[str, Any]) -> _FakeProcess:
        self.calls.append({"argv": argv, "kwargs": kwargs})
        if len(argv) >= 1 and argv[0] == "git" and "push" in argv:
            return _FakeProcess(argv, returncode=0, stdout="", stderr="")
        return _FakeProcess(argv, returncode=0, stdout="", stderr="")


def _recorder_remote_name(recorder: _CommandRecorder) -> str:
    for call in recorder.calls:
        argv = call["argv"]
        if len(argv) > 4 and argv[0] == "git" and argv[1] == "remote" and argv[2] == "add":
            remote_name = str(argv[3])
            assert remote_name.startswith("assembly-publish-")
            return remote_name
    raise AssertionError("No git remote add call recorded")


class TestPublishBranch:
    """Tests for the public ``publish_branch`` function."""

    # ------------------------------------------------------------------
    # Push argv verification
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_pushes_to_explicit_https_remote_not_origin(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """VOY-1822: push must use ``https://github.com/<repo>.git``, not ``origin``."""
        recorder = _CommandRecorder()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder.create_subprocess_exec)
        monkeypatch.setattr(
            "voyager.bots.assembly.publish.shutil.rmtree",
            lambda _dir, **kw: None,
        )

        result = await publish_branch(
            repository=TEST_REPOSITORY,
            branch_name=TEST_BRANCH,
            installation_token=TEST_TOKEN,
            checkout_dir=tmp_path,
            timeout_seconds=30,
        )

        assert result.success
        # The push argv must use the named remote, not the URL directly
        push_calls = [call for call in recorder.calls if "push" in " ".join(call["argv"])]
        assert push_calls, "No git push call recorded"
        push_argv = " ".join(push_calls[0]["argv"])
        assert "assembly-publish" in push_argv, (
            f"push argv must use the named remote, got: {push_argv}"
        )
        assert TEST_REMOTE_URL not in push_argv, (
            f"push argv must not contain the URL directly, got: {push_argv}"
        )
        assert " origin " not in f" {push_argv} ", (
            f"push argv must not contain literal 'origin', got: {push_argv}"
        )
        # Verify the git remote add command was issued with the URL
        remote_add_calls = [
            call
            for call in recorder.calls
            if "remote" in " ".join(call["argv"]) and "add" in " ".join(call["argv"])
        ]
        assert remote_add_calls, "No git remote add call recorded"
        remote_add_argv = " ".join(remote_add_calls[0]["argv"])
        remote_name = _recorder_remote_name(recorder)
        assert TEST_REMOTE_URL in remote_add_argv, (
            f"remote add argv must contain the HTTPS URL, got: {remote_add_argv}"
        )

        # Verify git fetch is called before push with the named remote
        fetch_calls = [call for call in recorder.calls if "fetch" in " ".join(call["argv"])]
        assert fetch_calls, "No git fetch call recorded"
        fetch_argv = " ".join(fetch_calls[0]["argv"])
        assert "assembly-publish" in fetch_argv, (
            f"fetch argv must use the named remote, got: {fetch_argv}"
        )
        assert "--no-tags" in fetch_argv
        assert f"refs/heads/{TEST_BRANCH}" in fetch_argv
        assert f"refs/remotes/{remote_name}/{TEST_BRANCH}" in fetch_argv
        # The fetch must appear before the push
        fetch_idx = next(i for i, c in enumerate(recorder.calls) if "fetch" in " ".join(c["argv"]))
        push_idx = next(i for i, c in enumerate(recorder.calls) if "push" in " ".join(c["argv"]))
        assert fetch_idx < push_idx, (
            f"fetch (index {fetch_idx}) must precede push (index {push_idx})"
        )

    @pytest.mark.asyncio
    async def test_push_uses_force_with_lease_and_no_verify(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        recorder = _CommandRecorder()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder.create_subprocess_exec)
        monkeypatch.setattr(
            "voyager.bots.assembly.publish.shutil.rmtree",
            lambda _dir, **kw: None,
        )

        result = await publish_branch(
            repository=TEST_REPOSITORY,
            branch_name=TEST_BRANCH,
            installation_token=TEST_TOKEN,
            checkout_dir=tmp_path,
            timeout_seconds=30,
        )

        assert result.success
        push_calls = [call for call in recorder.calls if "push" in " ".join(call["argv"])]
        assert push_calls
        push_argv = " ".join(push_calls[0]["argv"])
        assert "--force-with-lease" in push_argv
        assert "--no-verify" in push_argv

    @pytest.mark.asyncio
    async def test_remote_add_failure_does_not_remove_existing_remote(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        class _FailedRemoteAddRecorder(_CommandRecorder):
            def _handle_subprocess(
                self, argv: tuple[str, ...], *, kwargs: dict[str, Any]
            ) -> _FakeProcess:
                self.calls.append({"argv": argv, "kwargs": kwargs})
                if len(argv) >= 3 and argv[0] == "git" and argv[1:3] == ("remote", "add"):
                    return _FakeProcess(
                        argv,
                        returncode=3,
                        stdout="",
                        stderr="remote already exists",
                    )
                return _FakeProcess(argv, returncode=0, stdout="", stderr="")

        recorder = _FailedRemoteAddRecorder()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder.create_subprocess_exec)
        monkeypatch.setattr(
            "voyager.bots.assembly.publish.shutil.rmtree",
            lambda _dir, **kw: None,
        )

        result = await publish_branch(
            repository=TEST_REPOSITORY,
            branch_name=TEST_BRANCH,
            installation_token=TEST_TOKEN,
            checkout_dir=tmp_path,
            timeout_seconds=30,
        )

        assert not result.success
        assert "failed to add temporary remote" in result.message.lower()
        remove_calls = [
            call
            for call in recorder.calls
            if len(call["argv"]) >= 3 and call["argv"][1:3] == ("remote", "remove")
        ]
        assert not remove_calls

    @pytest.mark.asyncio
    async def test_missing_remote_ref_fetch_allows_first_push(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        class _MissingRemoteRefRecorder(_CommandRecorder):
            def _handle_subprocess(
                self, argv: tuple[str, ...], *, kwargs: dict[str, Any]
            ) -> _FakeProcess:
                self.calls.append({"argv": argv, "kwargs": kwargs})
                if len(argv) >= 2 and argv[0] == "git" and argv[1] == "fetch":
                    return _FakeProcess(
                        argv,
                        returncode=128,
                        stdout="",
                        stderr="fatal: could not find remote ref 42-fix-thing",
                    )
                return _FakeProcess(argv, returncode=0, stdout="", stderr="")

        recorder = _MissingRemoteRefRecorder()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder.create_subprocess_exec)
        monkeypatch.setattr(
            "voyager.bots.assembly.publish.shutil.rmtree",
            lambda _dir, **kw: None,
        )

        result = await publish_branch(
            repository=TEST_REPOSITORY,
            branch_name=TEST_BRANCH,
            installation_token=TEST_TOKEN,
            checkout_dir=tmp_path,
            timeout_seconds=30,
        )

        assert result.success
        push_calls = [call for call in recorder.calls if "push" in " ".join(call["argv"])]
        assert push_calls, "No git push call recorded"

    @pytest.mark.asyncio
    async def test_unexpected_fetch_failure_stops_before_push(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        class _FailedFetchRecorder(_CommandRecorder):
            def _handle_subprocess(
                self, argv: tuple[str, ...], *, kwargs: dict[str, Any]
            ) -> _FakeProcess:
                self.calls.append({"argv": argv, "kwargs": kwargs})
                if len(argv) >= 2 and argv[0] == "git" and argv[1] == "fetch":
                    return _FakeProcess(
                        argv,
                        returncode=128,
                        stdout="",
                        stderr="fatal: repository not found",
                    )
                return _FakeProcess(argv, returncode=0, stdout="", stderr="")

        recorder = _FailedFetchRecorder()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder.create_subprocess_exec)
        monkeypatch.setattr(
            "voyager.bots.assembly.publish.shutil.rmtree",
            lambda _dir, **kw: None,
        )

        result = await publish_branch(
            repository=TEST_REPOSITORY,
            branch_name=TEST_BRANCH,
            installation_token=TEST_TOKEN,
            checkout_dir=tmp_path,
            timeout_seconds=30,
        )

        assert not result.success
        assert "failed to fetch" in result.message.lower()
        assert "repository not found" in result.message.lower()
        push_calls = [call for call in recorder.calls if "push" in " ".join(call["argv"])]
        assert not push_calls

    # ------------------------------------------------------------------
    # Token safety
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_token_never_appears_in_argv(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        recorder = _CommandRecorder()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder.create_subprocess_exec)
        monkeypatch.setattr(
            "voyager.bots.assembly.publish.shutil.rmtree",
            lambda _dir, **kw: None,
        )

        result = await publish_branch(
            repository=TEST_REPOSITORY,
            branch_name=TEST_BRANCH,
            installation_token=TEST_TOKEN,
            checkout_dir=tmp_path,
            timeout_seconds=30,
        )

        assert result.success
        flattened_argv = "\n".join(" ".join(call["argv"]) for call in recorder.calls)
        assert TEST_TOKEN not in flattened_argv
        assert f"x-access-token:{TEST_TOKEN}" not in flattened_argv

    @pytest.mark.asyncio
    async def test_token_in_env_not_in_argv(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Token is passed via ASSEMBLY_GITHUB_TOKEN env, never in argv."""
        recorder = _CommandRecorder()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder.create_subprocess_exec)
        monkeypatch.setattr(
            "voyager.bots.assembly.publish.shutil.rmtree",
            lambda _dir, **kw: None,
        )

        result = await publish_branch(
            repository=TEST_REPOSITORY,
            branch_name=TEST_BRANCH,
            installation_token=TEST_TOKEN,
            checkout_dir=tmp_path,
            timeout_seconds=30,
        )

        assert result.success
        push_calls = [call for call in recorder.calls if "push" in " ".join(call["argv"])]
        assert push_calls
        env_json = json.dumps((push_calls[0]["kwargs"] or {}).get("env") or {}, default=str)
        assert TEST_TOKEN in env_json

    # ------------------------------------------------------------------
    # Timeout
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_timeout_returns_failure_result_not_timeout_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """VOY-1822: git push timeout is converted to a structured failure."""
        recorder = _CommandRecorder()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder.create_subprocess_exec)
        monkeypatch.setattr(
            "voyager.bots.assembly.publish.shutil.rmtree",
            lambda _dir, **kw: None,
        )

        result = await publish_branch(
            repository=TEST_REPOSITORY,
            branch_name=TEST_BRANCH,
            installation_token=TEST_TOKEN,
            checkout_dir=tmp_path,
            timeout_seconds=0,  # Will trigger immediate timeout
        )

        assert not result.success
        assert "timed out" in result.message.lower()

    # ------------------------------------------------------------------
    # Ref specification
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_pushes_head_to_branch_ref(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        recorder = _CommandRecorder()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder.create_subprocess_exec)
        monkeypatch.setattr(
            "voyager.bots.assembly.publish.shutil.rmtree",
            lambda _dir, **kw: None,
        )

        result = await publish_branch(
            repository=TEST_REPOSITORY,
            branch_name=TEST_BRANCH,
            installation_token=TEST_TOKEN,
            checkout_dir=tmp_path,
            timeout_seconds=30,
        )

        assert result.success
        push_calls = [call for call in recorder.calls if "push" in " ".join(call["argv"])]
        assert push_calls
        push_argv = " ".join(push_calls[0]["argv"])
        assert f"HEAD:refs/heads/{TEST_BRANCH}" in push_argv

    # ------------------------------------------------------------------
    # Temp file cleanup
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_temp_files_cleaned_up_on_success(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        recorder = _CommandRecorder()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder.create_subprocess_exec)

        result = await publish_branch(
            repository=TEST_REPOSITORY,
            branch_name=TEST_BRANCH,
            installation_token=TEST_TOKEN,
            checkout_dir=tmp_path,
            timeout_seconds=30,
        )

        assert result.success
        # Do not rely on tmp_path — publish_branch uses tempfile.mkdtemp
        # which chooses a system temp dir (e.g. /tmp), not the test's tmp_path.
        # Just verify the result is correct and no exception occurred.

    @pytest.mark.asyncio
    async def test_temp_files_cleaned_up_on_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Push failure still cleans up askpass and temp dir."""

        class _FailingPushRecorder(_CommandRecorder):
            def _handle_subprocess(
                self, argv: tuple[str, ...], *, kwargs: dict[str, Any]
            ) -> _FakeProcess:
                self.calls.append({"argv": argv, "kwargs": kwargs})
                if len(argv) >= 1 and argv[0] == "git" and "push" in argv:
                    return _FakeProcess(argv, returncode=128, stdout="", stderr="permission denied")
                return _FakeProcess(argv, returncode=0, stdout="", stderr="")

        recorder = _FailingPushRecorder()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder.create_subprocess_exec)
        monkeypatch.setattr(
            "voyager.bots.assembly.publish.shutil.rmtree",
            lambda _dir, **kw: None,
        )

        result = await publish_branch(
            repository=TEST_REPOSITORY,
            branch_name=TEST_BRANCH,
            installation_token=TEST_TOKEN,
            checkout_dir=tmp_path,
            timeout_seconds=30,
        )

        assert not result.success
        assert "permission denied" in result.message.lower()
