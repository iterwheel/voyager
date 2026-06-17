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
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from voyager.core.redaction import sanitize_public_text

from .ac_spotcheck import AcceptanceSpotCheckResult, check_acceptance_exact_tokens
from .constants import (
    ASSEMBLY_AC_SPOTCHECK_ENV,
    ASSEMBLY_BACKEND_DRY_RUN,
    ASSEMBLY_BACKEND_FAKE_SUBPROCESS,
    ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK,
    ASSEMBLY_EXECUTION_BACKEND_ENV,
    ASSEMBLY_FAKE_SUBPROCESS_ALLOW_ENV,
    ASSEMBLY_FAKE_SUBPROCESS_OUTPUT_ENV,
)
from .job_contract import AssemblyJobContract
from .maturity import GateMaturity
from .publish import publish_branch

_COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_AC_SPOTCHECK_MATURITY: GateMaturity = GateMaturity.L3
"""Explicit maturity for the acceptance-criteria spotcheck gate.

This gate was shipped as L3 (blocking) from day one via the #152 AC
spotcheck.  New gates should default to ``GateMaturity.L1`` so they
gradually earn their blocking power.
"""
_FAILURE_TAIL_LIMIT = 600


@dataclass(frozen=True)
class AdapterResult:
    """The structured outcome an adapter must return."""

    status: str  # "dry_run" | "executed" | "no_changes" | "failed" | "blocked"
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
    audit_id: str | None = None
    phase: str = "implementer"

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "workdir": str(self.workdir),
            "timeout_seconds": self.timeout_seconds,
            "command_path": self.command_path,
            "resume_requested": self.resume_requested,
            "session_mode": self.session_mode,
            "audit_id": self.audit_id,
            "phase": self.phase,
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
    timed_out: bool = False


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
                    "Git clone failed for Assembly OMP backend.",
                    token,
                    details,
                    failure_diagnostic=_diagnostic_from_process(
                        phase="clone",
                        command_category="git",
                        command="git clone",
                        process=clone,
                        secret=token,
                    ),
                    temp_root_path=temp_root_path,
                    contract=contract,
                    context=context,
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
                    failure_diagnostic=_diagnostic_from_process(
                        phase="git_config",
                        command_category="git",
                        command="git config user.name",
                        process=git_user,
                        secret=token,
                    ),
                    temp_root_path=temp_root_path,
                    contract=contract,
                    context=context,
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
                    failure_diagnostic=_diagnostic_from_process(
                        phase="git_config",
                        command_category="git",
                        command="git config user.email",
                        process=git_email,
                        secret=token,
                    ),
                    temp_root_path=temp_root_path,
                    contract=contract,
                    context=context,
                )

            branch_start_ref, branch_start_failure = await _branch_start_ref(
                contract,
                checkout_dir,
                timeout_seconds,
                git_env=git_env,
                git_auth_env=git_auth_env,
                secret=token,
            )
            if branch_start_failure is not None:
                return _failed_pi_result(
                    "Git branch-start fetch failed for Assembly OMP backend.",
                    token,
                    details,
                    failure_diagnostic=branch_start_failure,
                    temp_root_path=temp_root_path,
                    contract=contract,
                    context=context,
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
                    failure_diagnostic=_diagnostic_from_process(
                        phase="checkout",
                        command_category="git",
                        command="git checkout",
                        process=checkout,
                        secret=token,
                    ),
                    temp_root_path=temp_root_path,
                    contract=contract,
                    context=context,
                )

            base_sha = await _git_head_sha(checkout_dir, timeout_seconds, git_env)
            if base_sha is None:
                return _failed_pi_result(
                    "Could not read base commit for Assembly OMP backend.",
                    token,
                    details,
                    failure_diagnostic=_simple_diagnostic(
                        phase="git_rev_parse",
                        command_category="git",
                        command="git rev-parse HEAD",
                    ),
                    temp_root_path=temp_root_path,
                    contract=contract,
                    context=context,
                )

            prompt = _build_omp_prompt(contract, phase=context.phase)
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
            if omp.returncode == 0:
                testpilot_signal = _parse_testpilot_signal(
                    stdout=omp.stdout,
                    stderr=omp.stderr,
                    secret=token,
                )
                if context.phase == "testpilot" and testpilot_signal is not None:
                    details["testpilot_signal"] = testpilot_signal
                    status_name = str(testpilot_signal["status"])
                    reason = str(testpilot_signal.get("reason") or "acceptance gap reported")
                    return AdapterResult(
                        status=status_name,
                        commit_shas=[],
                        summary=f"TestPilot reported {status_name}: {reason}",
                        details=details,
                    )
            if omp.returncode != 0:
                return _failed_pi_result(
                    f"OMP subprocess failed with exit code {omp.returncode}.",
                    token,
                    details,
                    failure_diagnostic=_diagnostic_from_process(
                        phase="omp_execution",
                        command_category="omp",
                        command=Path(command_path).name or "omp",
                        process=omp,
                        secret=token,
                    ),
                    temp_root_path=temp_root_path,
                    contract=contract,
                    context=context,
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
                    failure_diagnostic=_diagnostic_from_process(
                        phase="git_status",
                        command_category="git",
                        command="git status --porcelain",
                        process=status,
                        secret=token,
                    ),
                    temp_root_path=temp_root_path,
                    contract=contract,
                    context=context,
                )

            dirty_tree = bool(status.stdout.strip())
            if dirty_tree:
                details["patch_left_behind"] = True
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
                        failure_diagnostic=_diagnostic_from_process(
                            phase="git_add",
                            command_category="git",
                            command="git add -A",
                            process=staged,
                            secret=token,
                        ),
                        temp_root_path=temp_root_path,
                        contract=contract,
                        context=context,
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
                        failure_diagnostic=_diagnostic_from_process(
                            phase="git_commit",
                            command_category="git",
                            command="git commit",
                            process=commit,
                            secret=token,
                        ),
                        temp_root_path=temp_root_path,
                        contract=contract,
                        context=context,
                    )

            head_sha = await _git_head_sha(checkout_dir, timeout_seconds, git_env)
            if head_sha is None:
                return _failed_pi_result(
                    "Git rev-parse failed for Assembly OMP backend.",
                    token,
                    details,
                    failure_diagnostic=_simple_diagnostic(
                        phase="git_rev_parse",
                        command_category="git",
                        command="git rev-parse HEAD",
                    ),
                    temp_root_path=temp_root_path,
                    contract=contract,
                    context=context,
                )
            if not _COMMIT_SHA_RE.fullmatch(head_sha):
                return _failed_pi_result(
                    "Assembly OMP backend produced an invalid commit SHA.",
                    token,
                    details,
                    failure_diagnostic=_simple_diagnostic(
                        phase="git_rev_parse",
                        command_category="git",
                        command="git rev-parse HEAD",
                        stderr_tail="invalid commit SHA",
                    ),
                    temp_root_path=temp_root_path,
                    contract=contract,
                    context=context,
                )
            if not dirty_tree and head_sha == base_sha:
                return AdapterResult(
                    status="no_changes",
                    commit_shas=[],
                    summary="OMP completed with no repository changes.",
                    details=details,
                )

            details["patch_left_behind"] = True

            verification = await _run_verification_commands(
                contract,
                checkout_dir,
                timeout_seconds,
                git_env,
                secret=token,
            )
            if verification is not None:
                return _failed_pi_result(
                    str(verification["summary"]),
                    token,
                    details,
                    failure_diagnostic=dict(verification["failure_diagnostic"]),
                    temp_root_path=temp_root_path,
                    contract=contract,
                    context=context,
                )

            if _ac_spotcheck_enabled():
                ac_spotcheck = await _run_acceptance_spotcheck(
                    contract,
                    checkout_dir,
                    f"origin/{contract.base_branch}",
                    timeout_seconds,
                    git_env,
                    secret=token,
                )
                if not ac_spotcheck.ok:
                    details["ac_spotcheck"] = ac_spotcheck.to_dict()
                    if _AC_SPOTCHECK_MATURITY == GateMaturity.L1:
                        # L1: advisory — record findings but do not block.
                        details["ac_spotcheck_maturity"] = "L1"
                    else:
                        return _failed_pi_result(
                            ac_spotcheck.summary(),
                            token,
                            details,
                            failure_diagnostic=_simple_diagnostic(
                                phase="acceptance_spotcheck",
                                command_category="acceptance_criteria",
                                command="exact-token spot-check",
                                stdout_tail=_spotcheck_excerpt(ac_spotcheck),
                                secret=token,
                            ),
                            temp_root_path=temp_root_path,
                            contract=contract,
                            context=context,
                            status="blocked",
                        )

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
                    failure_diagnostic=_diagnostic_from_publish_result(
                        publish_result,
                        secret=token,
                    ),
                    temp_root_path=temp_root_path,
                    contract=contract,
                    context=context,
                )

            return AdapterResult(
                status="executed",
                commit_shas=[head_sha],
                summary=_executed_summary(details),
                details=details,
            )
        except TimeoutError:
            return _failed_pi_result(
                "Assembly OMP backend timed out.",
                token,
                details,
                failure_diagnostic=_simple_diagnostic(
                    phase="subprocess",
                    command_category="subprocess",
                    timed_out=True,
                ),
                temp_root_path=temp_root_path,
                contract=contract,
                context=context,
            )
        except OSError:
            return _failed_pi_result(
                "Assembly OMP backend could not start a subprocess.",
                token,
                details,
                failure_diagnostic=_simple_diagnostic(
                    phase="subprocess_start",
                    command_category="subprocess",
                ),
                temp_root_path=temp_root_path,
                contract=contract,
                context=context,
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
        return _ProcessResult(
            returncode=124,
            stdout="",
            stderr=f"command timed out after {timeout_seconds}s",
            timed_out=True,
        )
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
        return _ProcessResult(
            returncode=124,
            stdout="",
            stderr=f"command timed out after {timeout_seconds}s",
            timed_out=True,
        )
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
    secret: str,
) -> tuple[str, dict[str, Any] | None]:
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
        fetch_err = fetch.stderr.strip().lower()
        missing_remote_ref = (
            "couldn't find remote ref" in fetch_err or "could not find remote ref" in fetch_err
        )
        if missing_remote_ref:
            return f"origin/{contract.base_branch}", None
        return (
            f"origin/{contract.base_branch}",
            _diagnostic_from_process(
                phase="branch_start_fetch",
                command_category="git",
                command="git fetch",
                process=fetch,
                secret=secret,
            ),
        )

    remote_branch_ref = f"refs/remotes/origin/{contract.branch_name}"
    verify = await _run_exec(
        ["git", "rev-parse", "--verify", "--quiet", remote_branch_ref],
        cwd=checkout_dir,
        timeout_seconds=timeout_seconds,
        env=git_env,
    )
    if verify.returncode == 0:
        return remote_branch_ref, None
    return f"origin/{contract.base_branch}", None


