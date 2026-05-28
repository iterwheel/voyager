"""Assembly bot — phase models for implementer / testpilot orchestration.

Per #96: Assembly can run in single-phase (implementer only, backward-compatible
default) or two-phase (implementer + independent testpilot) mode.

Each phase produces a ``PhaseResult`` that the writeback dispatcher combines
into the final outcome.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .constants import (
    ASSEMBLY_BACKEND_DRY_RUN,
    ASSEMBLY_IMPLEMENTER_BACKEND_ENV,
    ASSEMBLY_PHASE_MODE_DEFAULT,
    ASSEMBLY_PHASE_MODE_ENV,
    ASSEMBLY_PHASE_MODE_SINGLE,
    ASSEMBLY_PHASE_MODE_TWO_PHASE,
    ASSEMBLY_TESTPILOT_BACKEND_ENV,
)


class PhaseMode(Enum):
    """Assembly execution phase mode."""

    SINGLE = ASSEMBLY_PHASE_MODE_SINGLE
    TWO_PHASE = ASSEMBLY_PHASE_MODE_TWO_PHASE

    @classmethod
    def from_env(cls) -> PhaseMode:
        """Read phase mode from the environment, defaulting to SINGLE."""
        raw = os.environ.get(ASSEMBLY_PHASE_MODE_ENV, ASSEMBLY_PHASE_MODE_DEFAULT)
        raw_lower = raw.strip().lower()
        if raw_lower == ASSEMBLY_PHASE_MODE_TWO_PHASE:
            return cls.TWO_PHASE
        return cls.SINGLE


class PhaseName(Enum):
    """Names for the two execution phases."""

    IMPLEMENTER = "implementer"
    TESTPILOT = "testpilot"


def select_phase_backend(global_backend: str | None, phase: PhaseName) -> str:
    """Return the backend for a specific phase.

    Per-phase env vars (ASSEMBLY_IMPLEMENTER_BACKEND, ASSEMBLY_TESTPILOT_BACKEND)
    override the global ASSEMBLY_EXECUTION_BACKEND. Falls back to dry-run.
    """
    env_var = (
        ASSEMBLY_IMPLEMENTER_BACKEND_ENV
        if phase == PhaseName.IMPLEMENTER
        else ASSEMBLY_TESTPILOT_BACKEND_ENV
    )
    raw = os.environ.get(env_var)
    if raw:
        chosen = raw.strip().lower()
        if chosen:
            return chosen
    if global_backend:
        chosen = global_backend.strip().lower()
        if chosen:
            return chosen
    return ASSEMBLY_BACKEND_DRY_RUN


@dataclass(frozen=True)
class PhaseResult:
    """Outcome of a single execution phase."""

    phase: PhaseName
    adapter_result: dict[str, Any] | None = None
    audit_id: str | None = None
    session: dict[str, Any] = field(default_factory=dict)
    branch: dict[str, Any] | None = None
    pull_request: dict[str, Any] | None = None
    codex_review_comment_id: int | None = None

    @property
    def status(self) -> str:
        """Derive the phase status from the adapter result.

        Returns one of: ``"pending"``, ``"executed"``, ``"no_changes"``,
        ``"failed"``, ``"dry_run"``, ``"blocked"``.
        """
        if self.adapter_result is None:
            return "pending"
        raw = (self.adapter_result.get("status") or "unknown").lower()
        if raw == "blocked":
            return "blocked"
        if raw in ("executed", "no_changes", "dry_run", "failed"):
            return raw
        return "unknown"

    @property
    def summary(self) -> str:
        """Return a human-readable one-line summary of this phase."""
        if self.adapter_result is None:
            return "Not started"
        summary_raw = self.adapter_result.get("summary") or ""
        status = self.status
        if status == "executed":
            return summary_raw or "Committed changes"
        if status == "no_changes":
            return summary_raw or "No changes needed"
        if status == "failed":
            return summary_raw or "Execution failed"
        if status == "dry_run":
            return summary_raw or "Dry-run recorded"
        if status == "blocked":
            return summary_raw or "Blocked — gaps reported"
        return summary_raw or status.replace("_", " ").title()

    @property
    def is_success(self) -> bool:
        """True when the phase completed without blocking the overall run."""
        if self.adapter_result is None:
            return False
        return self.status in ("executed", "no_changes", "dry_run")

    @property
    def is_blocking(self) -> bool:
        """True when this phase prevents the run from claiming success."""
        if self.adapter_result is None:
            return True  # Not started yet = blocking
        return self.status in ("failed", "blocked", "unknown")


def combine_phase_results(
    implementer: PhaseResult,
    testpilot: PhaseResult | None,
) -> str:
    """Compute the overall status from two phase results.

    Priority:
    1. any blocking failure → "failed"
    2. testpilot blocked → "blocked"
    3. both passed → "applied"
    4. only implementer ran → implementer status
    """
    if implementer.is_blocking:
        if implementer.adapter_result is None:
            return "pending"
        return "failed" if implementer.status == "failed" else implementer.status

    if testpilot is not None:
        if testpilot.is_blocking:
            if testpilot.status == "blocked":
                return "blocked"
            return "failed"
        if testpilot.adapter_result is not None:
            # Both phases completed
            if implementer.is_success and testpilot.is_success:
                return "applied"
            return testpilot.status

    # Single-phase: map success statuses to "applied"
    if implementer.is_success:
        return "applied"
    return implementer.status
