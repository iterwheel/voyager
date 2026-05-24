"""RED tests for VOY-1821 Stage 2 real Assembly OMP backend.

These tests specify the real ``pi-oh-my-pi-deepseek`` backend behavior.
They started RED while ``PiOhMyPiDeepSeekAdapter`` was still the Stage 1
placeholder and now guard the real OMP adapter contract.
"""

from __future__ import annotations

import asyncio
import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from voyager.bots.assembly import writeback as writeback_module
from voyager.bots.assembly.adapters import (
    AdapterExecutionContext,
    PiOhMyPiDeepSeekAdapter,
    select_execution_adapter,
)
from voyager.bots.assembly.constants import ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK
from voyager.bots.assembly.job_contract import build_job_contract

VALID_SHA = "0123456789abcdef0123456789abcdef01234567"
INSTALLATION_TOKEN = "ghs_stage2_real_omp_secret_token"
DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_WORKDIR_SUFFIX = ".voyager/state/assembly"


def _contract():
    return build_job_contract(
        issue={
            "number": 1821,
            "title": "[Feature]: real Assembly OMP backend",
            "body": "## Problem / Goal\n\nRun OMP for Assembly.\n\n"
            "## Acceptance Criteria\n\n- [ ] OMP edits and pushes a branch\n",
            "html_url": "https://example/issues/1821",
        },
        repository="iterwheel/voyager-sandbox",
        branch_name="1821-real-assembly-omp-backend",
        delivery_id="delivery-stage2-red",
    )


def _context(tmp_path: Path, *, token: str = INSTALLATION_TOKEN) -> AdapterExecutionContext:
    return AdapterExecutionContext(
        repository="iterwheel/voyager-sandbox",
        workdir=tmp_path,
        timeout_seconds=120,
        command_path="omp",
        installation_token=token,
    )


def _assert_no_secret(value: Any, secret: str = INSTALLATION_TOKEN) -> None:
    serialized = json.dumps(value, default=str, sort_keys=True)
    assert secret not in serialized
    assert "ghs_stage2" not in serialized


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
    def __init__(
        self,
        *,
        status_porcelain: str,
        omp_returncode: int = 0,
        omp_stdout: str = "OMP completed\n",
        omp_stderr: str = "",
        rev_parse_sha: str = VALID_SHA,
        remote_branch_exists: bool = False,
    ) -> None:
        self.status_porcelain = status_porcelain
        self.omp_returncode = omp_returncode
        self.omp_stdout = omp_stdout
        self.omp_stderr = omp_stderr
        self.rev_parse_sha = rev_parse_sha
        self.remote_branch_exists = remote_branch_exists
        self.calls: list[dict[str, Any]] = []

    async def create_subprocess_exec(self, *argv: object, cwd: object = None, **kwargs: Any):
        return self._record_process(tuple(str(item) for item in argv), cwd=cwd, kwargs=kwargs)

    async def create_subprocess_shell(self, command: str, cwd: object = None, **kwargs: Any):
        return self._record_process(tuple(shlex.split(command)), cwd=cwd, kwargs=kwargs)

    def run(self, argv: object, cwd: object = None, check: bool = False, **kwargs: Any):
        if isinstance(argv, str):
            normalized = tuple(shlex.split(argv))
        else:
            normalized = tuple(str(item) for item in argv)
        process = self._record_process(normalized, cwd=cwd, kwargs=kwargs)
        stdout = process._stdout.decode()
        stderr = process._stderr.decode()
        if check and process.returncode != 0:
            raise subprocess.CalledProcessError(
                process.returncode,
                normalized,
                output=stdout,
                stderr=stderr,
            )
        return subprocess.CompletedProcess(normalized, process.returncode, stdout, stderr)

    def command_calls(self, command_name: str) -> list[dict[str, Any]]:
        return [
            call
            for call in self.calls
            if call["argv"] and Path(call["argv"][0]).name == command_name
        ]

    def git_calls(self, subcommand: str) -> list[dict[str, Any]]:
        return [
            call
            for call in self.command_calls("git")
            if len(call["argv"]) > 1 and call["argv"][1] == subcommand
        ]

    def _record_process(
        self,
        argv: tuple[str, ...],
        *,
        cwd: object,
        kwargs: dict[str, Any],
    ) -> _FakeProcess:
        cwd_path = Path(str(cwd)) if cwd is not None else None
        self.calls.append({"argv": argv, "cwd": cwd_path, "kwargs": kwargs})
        if not argv:
            return _FakeProcess(argv, returncode=1, stdout="", stderr="empty command")

        command_name = Path(argv[0]).name
        if command_name == "git":
            return self._git_process(argv)
        if command_name == "omp":
            return _FakeProcess(
                argv,
                returncode=self.omp_returncode,
                stdout=self.omp_stdout,
                stderr=self.omp_stderr,
            )
        return _FakeProcess(argv, returncode=0, stdout="", stderr="")

    def _git_process(self, argv: tuple[str, ...]) -> _FakeProcess:
        if len(argv) > 1 and argv[1] == "clone":
            Path(argv[-1]).mkdir(parents=True, exist_ok=True)
        if len(argv) > 1 and argv[1] == "fetch":
            if self.remote_branch_exists:
                return _FakeProcess(argv, returncode=0, stdout="", stderr="")
            # Simulate git fetch failure for a non-existent remote ref
            # with the standard git error message.
            fetch_ref = argv[-1] if len(argv) > 2 else "unknown"
            return _FakeProcess(
                argv,
                returncode=128,
                stdout="",
                stderr=f"fatal: couldn't find remote ref {fetch_ref}\n",
            )
        if "rev-parse" in argv and "--verify" in argv:
            return _FakeProcess(
                argv,
                returncode=0 if self.remote_branch_exists else 1,
                stdout=f"{self.rev_parse_sha}\n" if self.remote_branch_exists else "",
                stderr="",
            )
        if "status" in argv and "--porcelain" in argv:
            return _FakeProcess(argv, returncode=0, stdout=self.status_porcelain, stderr="")
        if "rev-parse" in argv and "HEAD" in argv:
            return _FakeProcess(argv, returncode=0, stdout=f"{self.rev_parse_sha}\n", stderr="")
        return _FakeProcess(argv, returncode=0, stdout="", stderr="")


