from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from voyager.governance.audit_log import ReviewFixAuditLog, ReviewFixAuditRecord
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


def test_runner_honors_kill_switch_after_final_fix(tmp_path) -> None:
    audit_path = tmp_path / "review-fix.jsonl"
    kill_switch = tmp_path / ".voyager" / "review-fix.disabled"

    def gather(status: ReviewFixLoopStatus) -> list[ReviewFixFinding]:
        return [ReviewFixFinding(finding_id="codex:finding-1", category="codex-review")]

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
        return ReviewFixLoopFixResult(commit="abc987", verdict="kept", tests=("pytest",))

    outcome = ReviewFixLoopRunner(
        enablement=_enablement(tmp_path, max_rounds=1),
        audit_log=ReviewFixAuditLog(audit_path),
        seams=ReviewFixLoopSeams(gather=gather, classify=classify, fix=fix),
        root_path=tmp_path,
        now=lambda: _NOW,
    ).run()

    assert outcome.status is ReviewFixLoopOutcomeStatus.KILL_SWITCH
    assert outcome.rounds_run == 1

    records = ReviewFixAuditLog(audit_path).read_all()
    assert [(record.finding_id, record.verdict) for record in records] == [
        ("codex:finding-1", "kept"),
        ("round:1", "round_fixed"),
        ("kill-switch", "kill_switch"),
    ]


def test_runner_rechecks_kill_switch_after_classification_before_fix(tmp_path) -> None:
    audit_path = tmp_path / "review-fix.jsonl"
    kill_switch = tmp_path / ".voyager" / "review-fix.disabled"
    fixed: list[str] = []

    def gather(status: ReviewFixLoopStatus) -> list[ReviewFixFinding]:
        return [ReviewFixFinding(finding_id="codex:finding-1", category="codex-review")]

    def classify(
        finding: ReviewFixFinding,
        status: ReviewFixLoopStatus,
    ) -> ReviewFixClassification:
        kill_switch.parent.mkdir(parents=True)
        kill_switch.write_text("stop\n", encoding="utf-8")
        return ReviewFixClassification(fixable=True)

    def fix(
        work: ReviewFixLoopWork,
        status: ReviewFixLoopStatus,
    ) -> ReviewFixLoopFixResult:
        fixed.append(work.finding.finding_id)
        return ReviewFixLoopFixResult(commit="unused", verdict="kept", tests=("pytest",))

    outcome = ReviewFixLoopRunner(
        enablement=_enablement(tmp_path, max_rounds=3),
        audit_log=ReviewFixAuditLog(audit_path),
        seams=ReviewFixLoopSeams(gather=gather, classify=classify, fix=fix),
        root_path=tmp_path,
        now=lambda: _NOW,
    ).run()

    assert outcome.status is ReviewFixLoopOutcomeStatus.KILL_SWITCH
    assert fixed == []
    assert ReviewFixAuditLog(audit_path).read_all()[-1].verdict == "kill_switch"


def test_runner_rechecks_kill_switch_after_clean_gather(tmp_path) -> None:
    audit_path = tmp_path / "review-fix.jsonl"
    kill_switch = tmp_path / ".voyager" / "review-fix.disabled"
    classified: list[str] = []

    def gather(status: ReviewFixLoopStatus) -> list[ReviewFixFinding]:
        kill_switch.parent.mkdir(parents=True)
        kill_switch.write_text("stop\n", encoding="utf-8")
        return []

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
        raise AssertionError("fix should not be called after kill-switch during gather")

    outcome = ReviewFixLoopRunner(
        enablement=_enablement(tmp_path, max_rounds=3),
        audit_log=ReviewFixAuditLog(audit_path),
        seams=ReviewFixLoopSeams(gather=gather, classify=classify, fix=fix),
        root_path=tmp_path,
        now=lambda: _NOW,
    ).run()

    assert outcome.status is ReviewFixLoopOutcomeStatus.KILL_SWITCH
    assert outcome.rounds_run == 1
    assert classified == []

    records = ReviewFixAuditLog(audit_path).read_all()
    assert [(record.finding_id, record.verdict) for record in records] == [
        ("kill-switch", "kill_switch")
    ]


