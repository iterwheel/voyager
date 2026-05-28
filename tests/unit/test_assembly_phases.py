"""Unit tests for Assembly phase orchestration (VOY-1817 #96).

Covers:
- PhaseMode.from_env resolution (default, explicit two-phase)
- select_phase_backend precedence (per-phase env, global env, fallback)
- PhaseResult status/summary/is_success/is_blocking properties
- combine_phase_results for all four AC scenarios
- Comment rendering with phase status
"""

from __future__ import annotations

from voyager.bots.assembly.comment import build_assembly_comment
from voyager.bots.assembly.constants import (
    ASSEMBLY_EXECUTION_BACKEND_ENV,
    ASSEMBLY_IMPLEMENTER_BACKEND_ENV,
    ASSEMBLY_PHASE_MODE_ENV,
    ASSEMBLY_TESTPILOT_BACKEND_ENV,
)
from voyager.bots.assembly.phase import (
    PhaseMode,
    PhaseName,
    PhaseResult,
    combine_phase_results,
    select_phase_backend,
)

# ---------------------------------------------------------------------------
# PhaseMode.from_env
# ---------------------------------------------------------------------------


def test_phase_mode_defaults_to_single() -> None:
    assert PhaseMode.from_env() == PhaseMode.SINGLE


def test_phase_mode_two_phase_when_env_set(monkeypatch) -> None:
    monkeypatch.setenv(ASSEMBLY_PHASE_MODE_ENV, "two-phase")
    assert PhaseMode.from_env() == PhaseMode.TWO_PHASE


def test_phase_mode_single_when_env_unknown(monkeypatch) -> None:
    monkeypatch.setenv(ASSEMBLY_PHASE_MODE_ENV, "unknown-mode")
    assert PhaseMode.from_env() == PhaseMode.SINGLE


def test_phase_mode_case_insensitive(monkeypatch) -> None:
    monkeypatch.setenv(ASSEMBLY_PHASE_MODE_ENV, "TWO-PHASE")
    assert PhaseMode.from_env() == PhaseMode.TWO_PHASE


# ---------------------------------------------------------------------------
# select_phase_backend
# ---------------------------------------------------------------------------


def test_select_phase_backend_falls_back_to_dry_run() -> None:
    backend = select_phase_backend(None, PhaseName.IMPLEMENTER)
    assert backend == "dry-run"


def test_select_phase_backend_uses_global(monkeypatch) -> None:
    monkeypatch.setenv(ASSEMBLY_EXECUTION_BACKEND_ENV, "pi-oh-my-pi-deepseek")
    backend = select_phase_backend("pi-oh-my-pi-deepseek", PhaseName.IMPLEMENTER)
    assert backend == "pi-oh-my-pi-deepseek"


def test_select_phase_backend_implementer_env_overrides_global(monkeypatch) -> None:
    monkeypatch.setenv(ASSEMBLY_IMPLEMENTER_BACKEND_ENV, "fake-subprocess")
    backend = select_phase_backend("pi-oh-my-pi-deepseek", PhaseName.IMPLEMENTER)
    assert backend == "fake-subprocess"


def test_select_phase_backend_testpilot_env_overrides_global(monkeypatch) -> None:
    monkeypatch.setenv(ASSEMBLY_TESTPILOT_BACKEND_ENV, "fake-subprocess")
    backend = select_phase_backend("pi-oh-my-pi-deepseek", PhaseName.TESTPILOT)
    assert backend == "fake-subprocess"


def test_select_phase_backend_implementer_env_does_not_affect_testpilot(
    monkeypatch,
) -> None:
    monkeypatch.setenv(ASSEMBLY_IMPLEMENTER_BACKEND_ENV, "fake-subprocess")
    backend = select_phase_backend("pi-oh-my-pi-deepseek", PhaseName.TESTPILOT)
    assert backend == "pi-oh-my-pi-deepseek"


def test_select_phase_backend_empty_env_falls_back(monkeypatch) -> None:
    monkeypatch.setenv(ASSEMBLY_IMPLEMENTER_BACKEND_ENV, "")
    backend = select_phase_backend(None, PhaseName.IMPLEMENTER)
    assert backend == "dry-run"


# ---------------------------------------------------------------------------
# PhaseResult properties
# ---------------------------------------------------------------------------


def test_phase_result_pending() -> None:
    r = PhaseResult(phase=PhaseName.IMPLEMENTER)
    assert r.status == "pending"
    assert r.summary == "Not started"
    assert r.is_success is False
    assert r.is_blocking is True


def test_phase_result_executed() -> None:
    r = PhaseResult(
        phase=PhaseName.IMPLEMENTER,
        adapter_result={"status": "executed", "summary": "3 commits"},
    )
    assert r.status == "executed"
    assert r.summary == "3 commits"
    assert r.is_success is True
    assert r.is_blocking is False


def test_phase_result_no_changes() -> None:
    r = PhaseResult(
        phase=PhaseName.TESTPILOT,
        adapter_result={"status": "no_changes", "summary": "All AC met"},
    )
    assert r.status == "no_changes"
    assert r.is_success is True
    assert r.is_blocking is False


def test_phase_result_failed() -> None:
    r = PhaseResult(
        phase=PhaseName.IMPLEMENTER,
        adapter_result={"status": "failed", "summary": "OMP subprocess failed"},
    )
    assert r.status == "failed"
    assert r.is_success is False
    assert r.is_blocking is True


def test_phase_result_blocked() -> None:
    r = PhaseResult(
        phase=PhaseName.TESTPILOT,
        adapter_result={"status": "blocked", "summary": "AC #3 not met"},
    )
    assert r.status == "blocked"
    assert r.is_success is False
    assert r.is_blocking is True