def _install_command_fakes(monkeypatch: pytest.MonkeyPatch, recorder: _CommandRecorder) -> None:
    monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder.create_subprocess_exec)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", recorder.create_subprocess_shell)
    monkeypatch.setattr(subprocess, "run", recorder.run)


def test_select_execution_adapter_pi_backend_requires_installation_token() -> None:
    adapter = select_execution_adapter(ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK)

    assert isinstance(adapter, PiOhMyPiDeepSeekAdapter)
    assert adapter.requires_installation_token is True


@pytest.mark.asyncio
async def test_build_adapter_context_defaults_to_omp_and_fetches_installation_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ASSEMBLY_PI_COMMAND_PATH", raising=False)
    monkeypatch.delenv("ASSEMBLY_PI_TIMEOUT_SECONDS", raising=False)
    client = AsyncMock()
    client.installation_token = AsyncMock(return_value=INSTALLATION_TOKEN)
    adapter = select_execution_adapter(ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK)

    context = await writeback_module._build_adapter_context(
        client,
        adapter,
        "iterwheel/voyager-sandbox",
        is_dry_run=False,
    )

    assert context.command_path == "omp"
    assert context.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert str(context.workdir).endswith(DEFAULT_WORKDIR_SUFFIX)
    assert context.installation_token == INSTALLATION_TOKEN
    client.installation_token.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_adapter_context_reads_omp_command_and_timeout_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ASSEMBLY_PI_COMMAND_PATH", "/custom/bin/omp")
    monkeypatch.setenv("ASSEMBLY_PI_TIMEOUT_SECONDS", "123")
    monkeypatch.setenv("ASSEMBLY_PI_WORKDIR", "~/custom-assembly-workdir")
    adapter = select_execution_adapter(ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK)

    context = await writeback_module._build_adapter_context(
        AsyncMock(),
        adapter,
        "iterwheel/voyager-sandbox",
        is_dry_run=False,
    )

    assert context.command_path == "/custom/bin/omp"
    assert context.timeout_seconds == 123
    assert str(context.workdir).endswith("custom-assembly-workdir")


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout_value", ["", "0", "-1", "not-an-int"])
async def test_build_adapter_context_invalid_timeout_env_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
    timeout_value: str,
) -> None:
    monkeypatch.setenv("ASSEMBLY_PI_TIMEOUT_SECONDS", timeout_value)
    adapter = select_execution_adapter(ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK)

    context = await writeback_module._build_adapter_context(
        AsyncMock(),
        adapter,
        "iterwheel/voyager-sandbox",
        is_dry_run=False,
    )

    assert context.timeout_seconds == DEFAULT_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_pi_adapter_executes_omp_in_clone_pushes_branch_and_returns_sha(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    recorder = _CommandRecorder(status_porcelain="M voyager/example.py\n")
    _install_command_fakes(monkeypatch, recorder)
    adapter = PiOhMyPiDeepSeekAdapter()

    result = await adapter.execute(_contract(), _context(tmp_path))

    assert result.status == "executed"
    assert result.commit_shas == [VALID_SHA]
    assert re.fullmatch(r"[0-9a-f]{40}", result.commit_shas[0])
    assert result.details["checkout_dir"]
    assert not Path(str(result.details["checkout_dir"])).exists()
    assert not list(tmp_path.glob("assembly-omp-*"))

    omp_calls = recorder.command_calls("omp")
    assert len(omp_calls) == 1
    omp_argv = omp_calls[0]["argv"]
    assert "-p" in omp_argv
    assert omp_calls[0]["cwd"] is not None
    assert omp_calls[0]["cwd"] != tmp_path
    assert INSTALLATION_TOKEN not in json.dumps(omp_calls[0]["kwargs"], default=str)

    assert recorder.git_calls("clone")
    push_calls = recorder.git_calls("push")
    assert push_calls
    # VOY-1822: push must use a dedicated named remote, not the URL directly.
    push_argv = " ".join(push_calls[0]["argv"])
    assert "assembly-publish" in push_argv, f"push argv must use the named remote, got: {push_argv}"
    remote_url = "https://github.com/iterwheel/voyager-sandbox.git"
    assert remote_url not in push_argv, (
        f"push argv must not contain the URL directly, got: {push_argv}"
    )
    assert " origin " not in f" {push_argv} ", (
        f"push argv must not contain literal 'origin', got: {push_argv}"
    )
    branch = _contract().branch_name
    lease_fetch_calls = [
        call
        for call in recorder.git_calls("fetch")
        if any(arg.startswith("assembly-publish-") for arg in call["argv"])
        and "--no-tags" in call["argv"]
        and any(
            arg.startswith(f"refs/heads/{branch}:refs/remotes/assembly-publish-")
            and arg.endswith(f"/{branch}")
            for arg in call["argv"]
        )
    ]
    assert lease_fetch_calls, "No Assembly publish lease fetch call recorded"
    fetch_idx = recorder.calls.index(lease_fetch_calls[0])
    push_idx = recorder.calls.index(push_calls[0])
    assert fetch_idx < push_idx
    # Verify the git remote add was issued with the HTTPS URL
    remote_add_calls = [
        call
        for call in recorder.calls
        if "remote" in " ".join(call["argv"]) and "add" in " ".join(call["argv"])
    ]
    assert remote_add_calls, "No git remote add call recorded"
    remote_add_argv = " ".join(remote_add_calls[0]["argv"])
    assert remote_url in remote_add_argv, (
        f"remote add argv must contain the HTTPS URL, got: {remote_add_argv}"
    )
    assert any(_contract().branch_name in " ".join(call["argv"]) for call in push_calls)
    flattened_argv = "\n".join(" ".join(call["argv"]) for call in recorder.calls)
    assert INSTALLATION_TOKEN not in flattened_argv
    assert f"x-access-token:{INSTALLATION_TOKEN}" not in flattened_argv
    for call in recorder.calls:
        env_json = json.dumps((call["kwargs"] or {}).get("env") or {}, default=str)
        is_auth_git = (
            call in recorder.git_calls("clone")
            or call in recorder.git_calls("fetch")
            or call in recorder.git_calls("push")
            or (
                call in recorder.git_calls("remote")
                and len(call["argv"]) > 2
                and call["argv"][2] == "add"
            )
        )
        if is_auth_git:
            assert INSTALLATION_TOKEN in env_json
        else:
            assert INSTALLATION_TOKEN not in env_json


@pytest.mark.asyncio
async def test_pi_adapter_reuses_existing_remote_assembly_branch_before_push(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    recorder = _CommandRecorder(
        status_porcelain="M voyager/example.py\n",
        remote_branch_exists=True,
    )
    _install_command_fakes(monkeypatch, recorder)
    adapter = PiOhMyPiDeepSeekAdapter()

    result = await adapter.execute(_contract(), _context(tmp_path))

    assert result.status == "executed"
    fetch_calls = recorder.git_calls("fetch")
    assert fetch_calls
    assert any(_contract().branch_name in " ".join(call["argv"]) for call in fetch_calls)
    checkout_calls = recorder.git_calls("checkout")
    assert checkout_calls
    assert checkout_calls[0]["argv"][-1] == f"refs/remotes/origin/{_contract().branch_name}"


@pytest.mark.asyncio
async def test_pi_adapter_uses_askpass_without_installation_token_in_argv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token = "ghs_stage2_argv_safety_secret"
    recorder = _CommandRecorder(status_porcelain="M voyager/example.py\n")
    _install_command_fakes(monkeypatch, recorder)
    adapter = PiOhMyPiDeepSeekAdapter()

    result = await adapter.execute(_contract(), _context(tmp_path, token=token))

    assert result.status == "executed"
    assert recorder.calls
    for call in recorder.calls:
        argv = call["argv"]
        argv_text = " ".join(argv)
        assert token not in argv_text
        assert f"x-access-token:{token}" not in argv_text

    clone_calls = recorder.git_calls("clone")
    assert clone_calls
    assert "https://github.com/iterwheel/voyager-sandbox.git" in clone_calls[0]["argv"]


@pytest.mark.asyncio
async def test_pi_adapter_subprocess_failure_returns_failed_and_sanitizes_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token = "ghs_stage2_subprocess_failure_secret"
    recorder = _CommandRecorder(
        status_porcelain="",
        omp_returncode=42,
        omp_stdout=f"stdout accidentally mentioned {token}",
        omp_stderr=f"stderr accidentally mentioned {token}",
    )
    _install_command_fakes(monkeypatch, recorder)
    adapter = PiOhMyPiDeepSeekAdapter()

    result = await adapter.execute(_contract(), _context(tmp_path, token=token))

    assert result.status == "failed"
    assert result.commit_shas == []
    assert "omp" in result.summary.lower()
    _assert_no_secret(result.summary, token)
