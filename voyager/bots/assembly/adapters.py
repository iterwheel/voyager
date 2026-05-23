"""Assembly bot — pluggable execution adapters.

Per VOY-1817 Surface 7 and D1.  The adapter seam isolates the
GitHub-mutation side of Assembly from the subprocess-execution side so
the bot can ship today with a deterministic ``DryRunAdapter`` while the
real ``pi -> oh-my-pi -> DeepSeek V4 Pro`` backend lands in a follow-up.

Selection happens via the ``ASSEMBLY_EXECUTION_BACKEND`` env var; the
default is ``dry-run``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from .constants import (
    ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK,
    ASSEMBLY_EXECUTION_BACKEND_ENV,
)
from .job_contract import AssemblyJobContract


@dataclass(frozen=True)
class AdapterResult:
    """The structured outcome an adapter must return."""

    status: str  # "dry_run" | "executed" | "no_changes" | "failed"
    commit_shas: list[str] = field(default_factory=list)
    summary: str = ""
    # Optional extra metadata the adapter wants to surface (e.g., the
    # planned diff). Kept opaque to the writeback dispatcher.
    details: dict[str, Any] = field(default_factory=dict)


class ExecutionAdapter(Protocol):
    """Protocol every adapter must satisfy."""

    name: str

    async def execute(self, contract: AssemblyJobContract) -> AdapterResult: ...


@dataclass
class DryRunAdapter:
    """Records the planned contract without spawning a subprocess.

    Returns ``status="dry_run"`` and an empty ``commit_shas`` list so the
    writeback dispatcher knows there is no branch/PR work to do.
    """

    name: str = "dry-run"
    last_contract: AssemblyJobContract | None = None

    async def execute(self, contract: AssemblyJobContract) -> AdapterResult:
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

    name: str = "pi-oh-my-pi-deepseek"

    async def execute(self, contract: AssemblyJobContract) -> AdapterResult:
        raise NotImplementedError(
            "execution backend deferred; see follow-up issue for "
            "pi -> oh-my-pi -> DeepSeek V4 Pro wiring"
        )


def select_execution_adapter(backend: str | None = None) -> ExecutionAdapter:
    """Return an adapter instance for the requested backend.

    When ``backend`` is None, reads ``ASSEMBLY_EXECUTION_BACKEND`` from the
    environment.  Unknown / empty values fall back to ``dry-run``.
    """
    chosen = (backend or os.environ.get(ASSEMBLY_EXECUTION_BACKEND_ENV, "")).strip().lower()
    if chosen == ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK:
        return PiOhMyPiDeepSeekAdapter()
    return DryRunAdapter()