async def _run_verification_commands(
    contract: AssemblyJobContract,
    checkout_dir: Path,
    timeout_seconds: int,
    env: dict[str, str],
    *,
    secret: str,
) -> dict[str, Any] | None:
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
            return {
                "summary": f"Verification command failed: {command}",
                "failure_diagnostic": _diagnostic_from_process(
                    phase="verification",
                    command_category="verification",
                    command=command,
                    process=result,
                    secret=secret,
                ),
            }
    return None


async def _changed_text_since_base(
    checkout_dir: Path,
    base_ref: str,
    timeout_seconds: int,
    env: dict[str, str],
) -> str | None:
    diff = await _run_exec(
        [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=ACMRT",
            f"{base_ref}...HEAD",
        ],
        cwd=checkout_dir,
        timeout_seconds=timeout_seconds,
        env=env,
    )
    if diff.returncode != 0:
        return None

    checkout_root = checkout_dir.resolve()
    chunks: list[str] = []
    for raw_name in diff.stdout.splitlines():
        rel_name = raw_name.strip()
        if not rel_name:
            continue
        path = (checkout_dir / rel_name).resolve()
        try:
            path.relative_to(checkout_root)
        except ValueError:
            continue
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        chunks.append(f"\n--- {rel_name} ---\n{text}")
    return "\n".join(chunks) if chunks else None


