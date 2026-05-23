"""Unit tests for Assembly execution adapters (VOY-1817 Surface 18)."""

from __future__ import annotations

import asyncio

from voyager.bots.assembly.adapters import (
    DryRunAdapter,
    PiOhMyPiDeepSeekAdapter,
    select_execution_adapter,
)
from voyager.bots.assembly.constants import (
    ASSEMBLY_BACKEND_DRY_RUN,
    ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK,
    ASSEMBLY_EXECUTION_BACKEND_ENV,
)
from voyager.bots.assembly.job_contract import build_job_contract


def _contract():
    return build_job_contract(
        issue={
            "number": 69,
            "title": "[Feature]: x",
            "body": "## Problem / Goal\n\nDo a thing\n\n## Acceptance Criteria\n\n- [ ] one\n",
            "html_url": "https://example/issues/69",
        },
        repository="iterwheel/voyager-sandbox",
        branch_name="69-x",
        delivery_id="d",
    )


def test_dry_run_adapter_returns_dry_run_status() -> None:
    adapter = DryRunAdapter()
    result = asyncio.run(adapter.execute(_contract()))
    assert result.status == "dry_run"
    assert result.commit_shas == []
    assert "Dry-run" in result.summary
    assert adapter.last_contract is not None


def test_pi_oh_my_pi_deepseek_adapter_missing_context_returns_failed() -> None:
    adapter = PiOhMyPiDeepSeekAdapter()
    result = asyncio.run(adapter.execute(_contract()))
    assert result.status == "failed"
    assert result.commit_shas == []
    assert "adapter execution context" in result.summary.lower()
    assert adapter.requires_installation_token is True


def test_select_execution_adapter_defaults_to_dry_run(monkeypatch) -> None:
    monkeypatch.delenv(ASSEMBLY_EXECUTION_BACKEND_ENV, raising=False)
    adapter = select_execution_adapter()
    assert isinstance(adapter, DryRunAdapter)
    assert adapter.name == ASSEMBLY_BACKEND_DRY_RUN


def test_select_execution_adapter_pi_path(monkeypatch) -> None:
    monkeypatch.setenv(ASSEMBLY_EXECUTION_BACKEND_ENV, ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK)
    adapter = select_execution_adapter()
    assert isinstance(adapter, PiOhMyPiDeepSeekAdapter)
    assert adapter.requires_installation_token is True


def test_select_execution_adapter_unknown_backend_falls_back_to_dry_run(monkeypatch) -> None:
    monkeypatch.setenv(ASSEMBLY_EXECUTION_BACKEND_ENV, "totally-not-a-backend")
    adapter = select_execution_adapter()
    assert isinstance(adapter, DryRunAdapter)


def test_explicit_backend_overrides_env(monkeypatch) -> None:
    monkeypatch.setenv(ASSEMBLY_EXECUTION_BACKEND_ENV, ASSEMBLY_BACKEND_PI_OH_MY_PI_DEEPSEEK)
    adapter = select_execution_adapter(ASSEMBLY_BACKEND_DRY_RUN)
    assert isinstance(adapter, DryRunAdapter)