def test_runner_honors_kill_switch_after_not_fixable_classification(tmp_path) -> None:
    audit_path = tmp_path / "review-fix.jsonl"
    kill_switch = tmp_path / ".voyager" / "review-fix.disabled"
    classified: list[str] = []

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
        kill_switch.parent.mkdir(parents=True)
        kill_switch.write_text("stop\n", encoding="utf-8")
        return ReviewFixClassification(fixable=False, reason="needs-human")

    outcome = ReviewFixLoopRunner(
        enablement=_enablement(tmp_path, max_rounds=3),
        audit_log=ReviewFixAuditLog(audit_path),
        seams=ReviewFixLoopSeams(
            gather=gather,
            classify=classify,
            fix=lambda work, status: ReviewFixLoopFixResult(
                commit="unused",
                verdict="kept",
                tests=("pytest",),
            ),
        ),
        root_path=tmp_path,
        now=lambda: _NOW,
    ).run()

    assert outcome.status is ReviewFixLoopOutcomeStatus.KILL_SWITCH
    assert classified == ["codex:finding-1"]

    records = ReviewFixAuditLog(audit_path).read_all()
    assert ("codex:finding-1", "not_fixable") in {
        (record.finding_id, record.verdict) for record in records
    }
    assert ("codex:finding-2", "not_fixable") not in {
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


def test_runner_audits_not_fixable_without_calling_fix(tmp_path) -> None:
    audit_path = tmp_path / "review-fix.jsonl"
    fixed: list[str] = []

    def gather(status: ReviewFixLoopStatus) -> list[ReviewFixFinding]:
        return [ReviewFixFinding(finding_id="codex:not-fixable", category="codex-review")]

    def classify(
        finding: ReviewFixFinding,
        status: ReviewFixLoopStatus,
    ) -> ReviewFixClassification:
        return ReviewFixClassification(fixable=False, reason="needs-human")

    def fix(
        work: ReviewFixLoopWork,
        status: ReviewFixLoopStatus,
    ) -> ReviewFixLoopFixResult:
        fixed.append(work.finding.finding_id)
        return ReviewFixLoopFixResult(commit="unused", verdict="kept", tests=("pytest",))

    outcome = ReviewFixLoopRunner(
        enablement=_enablement(tmp_path, max_rounds=1),
        audit_log=ReviewFixAuditLog(audit_path),
        seams=ReviewFixLoopSeams(gather=gather, classify=classify, fix=fix),
        root_path=tmp_path,
        now=lambda: _NOW,
    ).run()

    assert outcome.status is ReviewFixLoopOutcomeStatus.ESCALATED
    assert fixed == []

    records = ReviewFixAuditLog(audit_path).read_all()
    assert ("codex:not-fixable", "not_fixable", ("reason=needs-human",)) in {
        (record.finding_id, record.verdict, record.tests) for record in records
    }


def test_runner_skips_duplicate_fix_audit_when_seam_recorded_it(tmp_path) -> None:
    audit_path = tmp_path / "review-fix.jsonl"
    gather_calls = 0

    def gather(status: ReviewFixLoopStatus) -> list[ReviewFixFinding]:
        nonlocal gather_calls
        gather_calls += 1
        if gather_calls == 1:
            return [ReviewFixFinding(finding_id="codex:finding-1", category="codex-review")]
        return []

    def fix(
        work: ReviewFixLoopWork,
        status: ReviewFixLoopStatus,
    ) -> ReviewFixLoopFixResult:
        ReviewFixAuditLog(audit_path).append(
            ReviewFixAuditRecord(
                round=status.round_number,
                ts=_NOW,
                commit="abc123",
                finding_id=work.finding.finding_id,
                category=work.finding.category,
                verdict="kept",
                tests=("pytest",),
            )
        )
        return ReviewFixLoopFixResult(
            commit="abc123",
            verdict="kept",
            tests=("pytest",),
            audit_recorded=True,
        )

    outcome = ReviewFixLoopRunner(
        enablement=_enablement(tmp_path, max_rounds=2),
        audit_log=ReviewFixAuditLog(audit_path),
        seams=ReviewFixLoopSeams(
            gather=gather,
            classify=lambda finding, status: ReviewFixClassification(fixable=True),
            fix=fix,
        ),
        root_path=tmp_path,
        now=lambda: _NOW,
    ).run()

    assert outcome.status is ReviewFixLoopOutcomeStatus.CONVERGED

    records = ReviewFixAuditLog(audit_path).read_all()
    assert [(record.finding_id, record.verdict) for record in records] == [
        ("codex:finding-1", "kept"),
        ("round:1", "round_fixed"),
        ("round:2", "round_clean"),
        ("loop", "converged"),
    ]


def test_runner_normalizes_blank_seam_audit_text(tmp_path) -> None:
    audit_path = tmp_path / "review-fix.jsonl"

    def gather(status: ReviewFixLoopStatus) -> list[ReviewFixFinding]:
        return [
            ReviewFixFinding(finding_id="codex:not-fixable", category="codex-review"),
            ReviewFixFinding(finding_id="codex:fixable", category="codex-review"),
        ]

    def classify(
        finding: ReviewFixFinding,
        status: ReviewFixLoopStatus,
    ) -> ReviewFixClassification:
        return ReviewFixClassification(
            fixable=finding.finding_id == "codex:fixable",
            reason="   ",
        )

    def fix(
        work: ReviewFixLoopWork,
        status: ReviewFixLoopStatus,
    ) -> ReviewFixLoopFixResult:
        return ReviewFixLoopFixResult(commit="abc987", verdict="kept", tests=("pytest", "   "))

    ReviewFixLoopRunner(
        enablement=_enablement(tmp_path, max_rounds=1),
        audit_log=ReviewFixAuditLog(audit_path),
        seams=ReviewFixLoopSeams(gather=gather, classify=classify, fix=fix),
        root_path=tmp_path,
        now=lambda: _NOW,
    ).run()

    records = ReviewFixAuditLog(audit_path).read_all()
    assert ("codex:not-fixable", "not_fixable", ("reason=not_fixable",)) in {
        (record.finding_id, record.verdict, record.tests) for record in records
    }
    assert ("codex:fixable", "kept", ("pytest",)) in {
        (record.finding_id, record.verdict, record.tests) for record in records
    }


def test_runner_preexisting_kill_switch_halts_before_gather(tmp_path) -> None:
    audit_path = tmp_path / "review-fix.jsonl"
    kill_switch = tmp_path / ".voyager" / "review-fix.disabled"
    kill_switch.parent.mkdir(parents=True)
    kill_switch.write_text("stop\n", encoding="utf-8")
    gathered: list[int] = []

    outcome = ReviewFixLoopRunner(
        enablement=_enablement(tmp_path, max_rounds=3),
        audit_log=ReviewFixAuditLog(audit_path),
        seams=ReviewFixLoopSeams(
            gather=lambda status: gathered.append(status.round_number) or [],
            classify=lambda finding, status: ReviewFixClassification(fixable=True),
            fix=lambda work, status: ReviewFixLoopFixResult(
                commit="unused",
                verdict="kept",
                tests=("pytest",),
            ),
        ),
        root_path=tmp_path,
        now=lambda: _NOW,
    ).run()

    assert outcome.status is ReviewFixLoopOutcomeStatus.KILL_SWITCH
    assert outcome.rounds_run == 0
    assert gathered == []
    assert ReviewFixAuditLog(audit_path).read_all()[0].verdict == "kill_switch"


def test_runner_requires_multiple_consecutive_clean_rounds_when_configured(tmp_path) -> None:
    audit_path = tmp_path / "review-fix.jsonl"
    gathered: list[int] = []

    def gather(status: ReviewFixLoopStatus) -> list[ReviewFixFinding]:
        gathered.append(status.round_number)
        return []

    outcome = ReviewFixLoopRunner(
        enablement=_enablement(tmp_path, max_rounds=3),
        audit_log=ReviewFixAuditLog(audit_path),
        seams=ReviewFixLoopSeams(
            gather=gather,
            classify=lambda finding, status: ReviewFixClassification(fixable=False),
            fix=lambda work, status: ReviewFixLoopFixResult(
                commit="unused",
                verdict="kept",
                tests=("pytest",),
            ),
        ),
        root_path=tmp_path,
        now=lambda: _NOW,
        clean_rounds_required=2,
    ).run()

    assert outcome.status is ReviewFixLoopOutcomeStatus.CONVERGED
    assert outcome.rounds_run == 2
    assert outcome.clean_rounds == 2
    assert gathered == [1, 2]


def test_runner_rejects_impossible_clean_round_requirement(tmp_path) -> None:
    runner = ReviewFixLoopRunner(
        enablement=_enablement(tmp_path, max_rounds=1),
        audit_log=ReviewFixAuditLog(tmp_path / "review-fix.jsonl"),
        seams=ReviewFixLoopSeams(
            gather=lambda status: [],
            classify=lambda finding, status: ReviewFixClassification(fixable=False),
            fix=lambda work, status: ReviewFixLoopFixResult(
                commit="unused",
                verdict="kept",
                tests=("pytest",),
            ),
        ),
        root_path=tmp_path,
        clean_rounds_required=2,
    )

    with pytest.raises(ReviewFixLoopRunnerError, match="clean_rounds_required"):
        runner.run()


def test_runner_requires_l3_autonomy(tmp_path) -> None:
    fixed: list[str] = []
    seams = ReviewFixLoopSeams(
        gather=lambda status: [ReviewFixFinding(finding_id="codex:finding", category="codex")],
        classify=lambda finding, status: ReviewFixClassification(fixable=True),
        fix=lambda work, status: (
            fixed.append(work.finding.finding_id)
            or ReviewFixLoopFixResult(
                commit="unused",
                verdict="kept",
                tests=("pytest",),
            )
        ),
    )

    with pytest.raises(ReviewFixLoopRunnerError, match="requires autonomy L3"):
        ReviewFixLoopRunner(
            enablement=EnablementConfig(
                autonomy=Autonomy.L2,
                envelope=SafetyEnvelope(
                    max_rounds=1,
                    max_fixes_per_round=1,
                    kill_switch_path=Path(".voyager/review-fix.disabled"),
                    escalation="request-human-review",
                    verify_command="pytest",
                ),
            ),
            audit_log=ReviewFixAuditLog(tmp_path / "review-fix.jsonl"),
            seams=seams,
            root_path=tmp_path,
        ).run()

    assert fixed == []


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
            enablement=EnablementConfig(autonomy=Autonomy.L3),
            audit_log=ReviewFixAuditLog(tmp_path / "review-fix.jsonl"),
            seams=seams,
            root_path=tmp_path,
        ).run()


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("max_rounds", 0, "max_rounds must be >= 1"),
        ("max_fixes_per_round", 0, "max_fixes_per_round must be >= 1"),
        (
            "kill_switch_path",
            ".voyager/review-fix.disabled",
            "kill_switch_path must be a pathlib.Path",
        ),
        ("escalation", "   ", "escalation must be a non-empty string"),
        ("verify_command", "   ", "verify_command must be a non-empty string"),
    ],
)
def test_runner_validates_programmatic_envelope_before_seams(
    tmp_path,
    field: str,
    value: object,
    match: str,
) -> None:
    audit_path = tmp_path / "review-fix.jsonl"
    calls: list[str] = []
    envelope = {
        "max_rounds": 1,
        "max_fixes_per_round": 1,
        "kill_switch_path": Path(".voyager/review-fix.disabled"),
        "escalation": "request-human-review",
        "verify_command": "pytest",
    }
    envelope[field] = value

    def gather(status: ReviewFixLoopStatus) -> list[ReviewFixFinding]:
        calls.append("gather")
        return [ReviewFixFinding(finding_id="codex:finding", category="codex")]

    def fix(
        work: ReviewFixLoopWork,
        status: ReviewFixLoopStatus,
    ) -> ReviewFixLoopFixResult:
        calls.append("fix")
        return ReviewFixLoopFixResult(commit="unused", verdict="kept", tests=("pytest",))

    with pytest.raises(ReviewFixLoopRunnerError, match=match):
        ReviewFixLoopRunner(
            enablement=EnablementConfig(
                autonomy=Autonomy.L3,
                envelope=SafetyEnvelope(**envelope),
            ),
            audit_log=ReviewFixAuditLog(audit_path),
            seams=ReviewFixLoopSeams(
                gather=gather,
                classify=lambda finding, status: ReviewFixClassification(fixable=True),
                fix=fix,
            ),
            root_path=tmp_path,
        ).run()

    assert calls == []
    assert ReviewFixAuditLog(audit_path).read_all() == []


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