async def _run_acceptance_spotcheck(
    contract: AssemblyJobContract,
    checkout_dir: Path,
    base_ref: str,
    timeout_seconds: int,
    env: dict[str, str],
    *,
    secret: str,
) -> AcceptanceSpotCheckResult:
    changed_text = await _changed_text_since_base(
        checkout_dir,
        base_ref,
        timeout_seconds,
        env,
    )
    if not changed_text:
        return AcceptanceSpotCheckResult()
    changed_text = _sanitize_for_matching(changed_text, secret)
    return check_acceptance_exact_tokens(
        issue_body=contract.issue_body,
        acceptance_criteria=contract.acceptance_criteria,
        acceptance_criteria_items=contract.acceptance_criteria_items,
        changed_text=changed_text,
    )


def _spotcheck_excerpt(result: AcceptanceSpotCheckResult) -> str:
    lines: list[str] = []
    for finding in result.findings[:5]:
        missing = ", ".join(finding.missing_tokens)
        lines.append(f"{finding.source}: missing {missing}")
    return "\n".join(lines)


def _executed_summary(details: dict[str, Any]) -> str:
    summary = "OMP completed, committed changes, and pushed the Assembly branch."
    if _has_l1_advisory_gate_findings(details):
        return f"{summary} L1 advisory gate findings were recorded and surfaced."
    return summary


