"""Tests for the stale-PR schedule wiring in voyager.server."""

from __future__ import annotations

import asyncio

import pytest

import voyager.server as server


@pytest.fixture(autouse=True)
async def clean_stale_pr_task(monkeypatch: pytest.MonkeyPatch):
    await server._stop_stale_pr_schedule()
    monkeypatch.setattr(server, "_stale_pr_task", None)
    yield
    await server._stop_stale_pr_schedule()


# ---------------------------------------------------------------------------
# Config helper tests
# ---------------------------------------------------------------------------


class TestStalePrEnabled:
    def test_default_missing_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BRIDGE_STALE_PR_ENABLED", raising=False)
        assert server._stale_pr_enabled() is False

    def test_false_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIDGE_STALE_PR_ENABLED", "false")
        assert server._stale_pr_enabled() is False

    def test_true_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIDGE_STALE_PR_ENABLED", "true")
        assert server._stale_pr_enabled() is True


class TestStalePrIntervalSeconds:
    def test_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BRIDGE_STALE_PR_INTERVAL_SECONDS", raising=False)
        assert server._stale_pr_interval_seconds() == 86400

    def test_custom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIDGE_STALE_PR_INTERVAL_SECONDS", "3600")
        assert server._stale_pr_interval_seconds() == 3600

    def test_clamped_to_minimum(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIDGE_STALE_PR_INTERVAL_SECONDS", "5")
        assert server._stale_pr_interval_seconds() == 60

    def test_invalid_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIDGE_STALE_PR_INTERVAL_SECONDS", "not-a-number")
        assert server._stale_pr_interval_seconds() == 86400


class TestStalePrDays:
    def test_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BRIDGE_STALE_PR_DAYS", raising=False)
        assert server._stale_pr_days() == 7

    def test_custom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIDGE_STALE_PR_DAYS", "14")
        assert server._stale_pr_days() == 14

    def test_clamped_to_minimum(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIDGE_STALE_PR_DAYS", "0")
        assert server._stale_pr_days() == 1

    def test_invalid_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIDGE_STALE_PR_DAYS", "not-a-number")
        assert server._stale_pr_days() == 7


class TestStalePrRepository:
    def test_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BRIDGE_STALE_PR_REPOSITORY", raising=False)
        assert server._stale_pr_repository() == "iterwheel/voyager"

    def test_custom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIDGE_STALE_PR_REPOSITORY", "org/repo")
        assert server._stale_pr_repository() == "org/repo"


class TestStalePrAppSlug:
    def test_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BRIDGE_STALE_PR_APP_SLUG", raising=False)
        assert server._stale_pr_app_slug() == "iterwheel-assembly"

    def test_custom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIDGE_STALE_PR_APP_SLUG", "my-bot")
        assert server._stale_pr_app_slug() == "my-bot"


# ---------------------------------------------------------------------------
# Schedule lifecycle tests
# ---------------------------------------------------------------------------


async def test_stale_pr_schedule_stays_off_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BRIDGE_STALE_PR_ENABLED", raising=False)

    await server._start_stale_pr_schedule()

    assert server._stale_pr_task is None


async def test_stale_pr_schedule_starts_and_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_loop() -> None:
        await asyncio.Event().wait()

    monkeypatch.setenv("BRIDGE_STALE_PR_ENABLED", "true")
    monkeypatch.setattr(server, "_stale_pr_loop", fake_loop)

    await server._start_stale_pr_schedule()

    task = server._stale_pr_task
    assert task is not None
    assert not task.done()

    await server._stop_stale_pr_schedule()

    assert server._stale_pr_task is None


async def test_stale_pr_triage_skips_github_client_in_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setattr(
        server,
        "_get_client",
        lambda: pytest.fail("dry-run stale-PR triage must not initialize GitHub client"),
    )

    with caplog.at_level("INFO"):
        await server._run_stale_pr_triage()

    assert "DRY_RUN: would run stale_pr_triage" in caplog.text