def test_phase_result_dry_run() -> None:
    r = PhaseResult(
        phase=PhaseName.IMPLEMENTER,
        adapter_result={"status": "dry_run"},
    )
    assert r.status == "dry_run"
    assert r.summary == "Dry-run recorded"
    assert r.is_success is True
    assert r.is_blocking is False


def test_phase_result_unknown_status() -> None:
    r = PhaseResult(
        phase=PhaseName.IMPLEMENTER,
        adapter_result={"status": "weird_status"},
    )
    assert r.status == "unknown"
    assert r.is_success is False
    assert r.is_blocking is True


def test_phase_result_no_adapter_result_summary_fallback() -> None:
    r = PhaseResult(
        phase=PhaseName.IMPLEMENTER,
        adapter_result={"status": "executed"},
    )
    assert r.summary == "Committed changes"

    r2 = PhaseResult(
        phase=PhaseName.TESTPILOT,
        adapter_result={"status": "blocked"},
    )
    assert r2.summary == "Blocked — gaps reported"


# ---------------------------------------------------------------------------
# combine_phase_results
# ---------------------------------------------------------------------------


def test_combine_single_phase_implementer_executed() -> None:
    imp = PhaseResult(
        phase=PhaseName.IMPLEMENTER,
        adapter_result={"status": "executed"},
    )
    assert combine_phase_results(imp, None) == "applied"


def test_combine_implementer_fails_testpilot_skipped() -> None:
    imp = PhaseResult(
        phase=PhaseName.IMPLEMENTER,
        adapter_result={"status": "failed"},
    )
    assert combine_phase_results(imp, None) == "failed"


def test_combine_implementer_and_testpilot_both_pass() -> None:
    imp = PhaseResult(
        phase=PhaseName.IMPLEMENTER,
        adapter_result={"status": "executed"},
    )
    tp = PhaseResult(
        phase=PhaseName.TESTPILOT,
        adapter_result={"status": "no_changes"},
    )
    assert combine_phase_results(imp, tp) == "applied"


def test_combine_implementer_passes_testpilot_blocks() -> None:
    imp = PhaseResult(
        phase=PhaseName.IMPLEMENTER,
        adapter_result={"status": "executed"},
    )
    tp = PhaseResult(
        phase=PhaseName.TESTPILOT,
        adapter_result={"status": "blocked", "summary": "AC #2 not satisfied"},
    )
    assert combine_phase_results(imp, tp) == "blocked"


def test_combine_implementer_passes_testpilot_fails() -> None:
    imp = PhaseResult(
        phase=PhaseName.IMPLEMENTER,
        adapter_result={"status": "executed"},
    )
    tp = PhaseResult(
        phase=PhaseName.TESTPILOT,
        adapter_result={"status": "failed", "summary": "verification failed"},
    )
    assert combine_phase_results(imp, tp) == "failed"


def test_combine_implementer_passes_testpilot_adds_tests() -> None:
    imp = PhaseResult(
        phase=PhaseName.IMPLEMENTER,
        adapter_result={"status": "executed"},
    )
    tp = PhaseResult(
        phase=PhaseName.TESTPILOT,
        adapter_result={"status": "executed", "summary": "Added 2 test files"},
    )
    assert combine_phase_results(imp, tp) == "applied"


def test_combine_implementer_fails_testpilot_not_available() -> None:
    imp = PhaseResult(
        phase=PhaseName.IMPLEMENTER,
        adapter_result={"status": "failed"},
    )
    assert combine_phase_results(imp, None) == "failed"


# ---------------------------------------------------------------------------
# Comment rendering with phase status
# ---------------------------------------------------------------------------


def _minimal_contract() -> dict:
    return {
        "issue_number": 42,
        "repository": "iterwheel/voyager",
        "acceptance_criteria": ["Do the thing"],
    }


def test_comment_no_phase_section_in_single_mode() -> None:
    body = build_assembly_comment(
        status="applied",
        contract=_minimal_contract(),
        adapter_result={"status": "executed"},
    )
    assert "Phase status:" not in body


def test_comment_phase_status_two_phase_both_pass() -> None:
    body = build_assembly_comment(
        status="applied",
        contract=_minimal_contract(),
        adapter_result={"status": "executed"},
        phase_mode="two-phase",
        testpilot_result={"status": "no_changes", "summary": "All AC verified"},
    )
    assert "Phase status:" in body
    assert "Implementer: completed" in body
    assert "TestPilot: reviewed (no issues found)" in body
    assert "All AC verified" in body


def test_comment_phase_status_testpilot_adds_tests() -> None:
    body = build_assembly_comment(
        status="applied",
        contract=_minimal_contract(),
        adapter_result={"status": "executed"},
        phase_mode="two-phase",
        testpilot_result={"status": "executed", "summary": "Added missing coverage"},
    )
    assert "Implementer: completed" in body
    assert "TestPilot: passed" in body


def test_comment_phase_status_testpilot_blocks() -> None:
    body = build_assembly_comment(
        status="blocked",
        contract=_minimal_contract(),
        adapter_result={"status": "executed"},
        phase_mode="two-phase",
        testpilot_result={"status": "blocked", "summary": "AC #2 not met"},
    )
    assert "Implementer: completed" in body
    assert "TestPilot: blocked" in body
    assert "AC #2 not met" in body


def test_comment_phase_status_testpilot_failed() -> None:
    body = build_assembly_comment(
        status="applied",
        contract=_minimal_contract(),
        adapter_result={"status": "executed"},
        phase_mode="two-phase",
        testpilot_result={"status": "failed", "summary": "Adapter crashed"},
    )
    assert "TestPilot: `failed`" in body