def _has_l1_advisory_gate_findings(details: dict[str, Any]) -> bool:
    if details.get("ac_spotcheck_maturity") != "L1":
        return False
    spotcheck = details.get("ac_spotcheck")
    if not isinstance(spotcheck, dict):
        return False
    findings = spotcheck.get("findings")
    return isinstance(findings, list) and bool(findings)


def _ac_spotcheck_enabled() -> bool:
    raw = os.environ.get(ASSEMBLY_AC_SPOTCHECK_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _build_omp_prompt(contract: AssemblyJobContract, *, phase: str = "implementer") -> str:
    acceptance = "\n".join(f"- {item}" for item in contract.acceptance_criteria) or "- None"
    forbidden = "\n".join(f"- {item}" for item in contract.forbidden_operations) or "- None"
    verification = "\n".join(f"- {item}" for item in contract.verification_commands) or "- None"
    common = (
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
    )
    if phase == "testpilot":
        return (
            "You are Assembly TestPilot independently verifying the current PR branch.\n"
            f"{common}"
            "Inspect the current branch against the acceptance criteria and existing diff. "
            "Do not re-run the implementation phase. Add or tighten tests, fixtures, or "
            "small verification-only fixes when they are clearly needed to prove the PR. "
            "If the acceptance criteria are not met and a safe test/verification fix is not "
            "practical, leave the checkout unchanged and print exactly these machine-readable "
            "lines before your final explanation: `ASSEMBLY_TESTPILOT_STATUS=blocked` and "
            "`ASSEMBLY_TESTPILOT_REASON=<short reason>`. Run the verification commands when "
            "practical, commit any TestPilot changes on the current branch, and do not push. "
            "The Assembly adapter will push after validation."
        )
    return (
        "You are Assembly implementing a GitHub issue in the current checkout.\n"
        f"{common}"
        "Work only in this checkout on the current branch. Make the smallest production "
        "changes needed, run the verification commands when practical, commit changes "
        "on the current branch, and do not push. The Assembly adapter will push after "
        "validation."
    )


_GITHUB_TOKEN_RE = re.compile(r"gh[opsru]_[A-Za-z0-9_]+")
_TOKEN_QUERY_RE = re.compile(r"(?i)(token=)[^\s&]+")
_BEARER_RE = re.compile(r"(?i)(Bearer\s+)\S+")
_URL_USERINFO_RE = re.compile(r"(?i)(https?://)[^/\s:@]+:[^@\s/]+@")
_SECRET_ASSIGNMENT_FOR_MATCHING_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API[_-]?KEY|PRIVATE[_-]?KEY|CREDENTIAL)"
    r"[A-Z0-9_]*)\s*=\s*[^\s&]+"
)
_API_KEY_SHAPED_RE = re.compile(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{6,}(?![A-Za-z0-9_-])")
_TESTPILOT_STATUS_RE = re.compile(r"(?m)^ASSEMBLY_TESTPILOT_STATUS=(blocked|failed)\s*$")
_TESTPILOT_REASON_RE = re.compile(r"(?m)^ASSEMBLY_TESTPILOT_REASON=(.+)$")


def _parse_testpilot_signal(*, stdout: str, stderr: str, secret: str) -> dict[str, str] | None:
    combined = f"{stdout or ''}\n{stderr or ''}"
    status_match = _TESTPILOT_STATUS_RE.search(combined)
    if status_match is None:
        return None
    reason_match = _TESTPILOT_REASON_RE.search(combined)
    reason = reason_match.group(1).strip() if reason_match else "acceptance gap reported"
    return {
        "status": status_match.group(1),
        "reason": _sanitize_for_result(reason, secret, limit=500),
    }


def _sanitize_for_result(value: str, secret: str, *, limit: int = 2000) -> str:
    sanitized = value.replace(secret, "[redacted]") if secret else value
    sanitized = _GITHUB_TOKEN_RE.sub("[redacted]", sanitized)
    return sanitize_public_text(sanitized, limit=limit)


def _sanitize_for_matching(value: str, secret: str) -> str:
    sanitized = value.replace(secret, "[redacted]") if secret else value
    sanitized = " ".join(str(sanitized or "").split())
    sanitized = _URL_USERINFO_RE.sub(r"\1[redacted]@", sanitized)
    sanitized = _SECRET_ASSIGNMENT_FOR_MATCHING_RE.sub(r"\1=[redacted]", sanitized)
    sanitized = _TOKEN_QUERY_RE.sub(r"\1[redacted]", sanitized)
    sanitized = _GITHUB_TOKEN_RE.sub("[redacted]", sanitized)
    sanitized = _API_KEY_SHAPED_RE.sub("[redacted]", sanitized)
    return _BEARER_RE.sub(r"\1[redacted]", sanitized)


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


def _bounded_tail(value: str, secret: str, *, limit: int = _FAILURE_TAIL_LIMIT) -> str:
    tail = str(value or "")
    if len(tail) > limit:
        tail = tail[-limit:]
    return _sanitize_for_result(tail, secret, limit=limit)


def _simple_diagnostic(
    *,
    phase: str,
    command_category: str,
    command: str | None = None,
    exit_code: int | None = None,
    timed_out: bool = False,
    stdout_tail: str = "",
    stderr_tail: str = "",
    secret: str = "",
) -> dict[str, Any]:
    return {
        "phase": _sanitize_for_result(phase, secret, limit=120),
        "command_category": _sanitize_for_result(command_category, secret, limit=120),
        "command": _sanitize_for_result(command or "", secret, limit=240),
        "exit_code": exit_code,
        "timed_out": bool(timed_out),
        "stdout_tail": _bounded_tail(stdout_tail, secret),
        "stderr_tail": _bounded_tail(stderr_tail, secret),
    }


def _diagnostic_from_process(
    *,
    phase: str,
    command_category: str,
    command: str,
    process: _ProcessResult,
    secret: str,
) -> dict[str, Any]:
    return _simple_diagnostic(
        phase=phase,
        command_category=command_category,
        command=command,
        exit_code=process.returncode,
        timed_out=process.timed_out,
        stdout_tail=process.stdout,
        stderr_tail=process.stderr,
        secret=secret,
    )


def _diagnostic_from_publish_result(result: Any, *, secret: str) -> dict[str, Any]:
    return _simple_diagnostic(
        phase=str(getattr(result, "phase", None) or "git_push"),
        command_category="git",
        command=str(getattr(result, "command", None) or "git push"),
        exit_code=result.returncode,
        timed_out=bool(result.timed_out),
        stdout_tail=str(result.stdout or ""),
        stderr_tail=str(result.stderr or result.message or ""),
        secret=secret,
    )


def _failure_run_id(contract: AssemblyJobContract, context: AdapterExecutionContext) -> str:
    if context.audit_id:
        return context.audit_id
    seed = f"{contract.delivery_id}|{context.repository}|{contract.issue_number}".encode()
    return f"asmb-{hashlib.sha256(seed).hexdigest()[:16]}"


def _failure_bundle_path(
    contract: AssemblyJobContract,
    context: AdapterExecutionContext,
) -> Path:
    owner, _, repo = context.repository.partition("/")
    if not owner or not repo:
        owner, repo = "unknown", context.repository or "unknown"
    return (
        context.workdir
        / "failures"
        / owner
        / repo
        / str(contract.issue_number)
        / _failure_run_id(contract, context)
    )


def _retain_failure_bundle(
    *,
    temp_root_path: Path | None,
    contract: AssemblyJobContract | None,
    context: AdapterExecutionContext | None,
    failure_diagnostic: dict[str, Any] | None,
    details: dict[str, Any],
    secret: str,
) -> Path | None:
    if temp_root_path is None or contract is None or context is None:
        return None
    if not temp_root_path.exists():
        return None

    bundle_path = _failure_bundle_path(contract, context)
    try:
        if bundle_path.exists():
            shutil.rmtree(bundle_path, ignore_errors=True)
        bundle_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(temp_root_path), str(bundle_path))
    except (OSError, shutil.Error):
        return None

    details["failure_debug_bundle_path"] = str(bundle_path)
    details.setdefault("patch_left_behind", False)
    repo_path = bundle_path / "repo"
    if repo_path.exists():
        details["checkout_dir"] = str(repo_path)

    try:
        metadata = {
            "repository": context.repository,
            "issue_number": contract.issue_number,
            "branch_name": contract.branch_name,
            "delivery_id": contract.delivery_id,
            "audit_id": context.audit_id,
            "backend_name": ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK,
            "retained_at": datetime.now(UTC).isoformat(),
            "failure_diagnostic": failure_diagnostic or {},
            "details": details,
        }
        metadata_path = bundle_path / "assembly-failure.json"
        metadata_path.write_text(
            json.dumps(_sanitize_details_for_result(metadata, secret), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        metadata_path.chmod(0o600)
    except (OSError, TypeError) as exc:
        details["failure_bundle_metadata_error"] = _sanitize_for_result(str(exc), secret)
    return bundle_path


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
    *,
    failure_diagnostic: dict[str, Any] | None = None,
    temp_root_path: Path | None = None,
    contract: AssemblyJobContract | None = None,
    context: AdapterExecutionContext | None = None,
    status: str = "failed",
) -> AdapterResult:
    safe_details = dict(details or {})
    if failure_diagnostic:
        safe_details["failure_diagnostic"] = failure_diagnostic
    bundle_path = _retain_failure_bundle(
        temp_root_path=temp_root_path,
        contract=contract,
        context=context,
        failure_diagnostic=failure_diagnostic,
        details=safe_details,
        secret=secret,
    )
    if bundle_path is not None:
        safe_details["failure_debug_bundle_path"] = str(bundle_path)
        repo_path = bundle_path / "repo"
        if repo_path.exists():
            safe_details["checkout_dir"] = str(repo_path)
    return AdapterResult(
        status=status,
        commit_shas=[],
        summary=_sanitize_for_result(summary, secret, limit=240),
        details=_sanitize_details_for_result(safe_details, secret),
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

        # Phase-specific output env var takes precedence over the global one.
        phase = (context.phase if context else None) or "implementer"
        phase_key = f"{ASSEMBLY_FAKE_SUBPROCESS_OUTPUT_ENV}_{phase.upper()}"
        raw = os.environ.get(phase_key) or os.environ.get(ASSEMBLY_FAKE_SUBPROCESS_OUTPUT_ENV)

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
        if status == "blocked":
            return AdapterResult(
                status="blocked",
                commit_shas=[],
                summary=summary or "Fake subprocess reported blocked.",
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


def select_execution_adapter(
    backend: str | None = None,
    cfg: Any | None = None,
) -> ExecutionAdapter:
    """Return an adapter instance for the requested backend.

    When ``backend`` is None, reads ``ASSEMBLY_EXECUTION_BACKEND`` from the
    environment, then ``cfg.assembly.execution_backend`` as the TOML fallback.
    Unknown / empty values fall back to ``dry-run``.
    """
    if backend is not None:
        raw = backend
    elif ASSEMBLY_EXECUTION_BACKEND_ENV in os.environ:
        raw = os.environ.get(ASSEMBLY_EXECUTION_BACKEND_ENV, "")
    else:
        assembly = getattr(cfg, "assembly", None)
        raw = getattr(assembly, "execution_backend", None) or ""
    chosen = raw.strip().lower()
    if chosen == ASSEMBLY_BACKEND_FAKE_SUBPROCESS:
        return FakeSubprocessAdapter()
    if chosen == ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK:
        return PiOhMyPiDeepSeekAdapter()
    return DryRunAdapter()
