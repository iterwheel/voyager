"""Cross-test for Assembly adapters — independent env-routing + contract shape.

Covers: select_execution_adapter env routing (default + each backend),
DryRunAdapter contract recording, PiOhMyPiDeepSeekAdapter context validation
failure content per D1.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from voyager.bots.assembly.adapters import (
    AdapterResult,
    DryRunAdapter,
    PiOhMyPiDeepSeekAdapter,
    select_execution_adapter,
)
from voyager.bots.assembly.constants import ASSEMBLY_EXECUTION_BACKEND_ENV
from voyager.bots.assembly.job_contract import AssemblyJobContract


def _make_contract() -> AssemblyJobContract:
    return AssemblyJobContract(
        repository="o/r",
        issue_number=1,
        issue_url="u",
        issue_title="t",
        issue_body="b",
        branch_name="1-t",
        base_branch="main",
        task_summary="s",
        acceptance_criteria=[],
        forbidden_operations=(),
        verification_commands=(),
        delivery_id="d",
        requested_at="2026-01-01T00:00:00Z",
    )


# ---------------------------------------------------------------------------
# select_execution_adapter env routing
# ---------------------------------------------------------------------------


class TestSelectExecutionAdapterDefault:
    def test_no_env_returns_dry_run(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            adapter = select_execution_adapter()
            assert isinstance(adapter, DryRunAdapter)
            assert adapter.name == "dry-run"

    def test_explicit_dry_run_string(self) -> None:
        adapter = select_execution_adapter("dry-run")
        assert isinstance(adapter, DryRunAdapter)

    def test_env_set_to_dry_run(self) -> None:
        with patch.dict(os.environ, {ASSEMBLY_EXECUTION_BACKEND_ENV: "dry-run"}):
            adapter = select_execution_adapter()
            assert isinstance(adapter, DryRunAdapter)

    def test_unknown_value_falls_back_to_dry_run(self) -> None:
        adapter = select_execution_adapter("nonexistent-backend")
        assert isinstance(adapter, DryRunAdapter)

    def test_empty_string_falls_back_to_dry_run(self) -> None:
        adapter = select_execution_adapter("")
        assert isinstance(adapter, DryRunAdapter)

    def test_whitespace_trimmed(self) -> None:
        adapter = select_execution_adapter("  dry-run  ")
        assert isinstance(adapter, DryRunAdapter)


class TestSelectExecutionAdapterPi:
    def test_explicit_pi_string(self) -> None:
        adapter = select_execution_adapter("pi-oh-my-pi-deepseek")
        assert isinstance(adapter, PiOhMyPiDeepSeekAdapter)
        assert adapter.name == "pi-oh-my-pi-deepseek"
        assert adapter.requires_installation_token is True

    def test_env_set_to_pi(self) -> None:
        with patch.dict(os.environ, {ASSEMBLY_EXECUTION_BACKEND_ENV: "pi-oh-my-pi-deepseek"}):
            adapter = select_execution_adapter()
            assert isinstance(adapter, PiOhMyPiDeepSeekAdapter)

    def test_env_case_insensitive(self) -> None:
        with patch.dict(os.environ, {ASSEMBLY_EXECUTION_BACKEND_ENV: "PI-OH-MY-PI-DEEPSEEK"}):
            adapter = select_execution_adapter()
            assert isinstance(adapter, PiOhMyPiDeepSeekAdapter)

    def test_explicit_arg_overrides_env(self) -> None:
        with patch.dict(os.environ, {ASSEMBLY_EXECUTION_BACKEND_ENV: "dry-run"}):
            adapter = select_execution_adapter("pi-oh-my-pi-deepseek")
            assert isinstance(adapter, PiOhMyPiDeepSeekAdapter)

    def test_env_ignored_when_explicit_arg_none(self) -> None:
        with patch.dict(os.environ, {ASSEMBLY_EXECUTION_BACKEND_ENV: "pi-oh-my-pi-deepseek"}):
            adapter = select_execution_adapter(None)  # type: ignore[arg-type]
            assert isinstance(adapter, PiOhMyPiDeepSeekAdapter)


# ---------------------------------------------------------------------------
# DryRunAdapter contract shape
# ---------------------------------------------------------------------------


class TestDryRunAdapterContract:
    @pytest.mark.asyncio
    async def test_returns_dry_run_status(self) -> None:
        adapter = DryRunAdapter()
        contract = _make_contract()
        result = await adapter.execute(contract)
        assert result.status == "dry_run"
        assert result.commit_shas == []
        assert "no commits" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_records_contract(self) -> None:
        adapter = DryRunAdapter()
        contract = _make_contract()
        await adapter.execute(contract)
        assert adapter.last_contract is contract

    @pytest.mark.asyncio
    async def test_records_contract_in_details(self) -> None:
        adapter = DryRunAdapter()
        contract = _make_contract()
        result = await adapter.execute(contract)
        assert "recorded" in result.details
        assert result.details["recorded"]["issue_number"] == 1

    @pytest.mark.asyncio
    async def test_name_attribute(self) -> None:
        adapter = DryRunAdapter()
        assert adapter.name == "dry-run"


# ---------------------------------------------------------------------------
# PiOhMyPiDeepSeekAdapter context validation
# ---------------------------------------------------------------------------


class TestPiOhMyPiDeepSeekAdapter:
    @pytest.mark.asyncio
    async def test_missing_context_returns_failed_result(self) -> None:
        adapter = PiOhMyPiDeepSeekAdapter()
        contract = _make_contract()
        result = await adapter.execute(contract)
        assert result.status == "failed"
        assert result.commit_shas == []
        assert "context" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_missing_context_mentions_required_context(self) -> None:
        adapter = PiOhMyPiDeepSeekAdapter()
        contract = _make_contract()
        result = await adapter.execute(contract)
        assert "requires" in result.summary.lower()
        assert "adapter execution context" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_missing_context_mentions_pi_oh_my_pi_deepseek(self) -> None:
        adapter = PiOhMyPiDeepSeekAdapter()
        contract = _make_contract()
        result = await adapter.execute(contract)
        msg = result.summary.lower()
        assert "pi" in msg or "deepseek" in msg

    def test_name_attribute(self) -> None:
        adapter = PiOhMyPiDeepSeekAdapter()
        assert adapter.name == "pi-oh-my-pi-deepseek"

    def test_requires_installation_token(self) -> None:
        adapter = PiOhMyPiDeepSeekAdapter()
        assert adapter.requires_installation_token is True


# ---------------------------------------------------------------------------
# AdapterResult dataclass
# ---------------------------------------------------------------------------


class TestAdapterResultDataclass:
    def test_frozen(self) -> None:
        result = AdapterResult(status="dry_run")
        with pytest.raises(__import__("dataclasses").FrozenInstanceError):
            result.status = "changed"  # type: ignore[misc]

    def test_defaults(self) -> None:
        result = AdapterResult(status="executed")
        assert result.commit_shas == []
        assert result.summary == ""
        assert result.details == {}

    def test_full_construction(self) -> None:
        result = AdapterResult(
            status="executed",
            commit_shas=["abc123"],
            summary="done",
            details={"plan": "x"},
        )
        assert result.status == "executed"
        assert result.commit_shas == ["abc123"]
        assert result.summary == "done"
        assert result.details == {"plan": "x"}
