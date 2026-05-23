"""Assembly bot — pluggable execution adapters.

Per VOY-1817 Surface 7 and D1.  The adapter seam isolates the
GitHub-mutation side of Assembly from the subprocess-execution side so
the bot can ship today with a deterministic ``DryRunAdapter`` while the
real ``pi -> oh-my-pi -> DeepSeek V4 Pro`` backend lands in a follow-up.

Selection happens via the ``ASSEMBLY_EXECUTION_BACKEND`` env var; the
default is ``dry-run``.
"""

from __future__ import annotations

import json
import os
import re
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

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "workdir": str(self.workdir),
            "timeout_seconds": self.timeout_seconds,
            "command_path": self.command_path,
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


@dataclass
class PiOhMyPiDeepSeekAdapter:
    """Placeholder for the real ``pi -> oh-my-pi -> DeepSeek V4 Pro`` backend.

    Wiring the subprocess pipeline is deferred per VOY-1817 §Out of Scope
    and §Decisions D1. Calling ``execute`` raises ``NotImplementedError``
    so an operator who flips ``ASSEMBLY_EXECUTION_BACKEND=pi-oh-my-pi-deepseek``
    without landing the follow-up PR gets an immediate, surfaced failure.
    """

    name: str = ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK
    requires_installation_token: bool = False

    async def execute(
        self,
        contract: AssemblyJobContract,
        context: AdapterExecutionContext | None = None,
    ) -> AdapterResult:
        raise NotImplementedError(
            "execution backend deferred; see follow-up issue for "
            "pi -> oh-my-pi -> DeepSeek V4 Pro wiring"
        )


@dataclass
class FakeSubprocessAdapter:
    """Deterministic subprocess-shaped backend for dispatcher verification.

    This adapter never spawns ``pi`` or any other command. It reads a JSON
    fixture from ``ASSEMBLY_FAKE_SUBPROCESS_OUTPUT`` and returns that shaped
    result after applying the same commit-SHA safety boundary the real
    subprocess backend must satisfy.
    """

    name: str = ASSEMBLY_BACKEND_FAKE_SUBPROCESS
    requires_installation_token: bool = False

    async def execute(
        self,
        contract: AssemblyJobContract,
        context: AdapterExecutionContext | None = None,
    ) -> AdapterResult:
        _ = contract, context
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
            )
        if status == "failed":
            return AdapterResult(
                status="failed",
                commit_shas=[],
                summary=summary or "Fake subprocess reported failure.",
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
        )


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
