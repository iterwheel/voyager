"""Bounded review-fix loop runner with kill-switch and escalation auditing."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from .audit_log import ReviewFixAuditLog, ReviewFixAuditRecord
from .enablement import Autonomy, EnablementConfig, SafetyEnvelope

_RUNNER_COMMIT = "loop-runner"
_RUNNER_CATEGORY = "loop-runner"


class ReviewFixLoopRunnerError(ValueError):
    """Raised when the review-fix loop runner is configured unsafely."""


class ReviewFixLoopOutcomeStatus(StrEnum):
    """Terminal outcome for one bounded review-fix loop run."""

    CONVERGED = "converged"
    ESCALATED = "escalated"
    KILL_SWITCH = "kill_switch"


@dataclass(frozen=True, kw_only=True)
class ReviewFixFinding:
    """One review finding visible to the governed loop."""

    finding_id: str
    category: str


@dataclass(frozen=True, kw_only=True)
class ReviewFixClassification:
    """Classifier decision for one finding."""

    fixable: bool
    reason: str = ""


@dataclass(frozen=True, kw_only=True)
class ReviewFixLoopFixResult:
    """Result returned by the injected fix seam after verification/rollback."""

    commit: str
    verdict: str
    tests: tuple[str, ...] = ()
    audit_recorded: bool = False


@dataclass(frozen=True, kw_only=True)
class ReviewFixLoopWork:
    """One fix attempt handed to the injected fix seam."""

    finding: ReviewFixFinding
    classification: ReviewFixClassification


@dataclass(frozen=True, kw_only=True)
class ReviewFixLoopStatus:
    """Current loop position passed to injected seams."""

    round_number: int
    clean_rounds: int
    max_rounds: int
    max_fixes_per_round: int


@dataclass(frozen=True, kw_only=True)
class ReviewFixLoopOutcome:
    """Summary returned after the loop reaches a terminal state."""

    status: ReviewFixLoopOutcomeStatus
    rounds_run: int
    clean_rounds: int
    escalation: str | None = None
    kill_switch_path: Path | None = None


GatherFindings = Callable[[ReviewFixLoopStatus], Sequence[ReviewFixFinding]]
ClassifyFinding = Callable[[ReviewFixFinding, ReviewFixLoopStatus], ReviewFixClassification]
FixFinding = Callable[[ReviewFixLoopWork, ReviewFixLoopStatus], ReviewFixLoopFixResult]
NowFactory = Callable[[], datetime]


@dataclass(frozen=True, kw_only=True)
class ReviewFixLoopSeams:
    """Injected seams that keep the runner offline-testable."""

    gather: GatherFindings
    classify: ClassifyFinding
    fix: FixFinding


class ReviewFixLoopRunner:
    """Run a bounded review-fix loop under a recorded safety envelope."""

    def __init__(
        self,
        *,
        enablement: EnablementConfig,
        audit_log: ReviewFixAuditLog,
        seams: ReviewFixLoopSeams,
        root_path: str | Path = ".",
        now: NowFactory | None = None,
        clean_rounds_required: int = 1,
    ) -> None:
        self.enablement = enablement
        self.audit_log = audit_log
        self.seams = seams
        self.root_path = Path(root_path)
        self.now = now or _utcnow
        self.clean_rounds_required = _positive_int(
            clean_rounds_required,
            "clean_rounds_required",
        )

    def run(self) -> ReviewFixLoopOutcome:
        envelope = _require_envelope(self.enablement)
        if self.clean_rounds_required > envelope.max_rounds:
            raise ReviewFixLoopRunnerError(
                "clean_rounds_required must be <= max_rounds, got "
                f"{self.clean_rounds_required} > {envelope.max_rounds}"
            )
        kill_switch_path = _resolve_kill_switch(self.root_path, envelope.kill_switch_path)
        clean_rounds = 0
        rounds_run = 0

        for round_number in range(1, envelope.max_rounds + 1):
            status = ReviewFixLoopStatus(
                round_number=round_number,
                clean_rounds=clean_rounds,
                max_rounds=envelope.max_rounds,
                max_fixes_per_round=envelope.max_fixes_per_round,
            )

            if kill_switch_path.exists():
                _append_audit(
                    self.audit_log,
                    round_number=round_number,
                    ts=self.now(),
                    finding_id="kill-switch",
                    verdict=ReviewFixLoopOutcomeStatus.KILL_SWITCH.value,
                    tests=(str(kill_switch_path),),
                )
                return ReviewFixLoopOutcome(
                    status=ReviewFixLoopOutcomeStatus.KILL_SWITCH,
                    rounds_run=rounds_run,
                    clean_rounds=clean_rounds,
                    kill_switch_path=kill_switch_path,
                )

            rounds_run = round_number
            findings = tuple(self.seams.gather(status))
            if kill_switch_path.exists():
                _append_audit(
                    self.audit_log,
                    round_number=round_number,
                    ts=self.now(),
                    finding_id="kill-switch",
                    verdict=ReviewFixLoopOutcomeStatus.KILL_SWITCH.value,
                    tests=(str(kill_switch_path),),
                )
                return ReviewFixLoopOutcome(
                    status=ReviewFixLoopOutcomeStatus.KILL_SWITCH,
                    rounds_run=rounds_run,
                    clean_rounds=clean_rounds,
                    kill_switch_path=kill_switch_path,
                )
            if not findings:
                clean_rounds += 1
                _append_round_audit(
                    self.audit_log,
                    round_number=round_number,
                    ts=self.now(),
                    verdict="round_clean",
                    findings=0,
                    fixes=0,
                )
                if clean_rounds >= self.clean_rounds_required:
                    _append_audit(
                        self.audit_log,
                        round_number=round_number,
                        ts=self.now(),
                        finding_id="loop",
                        verdict=ReviewFixLoopOutcomeStatus.CONVERGED.value,
                        tests=(f"clean_rounds={clean_rounds}",),
                    )
                    return ReviewFixLoopOutcome(
                        status=ReviewFixLoopOutcomeStatus.CONVERGED,
                        rounds_run=rounds_run,
                        clean_rounds=clean_rounds,
                    )
                continue

            clean_rounds = 0
            fixes, killed, fix_error = self._process_findings(
                findings, status, envelope, kill_switch_path
            )
            _append_round_audit(
                self.audit_log,
                round_number=round_number,
                ts=self.now(),
                verdict="round_fixed" if fixes else "round_open",
                findings=len(findings),
                fixes=fixes,
            )
            if killed:
                _append_audit(
                    self.audit_log,
                    round_number=round_number,
                    ts=self.now(),
                    finding_id="kill-switch",
                    verdict=ReviewFixLoopOutcomeStatus.KILL_SWITCH.value,
                    tests=(str(kill_switch_path),),
                )
                return ReviewFixLoopOutcome(
                    status=ReviewFixLoopOutcomeStatus.KILL_SWITCH,
                    rounds_run=rounds_run,
                    clean_rounds=clean_rounds,
                    kill_switch_path=kill_switch_path,
                )
            if fix_error is not None:
                _append_audit(
                    self.audit_log,
                    round_number=round_number,
                    ts=self.now(),
                    finding_id="loop",
                    verdict=ReviewFixLoopOutcomeStatus.ESCALATED.value,
                    tests=(envelope.escalation, fix_error),
                )
                return ReviewFixLoopOutcome(
                    status=ReviewFixLoopOutcomeStatus.ESCALATED,
                    rounds_run=rounds_run,
                    clean_rounds=clean_rounds,
                    escalation=envelope.escalation,
                )

        _append_audit(
            self.audit_log,
            round_number=envelope.max_rounds,
            ts=self.now(),
            finding_id="loop",
            verdict=ReviewFixLoopOutcomeStatus.ESCALATED.value,
            tests=(envelope.escalation, f"max_rounds={envelope.max_rounds}"),
        )
        return ReviewFixLoopOutcome(
            status=ReviewFixLoopOutcomeStatus.ESCALATED,
            rounds_run=rounds_run,
            clean_rounds=clean_rounds,
            escalation=envelope.escalation,
        )

    def _process_findings(
        self,
        findings: Sequence[ReviewFixFinding],
        status: ReviewFixLoopStatus,
        envelope: SafetyEnvelope,
        kill_switch_path: Path,
    ) -> tuple[int, bool, str | None]:
        fixes = 0
        for finding in findings:
            if kill_switch_path.exists():
                return fixes, True, None
            finding = _validated_finding(finding)
            classification = self.seams.classify(finding, status)
            if not isinstance(classification.fixable, bool):
                raise ReviewFixLoopRunnerError(
                    "classification.fixable must be a bool, "
                    f"got {type(classification.fixable).__name__}"
                )
            if not classification.fixable:
                _append_audit(
                    self.audit_log,
                    round_number=status.round_number,
                    ts=self.now(),
                    finding_id=finding.finding_id,
                    category=finding.category,
                    verdict="not_fixable",
                    tests=_reason_tests(classification.reason),
                )
                if kill_switch_path.exists():
                    return fixes, True, None
                continue
            if fixes >= envelope.max_fixes_per_round:
                _append_audit(
                    self.audit_log,
                    round_number=status.round_number,
                    ts=self.now(),
                    finding_id=finding.finding_id,
                    category=finding.category,
                    verdict="fix_cap_deferred",
                    tests=(f"max_fixes_per_round={envelope.max_fixes_per_round}",),
                )
                if kill_switch_path.exists():
                    return fixes, True, None
                continue
            if kill_switch_path.exists():
                return fixes, True, None

            try:
                result = self.seams.fix(
                    ReviewFixLoopWork(finding=finding, classification=classification),
                    status,
                )
            except Exception as exc:
                return fixes + 1, False, _fix_error_test(exc)
            fixes += 1
            try:
                if result.audit_recorded is not True:
                    _append_audit(
                        self.audit_log,
                        round_number=status.round_number,
                        ts=self.now(),
                        commit=result.commit,
                        finding_id=finding.finding_id,
                        category=finding.category,
                        verdict=result.verdict,
                        tests=_result_tests(
                            result.tests, fallback_verify_command=envelope.verify_command
                        ),
                    )
            except Exception as exc:
                return fixes, False, _fix_error_test(exc)
            if kill_switch_path.exists():
                return fixes, True, None
        return fixes, False, None


def _validated_finding(finding: ReviewFixFinding) -> ReviewFixFinding:
    if not isinstance(finding, ReviewFixFinding):
        raise ReviewFixLoopRunnerError(
            f"finding must be a ReviewFixFinding, got {type(finding).__name__}"
        )
    return ReviewFixFinding(
        finding_id=_non_empty(finding.finding_id, "finding_id"),
        category=_non_empty(finding.category, "category"),
    )


def _fix_error_test(exc: Exception) -> str:
    detail = str(exc).strip()
    if detail:
        return f"fix_error={type(exc).__name__}: {detail}"
    return f"fix_error={type(exc).__name__}"


def _append_round_audit(
    audit_log: ReviewFixAuditLog,
    *,
    round_number: int,
    ts: datetime,
    verdict: str,
    findings: int,
    fixes: int,
) -> None:
    _append_audit(
        audit_log,
        round_number=round_number,
        ts=ts,
        finding_id=f"round:{round_number}",
        verdict=verdict,
        tests=(f"findings={findings}", f"fixes={fixes}"),
    )


def _append_audit(
    audit_log: ReviewFixAuditLog,
    *,
    round_number: int,
    ts: datetime,
    finding_id: str,
    verdict: str,
    tests: tuple[str, ...],
    commit: str = _RUNNER_COMMIT,
    category: str = _RUNNER_CATEGORY,
) -> None:
    audit_log.append(
        ReviewFixAuditRecord(
            round=_positive_int(round_number, "round_number"),
            ts=ts,
            commit=_non_empty(commit, "commit"),
            finding_id=_non_empty(finding_id, "finding_id"),
            category=_non_empty(category, "category"),
            verdict=_non_empty(verdict, "verdict"),
            tests=_tests_tuple(tests),
        )
    )


def _require_envelope(enablement: EnablementConfig) -> SafetyEnvelope:
    if enablement.autonomy is not Autonomy.L3:
        raise ReviewFixLoopRunnerError(
            f"review-fix loop runner requires autonomy {Autonomy.L3.value}, "
            f"got {enablement.autonomy.value}"
        )
    if enablement.envelope is None:
        raise ReviewFixLoopRunnerError("review-fix loop runner requires a safety envelope")
    envelope = enablement.envelope
    if not isinstance(envelope.kill_switch_path, Path):
        raise ReviewFixLoopRunnerError(
            "kill_switch_path must be a pathlib.Path, "
            f"got {type(envelope.kill_switch_path).__name__}"
        )
    return SafetyEnvelope(
        max_rounds=_positive_int(envelope.max_rounds, "max_rounds"),
        max_fixes_per_round=_positive_int(
            envelope.max_fixes_per_round,
            "max_fixes_per_round",
        ),
        kill_switch_path=envelope.kill_switch_path,
        escalation=_non_empty(envelope.escalation, "escalation"),
        verify_command=_non_empty(envelope.verify_command, "verify_command"),
    )


def _resolve_kill_switch(root: Path, configured: Path) -> Path:
    if configured.is_absolute():
        return configured
    return root / configured


def _positive_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReviewFixLoopRunnerError(f"{name} must be an integer, got {type(value).__name__}")
    if value < 1:
        raise ReviewFixLoopRunnerError(f"{name} must be >= 1, got {value!r}")
    return value


def _non_empty(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ReviewFixLoopRunnerError(f"{name} must be a non-empty string")
    return normalized


def _tests_tuple(value: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        raise ReviewFixLoopRunnerError("tests must contain at least one entry")
    return tuple(_non_empty(item, "tests item") for item in value)


def _reason_tests(reason: str) -> tuple[str, ...]:
    normalized = reason.strip()
    if not normalized:
        normalized = "not_fixable"
    return (f"reason={normalized}",)


def _result_tests(
    tests: tuple[str, ...],
    *,
    fallback_verify_command: str,
) -> tuple[str, ...]:
    normalized = tuple(item.strip() for item in tests if item.strip())
    if normalized:
        return normalized
    return ("verify_command=" + fallback_verify_command,)


def _utcnow() -> datetime:
    return datetime.now(UTC)
