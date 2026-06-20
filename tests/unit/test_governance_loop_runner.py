from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from voyager.governance.audit_log import ReviewFixAuditLog
from voyager.governance.enablement import Autonomy, EnablementConfig, SafetyEnvelope
from voyager.governance.loop_runner import (
    ReviewFixClassification,
    ReviewFixFinding,
    ReviewFixLoopFixResult,
    ReviewFixLoopOutcomeStatus,
    ReviewFixLoopRunner,
    ReviewFixLoopRunnerError,
    ReviewFixLoopSeams,
    ReviewFixLoopStatus,
    ReviewFixLoopWork,
)

_NOW = datetime(2026, 6, 20, 6, 15, tzinfo=UTC)


def test_runner_stops_on_convergence_and_audits_each_round(tmp_path) -> None:
    audit_path = tmp_path / "review-fix.jsonl"
    calls: list[tuple[str, int, str | None]] = []

    def gather(status: ReviewFixLoopStatus) -> list[ReviewFixFinding]:
        calls.append(("gather", status.round_number, None))
        if status.round_number == 1:
            return [ReviewFixFinding(finding_id="codex:finding-1", category="codex-review")]
        return []

    def classify(
        finding: ReviewFixFinding,
        status: ReviewFixLoopStatus,
    ) -> ReviewFixClassification:
        calls.append(("classify", status.round_number, finding.finding_id))
        return ReviewFixClassification(fixable=True)

    def fix(
        work: ReviewFixLoopWork,
        status: ReviewFixLoopStatus,
    ) -> ReviewFixLoopFixResult:
        calls.append(("fix", status.round_number, work.finding.finding_id))
        return ReviewFixLoopFixResult(
            commit="abc123",
            verdict="kept",
            tests=("pytest tests/unit/test_governance_loop_runner.py",),
        )

    outcome = ReviewFixLoopRunner(
        enablement=_enablement(tmp_path, max_rounds=4),
        audit_log=ReviewFixAuditLog(audit_path),
        seams=ReviewFixLoopSeams(gather=gather, classify=classify, fix=fix),
        root_path=tmp_path,
        now=lambda: _NOW,
    ).run()

    assert outcome.status is ReviewFixLoopOutcomeStatus.CONVERGED
    assert outcome.rounds_run == 2
    assert outcome.escalation is None
    assert calls == [
        ("gather", 1, None),
        ("classify", 1, "codex:finding-1"),
        ("fix", 1, "codex:finding-1"),
        ("gather", 2, None),
    ]

    records = ReviewFixAuditLog(audit_path).read_all()
    assert [(record.round, record.finding_id, record.verdict) for record in records] == [
        (1, "codex:finding-1", "kept"),
        (1, "round:1", "round_fixed"),
        (2, "round:2", "round_clean"),
        (2, "loop", "converged"),
    ]


def test_runner_escalates_at_max_rounds_when_findings_never_converge(tmp_path) -> None:
    audit_path = tmp_path / "review-fix.jsonl"
    fixed_rounds: list[int] = []

    def gather(status: ReviewFixLoopStatus) -> list[ReviewFixFinding]:
        return [
            ReviewFixFinding(
                finding_id=f"codex:finding-{status.round_number}",
                category="codex-review",
            )
        ]

    def classify(
        finding: ReviewFixFinding,
        status: ReviewFixLoopStatus,
    ) -> ReviewFixClassification:
        return ReviewFixClassification(fixable=True)

    def fix(
        work: ReviewFixLoopWork,
        status: ReviewFixLoopStatus,
    ) -> ReviewFixLoopFixResult:
        fixed_rounds.append(status.round_number)
        return ReviewFixLoopFixResult(
            commit=f"deadbeef{status.round_number}",
            verdict="kept",
            tests=("pytest",),
        )

    outcome = ReviewFixLoopRunner(
        enablement=_enablement(tmp_path, max_rounds=3, escalation="page-human-reviewer"),
        audit_log=ReviewFixAuditLog(audit_path),
        seams=ReviewFixLoopSeams(gather=gather, classify=classify, fix=fix),
        root_path=tmp_path,
        now=lambda: _NOW,
    ).run()

    assert outcome.status is ReviewFixLoopOutcomeStatus.ESCALATED
    assert outcome.rounds_run == 3
    assert outcome.escalation == "page-human-reviewer"
    assert fixed_rounds == [1, 2, 3]

    records = ReviewFixAuditLog(audit_path).read_all()
    assert records[-1].finding_id == "loop"
    assert records[-1].verdict == "escalated"
    assert records[-1].tests == ("page-human-reviewer", "max_rounds=3")
    assert [record.verdict for record in records].count("round_fixed") == 3


def test_runner_honors_kill_switch_before_next_round(tmp_path) -> None:
    audit_path = tmp_path / "review-fix.jsonl"
    kill_switch = tmp_path / ".voyager" / "review-fix.disabled"
    gathered_rounds: list[int] = []

    def gather(status: ReviewFixLoopStatus) -> list[ReviewFixFinding]:
        gathered_rounds.append(status.round_number)
        return [ReviewFixFinding(finding_id="codex:finding-kill", category="codex-review")]

    def classify(
        finding: ReviewFixFinding,
        status: ReviewFixLoopStatus,
    ) -> ReviewFixClassification:
        return ReviewFixClassification(fixable=True)

    def fix(
        work: ReviewFixLoopWork,
        status: ReviewFixLoopStatus,
    ) -> ReviewFixLoopFixResult:
        kill_switch.parent.mkdir(parents=True)
        kill_switch.write_text("stop\n", encoding="utf-8")
        return ReviewFixLoopFixResult(
            commit="abc456",
            verdict="kept",
            tests=("pytest",),
        )

    outcome = ReviewFixLoopRunner(
        enablement=_enablement(tmp_path, max_rounds=4),
        audit_log=ReviewFixAuditLog(audit_path),
        seams=ReviewFixLoopSeams(gather=gather, classify=classify, fix=fix),
        root_path=tmp_path,
        now=lambda: _NOW,
    ).run()

    assert outcome.status is ReviewFixLoopOutcomeStatus.KILL_SWITCH
    assert outcome.rounds_run == 1
    assert gathered_rounds == [1]

    records = ReviewFixAuditLog(audit_path).read_all()
    assert records[-1].finding_id == "kill-switch"
    assert records[-1].verdict == "kill_switch"
    assert records[-1].tests == (str(kill_switch),)


