"""Tests for the CI-failing sweep schedule wiring in voyager.server."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import voyager.server as server


@pytest.fixture(autouse=True)
async def clean_ci_failing_task(monkeypatch: pytest.MonkeyPatch):
    await server._stop_ci_failing_schedule()
    monkeypatch.setattr(server, "_ci_failing_task", None)
    yield
    await server._stop_ci_failing_schedule()


class TestCiFailingEnabled:
    def test_default_missing_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BRIDGE_CI_FAILING_ENABLED", raising=False)
        assert server._ci_failing_enabled() is False

    def test_false_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIDGE_CI_FAILING_ENABLED", "false")
        assert server._ci_failing_enabled() is False

    def test_true_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIDGE_CI_FAILING_ENABLED", "true")
        assert server._ci_failing_enabled() is True


class TestCiFailingIntervalSeconds:
    def test_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BRIDGE_CI_FAILING_INTERVAL_SECONDS", raising=False)
        assert server._ci_failing_interval_seconds() == 86400

    def test_custom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIDGE_CI_FAILING_INTERVAL_SECONDS", "3600")
        assert server._ci_failing_interval_seconds() == 3600

    def test_clamped_to_minimum(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIDGE_CI_FAILING_INTERVAL_SECONDS", "5")
        assert server._ci_failing_interval_seconds() == 60

    def test_invalid_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIDGE_CI_FAILING_INTERVAL_SECONDS", "not-a-number")
        assert server._ci_failing_interval_seconds() == 86400


class TestCiFailingRepository:
    def test_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BRIDGE_CI_FAILING_REPOSITORY", raising=False)
        assert server._ci_failing_repository() == "iterwheel/voyager"

    def test_custom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIDGE_CI_FAILING_REPOSITORY", "org/repo")
        assert server._ci_failing_repository() == "org/repo"


class TestCiFailingAppSlug:
    def test_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BRIDGE_CI_FAILING_APP_SLUG", raising=False)
        assert server._ci_failing_app_slug() == "iterwheel-assembly"

    def test_custom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIDGE_CI_FAILING_APP_SLUG", "my-bot")
        assert server._ci_failing_app_slug() == "my-bot"


def test_ci_failing_agent_slug_uses_feature_specific_allow_list_slug() -> None:
    assert server._ci_failing_agent_slug() == "iterwheel-ci-failing"


async def test_ci_failing_schedule_stays_off_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BRIDGE_CI_FAILING_ENABLED", raising=False)

    await server._start_ci_failing_schedule()

    assert server._ci_failing_task is None


async def test_ci_failing_schedule_starts_and_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_loop() -> None:
        await asyncio.Event().wait()

    monkeypatch.setenv("BRIDGE_CI_FAILING_ENABLED", "true")
    monkeypatch.setattr(server, "_ci_failing_loop", fake_loop)

    await server._start_ci_failing_schedule()

    task = server._ci_failing_task
    assert task is not None
    assert not task.done()

    await server._stop_ci_failing_schedule()

    assert server._ci_failing_task is None


async def test_ci_failing_sweep_skips_github_client_in_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setattr(
        server,
        "_get_client",
        lambda: pytest.fail("dry-run CI-failing sweep must not initialize GitHub client"),
    )

    with caplog.at_level("INFO"):
        await server._run_ci_failing_sweep()

    assert "DRY_RUN: would run ci_failing_sweep" in caplog.text


async def test_ci_failing_sweep_skips_github_client_when_repository_not_allowed(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("BRIDGE_CI_FAILING_REPOSITORY", "iterwheel/voyager")
    monkeypatch.delenv("BRIDGE_ALLOWED_REPOSITORIES", raising=False)
    monkeypatch.delenv("BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CI_FAILING", raising=False)
    monkeypatch.setattr(
        server,
        "_get_client",
        lambda: pytest.fail("disallowed CI-failing sweep must not initialize GitHub client"),
    )

    with caplog.at_level("WARNING"):
        await server._run_ci_failing_sweep()

    assert "not allow-listed for iterwheel-ci-failing" in caplog.text


async def test_ci_failing_sweep_runs_when_repository_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, str, str]] = []
    fake_client = object()

    async def fake_run_sweep(client: object, app_slug: str, repo: str) -> dict[str, object]:
        calls.append((client, app_slug, repo))
        return {
            "checked": 1,
            "flagged": [],
            "cleared": [],
            "already_failing": [],
            "skipped_no_checks": [1],
        }

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("BRIDGE_CI_FAILING_REPOSITORY", "iterwheel/voyager")
    monkeypatch.setenv("BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CI_FAILING", "iterwheel/voyager")
    monkeypatch.setattr(server, "_get_client", lambda: fake_client)
    monkeypatch.setattr(
        "voyager.bots.ci_failing.run_ci_failing_sweep",
        fake_run_sweep,
    )

    await server._run_ci_failing_sweep()

    assert calls == [(fake_client, "iterwheel-assembly", "iterwheel/voyager")]


async def test_ci_failing_sweep_uses_config_repository_allow_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, str, str]] = []
    fake_client = object()
    cfg = SimpleNamespace(
        bridge=SimpleNamespace(
            dry_run=False,
            allowed_repositories={"iterwheel-ci-failing": ("iterwheel/voyager",)},
        )
    )

    async def fake_run_sweep(client: object, app_slug: str, repo: str) -> dict[str, object]:
        calls.append((client, app_slug, repo))
        return {
            "checked": 1,
            "flagged": [],
            "cleared": [],
            "already_failing": [],
            "skipped_no_checks": [1],
        }

    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv("BRIDGE_ALLOWED_REPOSITORIES", raising=False)
    monkeypatch.delenv("BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CI_FAILING", raising=False)
    monkeypatch.setenv("BRIDGE_CI_FAILING_REPOSITORY", "iterwheel/voyager")
    monkeypatch.setattr(server, "_get_config", lambda: cfg)
    monkeypatch.setattr(server, "_get_client", lambda: fake_client)
    monkeypatch.setattr(
        "voyager.bots.ci_failing.run_ci_failing_sweep",
        fake_run_sweep,
    )

    await server._run_ci_failing_sweep()

    assert calls == [(fake_client, "iterwheel-assembly", "iterwheel/voyager")]
