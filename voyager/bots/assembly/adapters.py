"""Assembly bot — pluggable execution adapters.

Per VOY-1817 Surface 7 and D1.  The adapter seam isolates the
GitHub-mutation side of Assembly from the subprocess-execution side.  The
bot ships with a deterministic ``DryRunAdapter`` plus a real OMP-backed
``pi-oh-my-pi-deepseek`` adapter gated by operator environment.

Selection happens via the ``ASSEMBLY_EXECUTION_BACKEND`` env var; the
default is ``dry-run``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .constants import (
    ASSEMBLY_BACKEND_DRY_RUN,
    ASSEMBLY_BACKEND_FAKE_SUBPROCESS,
    ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK,
    ASSEMBLY_EXECUTION_BACKEND_ENV,
    ASSEMBLY_FAKE_SUBPROCESS_ALLOW_ENV,
    ASSEMBLY_FAKE_SUBPROCESS_OUTPUT_ENV,
)
from .job_contract import AssemblyJobContract
from .publish import publish_branch

_COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class AdapterResult:
    """The structured outcome an adapter must return."""

    status: str  # "dry_run" | "executed" | "no_changes" | "failed"
    commit_shas: list[str] = field(default_factory=list)
    summary: str = ""
    # Optional extra metadata the adapter wants to surface (e.g., the
    # planned diff). Kept opaque to the writeback dispatcher.
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AdapterExecutionContext:
    """Runtime-only execution metadata passed to adapters.

    The installation token is intentionally excluded from ``repr`` and
    ``to_safe_dict`` so it cannot be rendered into logs, comments, or
    serialized job-contract payloads by accident.
    """

    repository: str
    workdir: Path
    timeout_seconds: int
    command_path: str | None
    installation_token: str | None = field(default=None, repr=False)
    resume_requested: bool = False
    session_mode: str = "fresh"
    resume_session_id: str | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "workdir": str(self.workdir),
            "timeout_seconds": self.timeout_seconds,
            "command_path": self.command_path,
            "resume_requested": self.resume_requested,
            "session_mode": self.session_mode,
        }


class ExecutionAdapter(Protocol):
    """Protocol every adapter must satisfy.

    Adapters MUST push commits to the source repository before returning
    `commit_shas`; the writeback dispatcher passes `commit_shas[-1]` to
    `create_branch_ref` and assumes the SHA already exists on the remote.
    Adapters that produce commits locally only (without pushing) will cause
    the branch-create step to fail with 422 'Object does not exist'.
    """

    name: str
    requires_installation_token: bool = False
    supports_resume: bool = False

    async def execute(
        self,
        contract: AssemblyJobContract,
        context: AdapterExecutionContext | None = None,
    ) -> AdapterResult: ...


@dataclass
class DryRunAdapter:
    """Records the planned contract without spawning a subprocess.

    Returns ``status="dry_run"`` and an empty ``commit_shas`` list so the
    writeback dispatcher knows there is no branch/PR work to do.
    """

    name: str = ASSEMBLY_BACKEND_DRY_RUN
    requires_installation_token: bool = False
    supports_resume: bool = False
    last_contract: AssemblyJobContract | None = None

    async def execute(
        self,
        contract: AssemblyJobContract,
        context: AdapterExecutionContext | None = None,
    ) -> AdapterResult:
        _ = context
        self.last_contract = contract
        return AdapterResult(
            status="dry_run",
            commit_shas=[],
            summary="Dry-run adapter recorded the contract; no commits produced.",
            details={"recorded": contract.to_dict()},
        )


@dataclass(frozen=True)
class _ProcessResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class PiOhMyPiDeepSeekAdapter:
    """Real OMP subprocess backend for Assembly.

    The adapter clones the target repository, lets ``omp`` work in an
    isolated checkout, commits any dirty tree, verifies the result, pushes
    the Assembly branch, and only then returns a commit SHA to the dispatcher.
    """

    name: str = ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK
    requires_installation_token: bool = True
    supports_resume: bool = True

    async def execute(
        self,
        contract: AssemblyJobContract,
        context: AdapterExecutionContext | None = None,
    ) -> AdapterResult:
        if context is None:
            return AdapterResult(
                status="failed",
                commit_shas=[],
                summary="pi-oh-my-pi-deepseek requires an adapter execution context.",
            )
        validation_error = _validate_pi_context(context)
        if validation_error is not None:
            return AdapterResult(status="failed", commit_shas=[], summary=validation_error)

        repository = context.repository.strip()
        command_path = str(context.command_path or "").strip()
        token = str(context.installation_token or "")
        timeout_seconds = context.timeout_seconds if context.timeout_seconds > 0 else 900
        details: dict[str, Any] = {
            "workdir": str(context.workdir),
            "checkout_dir": None,
            "omp_session_jsonl_path": None,
            "exported_html_path": None,
        }
        if context.session_mode == "resumed" and context.resume_session_id:
            details["session_id"] = context.resume_session_id
        temp_root_path: Path | None = None

        try:
            context.workdir.mkdir(parents=True, exist_ok=True)
            temp_root_path = Path(
                tempfile.mkdtemp(
                    prefix="assembly-omp-",
                    dir=context.workdir,
                )
            )
            checkout_dir = temp_root_path / "repo"
            details["checkout_dir"] = str(checkout_dir)
            safe_remote = _github_safe_remote(repository)
            askpass = _write_git_askpass(temp_root_path)
            git_env = _git_env()
            git_auth_env = _git_env(token=token, askpass=askpass)

            clone = await _run_exec(
                ["git", "clone", safe_remote, str(checkout_dir)],
                cwd=context.workdir,
                timeout_seconds=timeout_seconds,
                env=git_auth_env,
            )
            if clone.returncode != 0:
                return _failed_pi_result(
                    "Git clone failed for Assembly OMP backend.", token, details
                )

            git_user = await _run_exec(
                ["git", "config", "user.name", "iterwheel-assembly[bot]"],
                cwd=checkout_dir,
                timeout_seconds=timeout_seconds,
                env=git_env,
            )
            if git_user.returncode != 0:
                return _failed_pi_result(
                    "Git user.name config failed for Assembly OMP backend.",
                    token,
                    details,
                )
            git_email = await _run_exec(
                [
                    "git",
                    "config",
                    "user.email",
                    "3821103+iterwheel-assembly[bot]@users.noreply.github.com",
                ],
                cwd=checkout_dir,
                timeout_seconds=timeout_seconds,
                env=git_env,
            )
            if git_email.returncode != 0:
                return _failed_pi_result(
                    "Git user.email config failed for Assembly OMP backend.",
                    token,
                    details,
                )

            branch_start_ref = await _branch_start_ref(
                contract,
                checkout_dir,
                timeout_seconds,
                git_env=git_env,
                git_auth_env=git_auth_env,
            )

            checkout = await _run_exec(
                [
                    "git",
                    "checkout",
                    "-B",
                    contract.branch_name,
                    branch_start_ref,
                ],
                cwd=checkout_dir,
                timeout_seconds=timeout_seconds,
                env=git_env,
            )
            if checkout.returncode != 0:
                return _failed_pi_result(
                    "Git checkout failed for Assembly OMP backend.",
                    token,
                    details,
                )

            base_sha = await _git_head_sha(checkout_dir, timeout_seconds, git_env)
            if base_sha is None:
                return _failed_pi_result(
                    "Could not read base commit for Assembly OMP backend.",
                    token,
                    details,
                )

            prompt = _build_omp_prompt(contract)
            omp_argv = [command_path, "-p", prompt]
            if context.session_mode == "resumed" and context.resume_session_id:
                omp_argv = [command_path, "-p", f"--resume={context.resume_session_id}", prompt]
            omp = await _run_exec(
                omp_argv,
                cwd=checkout_dir,
                timeout_seconds=timeout_seconds,
                env=_omp_env(),
            )
            details["omp_session_jsonl_path"] = _latest_omp_session_jsonl(checkout_dir)
            if omp.returncode != 0:
                return _failed_pi_result(
                    f"OMP subprocess failed with exit code {omp.returncode}.",
                    token,
                    details,
                )

            status = await _run_exec(
                ["git", "status", "--porcelain"],
                cwd=checkout_dir,
                timeout_seconds=timeout_seconds,
                env=git_env,
            )
            if status.returncode != 0:
                return _failed_pi_result(
                    "Git status failed for Assembly OMP backend.",
                    token,
                    details,
                )

            dirty_tree = bool(status.stdout.strip())
            if dirty_tree:
                staged = await _run_exec(
                    ["git", "add", "-A"],
                    cwd=checkout_dir,
                    timeout_seconds=timeout_seconds,
                    env=git_env,
                )
                if staged.returncode != 0:
                    return _failed_pi_result(
                        "Git add failed for Assembly OMP backend.",
                        token,
                        details,
                    )
                commit = await _run_exec(
                    [
                        "git",
                        "commit",
                        "-m",
                        f"Implement #{contract.issue_number} via Assembly",
                    ],
                    cwd=checkout_dir,
                    timeout_seconds=timeout_seconds,
                    env=git_env,
                )
                if commit.returncode != 0:
                    return _failed_pi_result(
                        "Git commit failed for Assembly OMP backend.",
                        token,
                        details,
                    )

            head_sha = await _git_head_sha(checkout_dir, timeout_seconds, git_env)
            if head_sha is None:
                return _failed_pi_result(
                    "Git rev-parse failed for Assembly OMP backend.",
                    token,
                    details,
                )
            if not _COMMIT_SHA_RE.fullmatch(head_sha):
                return _failed_pi_result(
                    "Assembly OMP backend produced an invalid commit SHA.",
                    token,
                    details,
                )
            if not dirty_tree and head_sha == base_sha:
                return AdapterResult(
                    status="no_changes",
                    commit_shas=[],
                    summary="OMP completed with no repository changes.",
                    details=details,
                )

            verification = await _run_verification_commands(
                contract,
                checkout_dir,
                timeout_seconds,
                git_env,
            )
            if verification is not None:
                return _failed_pi_result(verification, token, details)

            publish_result = await publish_branch(
                repository=repository,
                branch_name=contract.branch_name,
                installation_token=token,
                checkout_dir=checkout_dir,
                timeout_seconds=timeout_seconds,
            )
            if not publish_result.success:
                return _failed_pi_result(
                    f"Git push failed for Assembly OMP backend: {publish_result.message}",
                    token,
                    details,
                )

            return AdapterResult(
                status="executed",
                commit_shas=[head_sha],
                summary="OMP completed, committed changes, and pushed the Assembly branch.",
                details=details,
            )
        except TimeoutError:
            return _failed_pi_result("Assembly OMP backend timed out.", token, details)
        except OSError:
            return _failed_pi_result(
                "Assembly OMP backend could not start a subprocess.", token, details
            )
        finally:
            if temp_root_path is not None:
                shutil.rmtree(temp_root_path, ignore_errors=True)


def _validate_pi_context(context: AdapterExecutionContext) -> str | None:
    if not context.repository.strip():
        return "pi-oh-my-pi-deepseek requires a repository."
    if not str(context.command_path or "").strip():
        return "pi-oh-my-pi-deepseek requires an OMP command path."
    if not str(context.installation_token or "").strip():
        return "pi-oh-my-pi-deepseek requires a GitHub installation token."
    return None


def _github_safe_remote(repository: str) -> str:
    return f"https://github.com/{repository}.git"


def _write_git_askpass(temp_root: Path) -> Path:
    askpass = temp_root / "git-askpass.sh"
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


def _git_env(*, token: str | None = None, askpass: Path | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if token and askpass is not None:
        env["GIT_ASKPASS"] = str(askpass)
        env["ASSEMBLY_GITHUB_TOKEN"] = token
    return env


def _omp_env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


async def _run_exec(
    argv: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: dict[str, str],
) -> _ProcessResult:
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
        raise
    return _ProcessResult(
        returncode=int(process.returncode or 0),
        stdout=stdout_raw.decode(errors="replace"),
        stderr=stderr_raw.decode(errors="replace"),
    )


async def _run_shell(
    command: str,
    *,
    cwd: Path,
    timeout_seconds: int,
    env: dict[str, str],
) -> _ProcessResult:
    process = await asyncio.create_subprocess_shell(
        command,
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
        raise
    return _ProcessResult(
        returncode=int(process.returncode or 0),
        stdout=stdout_raw.decode(errors="replace"),
        stderr=stderr_raw.decode(errors="replace"),
    )


async def _git_head_sha(
    checkout_dir: Path,
    timeout_seconds: int,
    env: dict[str, str],
) -> str | None:
    result = await _run_exec(
        ["git", "rev-parse", "HEAD"],
        cwd=checkout_dir,
        timeout_seconds=timeout_seconds,
        env=env,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


async def _branch_start_ref(
    contract: AssemblyJobContract,
    checkout_dir: Path,
    timeout_seconds: int,
    *,
    git_env: dict[str, str],
    git_auth_env: dict[str, str],
) -> str:
    fetch = await _run_exec(
        [
            "git",
            "fetch",
            "origin",
            f"+refs/heads/{contract.branch_name}:refs/remotes/origin/{contract.branch_name}",
        ],
        cwd=checkout_dir,
        timeout_seconds=timeout_seconds,
        env=git_auth_env,
    )
    if fetch.returncode != 0:
        return f"origin/{contract.base_branch}"

    remote_branch_ref = f"refs/remotes/origin/{contract.branch_name}"
    verify = await _run_exec(
        ["git", "rev-parse", "--verify", "--quiet", remote_branch_ref],
        cwd=checkout_dir,
        timeout_seconds=timeout_seconds,
        env=git_env,
    )
    if verify.returncode == 0:
        return remote_branch_ref
    return f"origin/{contract.base_branch}"


async def _run_verification_commands(
    contract: AssemblyJobContract,
    checkout_dir: Path,
    timeout_seconds: int,
    env: dict[str, str],
) -> str | None:
    # Trust boundary: these shell commands come from the D9-locked default
    # command set or trusted operator runtime config, not user/model-controlled
    # input.
    for command in contract.verification_commands:
        result = await _run_shell(
            command,
            cwd=checkout_dir,
            timeout_seconds=timeout_seconds,
            env=env,
        )
        if result.returncode != 0:
            return f"Verification command failed: {command}"
    return None


def _build_omp_prompt(contract: AssemblyJobContract) -> str:
    acceptance = "\n".join(f"- {item}" for item in contract.acceptance_criteria) or "- None"
    forbidden = "\n".join(f"- {item}" for item in contract.forbidden_operations) or "- None"
    verification = "\n".join(f"- {item}" for item in contract.verification_commands) or "- None"
    return (
        "You are Assembly implementing a GitHub issue in the current checkout.\n"
        f"Repository: {contract.repository}\n"
        f"Issue: #{contract.issue_number} {contract.issue_title}\n"
        f"Issue URL: {contract.issue_url}\n"
        f"Branch: {contract.branch_name}\n\n"
        "Task summary:\n"
        f"{contract.task_summary}\n\n"
        "Acceptance criteria:\n"
        f"{acceptance}\n\n"
        "Forbidden operations:\n"
        f"{forbidden}\n\n"
        "Verification commands:\n"
        f"{verification}\n\n"
        "Work only in this checkout on the current branch. Make the smallest production "
        "changes needed, run the verification commands when practical, commit changes "
        "on the current branch, and do not push. The Assembly adapter will push after "
        "validation."
    )


_GITHUB_TOKEN_RE = re.compile(r"gh[opsru]_[A-Za-z0-9_]+")


def _sanitize_for_result(value: str, secret: str) -> str:
    sanitized = value.replace(secret, "[redacted]") if secret else value
    return _GITHUB_TOKEN_RE.sub("[redacted]", sanitized)


def _sanitize_details_for_result(value: Any, secret: str) -> Any:
    if isinstance(value, str):
        return _sanitize_for_result(value, secret)
    if isinstance(value, list):
        return [_sanitize_details_for_result(item, secret) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_details_for_result(item, secret) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_details_for_result(item, secret) for key, item in value.items()}
    return value


def _latest_omp_session_jsonl(checkout_dir: Path) -> str | None:
    """Return the newest OMP session transcript path for this checkout, if any."""
    session_root = Path.home() / ".omp" / "agent" / "sessions"
    if not session_root.exists():
        return None
    temp_root_name = checkout_dir.parent.name
    candidates = list(session_root.glob(f"*{temp_root_name}*/*.jsonl"))
    newest: Path | None = None
    newest_mtime = -1.0
    for candidate in candidates:
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        if newest is None or mtime > newest_mtime:
            newest = candidate
            newest_mtime = mtime
    if newest is None:
        return None
    return str(newest)


def _failed_pi_result(
    summary: str,
    secret: str,
    details: dict[str, Any] | None = None,
) -> AdapterResult:
    return AdapterResult(
        status="failed",
        commit_shas=[],
        summary=_sanitize_for_result(summary, secret),
        details=_sanitize_details_for_result(details or {}, secret),
    )


@dataclass
class FakeSubprocessAdapter:
    """Deterministic subprocess-shaped backend for dispatcher verification.

    This adapter never spawns ``omp`` or any other command. It reads a JSON
    fixture from ``ASSEMBLY_FAKE_SUBPROCESS_OUTPUT`` and returns that shaped
    result after applying the same commit-SHA safety boundary the real
    subprocess backend must satisfy.
    """

    name: str = ASSEMBLY_BACKEND_FAKE_SUBPROCESS
    requires_installation_token: bool = False
    supports_resume: bool = True

    async def execute(
        self,
        contract: AssemblyJobContract,
        context: AdapterExecutionContext | None = None,
    ) -> AdapterResult:
        _ = contract
        if not _truthy_env(ASSEMBLY_FAKE_SUBPROCESS_ALLOW_ENV):
            return AdapterResult(
                status="failed",
                commit_shas=[],
                summary=f"{ASSEMBLY_FAKE_SUBPROCESS_ALLOW_ENV} must be truthy to use fake subprocess.",
            )

        raw = os.environ.get(ASSEMBLY_FAKE_SUBPROCESS_OUTPUT_ENV)
        if not raw:
            return AdapterResult(
                status="failed",
                commit_shas=[],
                summary=f"{ASSEMBLY_FAKE_SUBPROCESS_OUTPUT_ENV} is not set.",
            )

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return AdapterResult(
                status="failed",
                commit_shas=[],
                summary="Malformed fake subprocess output.",
            )
        if not isinstance(payload, dict):
            return AdapterResult(
                status="failed",
                commit_shas=[],
                summary="Malformed fake subprocess output: expected a JSON object.",
            )

        status = str(payload.get("status") or "").strip().lower()
        summary = str(payload.get("summary") or "")
        if status == "no_changes":
            return AdapterResult(
                status="no_changes",
                commit_shas=[],
                summary=summary or "Fake subprocess reported no changes.",
                details=_fake_details(payload, context),
            )
        if status == "failed":
            return AdapterResult(
                status="failed",
                commit_shas=[],
                summary=summary or "Fake subprocess reported failure.",
                details=_fake_details(payload, context),
            )
        if status != "executed":
            return AdapterResult(
                status="failed",
                commit_shas=[],
                summary="Malformed fake subprocess output: unsupported status.",
            )

        commit_shas = payload.get("commit_shas")
        if not isinstance(commit_shas, list) or not commit_shas:
            return AdapterResult(
                status="failed",
                commit_shas=[],
                summary="Malformed fake subprocess output: executed requires commit_shas.",
            )
        safe_shas: list[str] = []
        for item in commit_shas:
            if not isinstance(item, str) or not _COMMIT_SHA_RE.fullmatch(item):
                return AdapterResult(
                    status="failed",
                    commit_shas=[],
                    summary="Fake subprocess output contained an invalid commit SHA.",
                )
            safe_shas.append(item)

        return AdapterResult(
            status="executed",
            commit_shas=safe_shas,
            summary=summary or "Fake subprocess reported commits.",
            details=_fake_details(payload, context),
        )


def _fake_details(
    payload: dict[str, Any], context: AdapterExecutionContext | None
) -> dict[str, Any]:
    raw = payload.get("details")
    details = dict(raw) if isinstance(raw, dict) else {}
    if context and context.session_mode == "resumed" and context.resume_session_id:
        details.setdefault("session_id", context.resume_session_id)
        details.setdefault("resumed", True)
    return details


def _truthy_env(name: str) -> bool:
    value = os.environ.get(name)
    if value is None:
        return False
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


def select_execution_adapter(backend: str | None = None) -> ExecutionAdapter:
    """Return an adapter instance for the requested backend.

    When ``backend`` is None, reads ``ASSEMBLY_EXECUTION_BACKEND`` from the
    environment.  Unknown / empty values fall back to ``dry-run``.
    """
    chosen = (backend or os.environ.get(ASSEMBLY_EXECUTION_BACKEND_ENV, "")).strip().lower()
    if chosen == ASSEMBLY_BACKEND_FAKE_SUBPROCESS:
        return FakeSubprocessAdapter()
    if chosen == ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK:
        return PiOhMyPiDeepSeekAdapter()
    return DryRunAdapter()