def test_runner_honors_kill_switch_between_findings(tmp_path) -> None:
    audit_path = tmp_path / "review-fix.jsonl"
    kill_switch = tmp_path / ".voyager" / "review-fix.disabled"
    classified: list[str] = []
    fixed: list[str] = []

    def gather(status: ReviewFixLoopStatus) -> list[ReviewFixFinding]:
        return [
            ReviewFixFinding(finding_id="codex:finding-1", category="codex-review"),
            ReviewFixFinding(finding_id="codex:finding-2", category="codex-review"),
        ]

    def classify(
        finding: ReviewFixFinding,
        status: ReviewFixLoopStatus,
    ) -> ReviewFixClassification:
        classified.append(finding.finding_id)
        return ReviewFixClassification(fixable=True)

    def fix(
        work: ReviewFixLoopWork,
        status: ReviewFixLoopStatus,
    ) -> ReviewFixLoopFixResult:
        fixed.append(work.finding.finding_id)
        kill_switch.parent.mkdir(parents=True)
        kill_switch.write_text("stop\n", encoding="utf-8")
        return ReviewFixLoopFixResult(commit="abc654", verdict="kept", tests=("pytest",))

    outcome = ReviewFixLoopRunner(
        enablement=_enablement(tmp_path, max_rounds=4),
        audit_log=ReviewFixAuditLog(audit_path),
        seams=ReviewFixLoopSeams(gather=gather, classify=classify, fix=fix),
        root_path=tmp_path,
        now=lambda: _NOW,
    ).run()

    assert outcome.status is ReviewFixLoopOutcomeStatus.KILL_SWITCH
    assert classified == ["codex:finding-1"]
    assert fixed == ["codex:finding-1"]

    records = ReviewFixAuditLog(audit_path).read_all()
    assert ("codex:finding-2", "kept") not in {
        (record.finding_id, record.verdict) for record in records
    }
    assert records[-1].verdict == "kill_switch"


def test_runner_defers_findings_past_max_fixes_per_round(tmp_path) -> None:
    audit_path = tmp_path / "review-fix.jsonl"
    fixed: list[str] = []

    def gather(status: ReviewFixLoopStatus) -> list[ReviewFixFinding]:
        return [
            ReviewFixFinding(finding_id="codex:finding-1", category="codex-review"),
            ReviewFixFinding(finding_id="codex:finding-2", category="codex-review"),
        ]

    def classify(
        finding: ReviewFixFinding,
        status: ReviewFixLoopStatus,
    ) -> ReviewFixClassification:
        return ReviewFixClassification(fixable=True)

    def fix(
        work: ReviewFixLoopWork,
        status: ReviewFixLoopStatus,
    ) -> ReviewFixLoopFixResult:
        fixed.append(work.finding.finding_id)
        return ReviewFixLoopFixResult(commit="abc789", verdict="kept", tests=("pytest",))

    outcome = ReviewFixLoopRunner(
        enablement=_enablement(tmp_path, max_rounds=1, max_fixes_per_round=1),
        audit_log=ReviewFixAuditLog(audit_path),
        seams=ReviewFixLoopSeams(gather=gather, classify=classify, fix=fix),
        root_path=tmp_path,
        now=lambda: _NOW,
    ).run()

    assert outcome.status is ReviewFixLoopOutcomeStatus.ESCALATED
    assert fixed == ["codex:finding-1"]

    records = ReviewFixAuditLog(audit_path).read_all()
    assert ("codex:finding-2", "fix_cap_deferred") in {
        (record.finding_id, record.verdict) for record in records
    }


def test_runner_requires_safety_envelope(tmp_path) -> None:
    seams = ReviewFixLoopSeams(
        gather=lambda status: [],
        classify=lambda finding, status: ReviewFixClassification(fixable=False),
        fix=lambda work, status: ReviewFixLoopFixResult(
            commit="unused",
            verdict="kept",
            tests=("pytest",),
        ),
    )

    with pytest.raises(ReviewFixLoopRunnerError, match="requires a safety envelope"):
        ReviewFixLoopRunner(
            enablement=EnablementConfig(autonomy=Autonomy.L1),
            audit_log=ReviewFixAuditLog(tmp_path / "review-fix.jsonl"),
            seams=seams,
            root_path=tmp_path,
        ).run()


def _enablement(
    tmp_path: Path,
    *,
    max_rounds: int,
    escalation: str = "request-human-review",
    max_fixes_per_round: int = 2,
) -> EnablementConfig:
    return EnablementConfig(
        autonomy=Autonomy.L3,
        envelope=SafetyEnvelope(
            max_rounds=max_rounds,
            max_fixes_per_round=max_fixes_per_round,
            kill_switch_path=Path(".voyager/review-fix.disabled"),
            escalation=escalation,
            verify_command="pytest tests/unit/test_governance_loop_runner.py",
        ),
    )
