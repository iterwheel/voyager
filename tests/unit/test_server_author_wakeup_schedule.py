from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import voyager.server as server
from voyager.bots.clearance.constants import CLEARANCE_AGENT_SLUG
from voyager.core.config import AuthorWakeupConfig, VoyagerConfig


def _cfg(tmp_path: Path, author_wakeup: AuthorWakeupConfig) -> VoyagerConfig:
    return VoyagerConfig(
        apps={},
        work_dir=tmp_path / "state",
        profiles={},
        default_profile=None,
        author_wakeup=author_wakeup,
    )


@pytest.fixture(autouse=True)
async def clean_author_wakeup_task(monkeypatch: pytest.MonkeyPatch):
    await server._stop_author_wakeup_schedule()
    monkeypatch.setattr(server, "_author_wakeup_task", None)
    monkeypatch.setattr(server, "_author_wakeup_event", None)
    monkeypatch.setattr(server, "_author_wakeup_reconciler", None)
    monkeypatch.setattr(server, "_author_wakeup_door", None)
    yield
    await server._stop_author_wakeup_schedule()


async def test_author_wakeup_schedule_stays_off_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(server, "_get_config", lambda: _cfg(tmp_path, AuthorWakeupConfig()))
    monkeypatch.setattr(
        server,
        "_get_client",
        lambda: pytest.fail("disabled schedule must not create a GitHub client"),
    )

    await server._start_author_wakeup_schedule()

    assert server._author_wakeup_task is None
    assert server._author_wakeup_reconciler is None


async def test_author_wakeup_schedule_starts_and_closes_door(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = "iterwheel/voyager-sandbox"
    config = AuthorWakeupConfig(
        enabled=True,
        allowed_repositories=(repository,),
        audit_dir=tmp_path / "wakeup",
    )
    fake_door = SimpleNamespace(close=AsyncMock())

    async def fake_loop(_config: AuthorWakeupConfig) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(server, "_get_config", lambda: _cfg(tmp_path, config))
    monkeypatch.setattr(server, "_get_client", lambda: object())
    monkeypatch.setattr(server, "_get_store", lambda: object())
    monkeypatch.setattr(server, "PfcDoorClient", lambda *args, **kwargs: fake_door)
    monkeypatch.setattr(server, "_author_wakeup_loop", fake_loop)

    await server._start_author_wakeup_schedule()

    assert server._author_wakeup_task is not None
    assert server._author_wakeup_reconciler is not None
    assert (tmp_path / "wakeup" / "author-wakeup.db").exists()

    await server._stop_author_wakeup_schedule()

    fake_door.close.assert_awaited_once()
    assert server._author_wakeup_task is None
    assert server._author_wakeup_reconciler is None


async def test_author_wakeup_schedule_fails_closed_on_invalid_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(server, "_get_config", lambda: _cfg(tmp_path, AuthorWakeupConfig()))
    monkeypatch.setenv("CLEARANCE_AUTHOR_WAKEUP_ENABLED", "not-a-boolean")

    with caplog.at_level("ERROR"):
        await server._start_author_wakeup_schedule()

    assert server._author_wakeup_task is None
    assert "author-wakeup config" in caplog.text


async def test_author_wakeup_loop_scans_immediately_and_on_nudge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reconciler = SimpleNamespace(tick=AsyncMock())
    event = asyncio.Event()
    monkeypatch.setattr(server, "_author_wakeup_reconciler", reconciler)
    monkeypatch.setattr(server, "_author_wakeup_event", event)
    config = AuthorWakeupConfig(
        enabled=True,
        reconcile_interval_seconds=3600,
        receipt_poll_interval_seconds=1,
    )
    task = asyncio.create_task(server._author_wakeup_loop(config))
    try:
        for _ in range(20):
            if reconciler.tick.await_count:
                break
            await asyncio.sleep(0)
        server._nudge_author_wakeup()
        for _ in range(20):
            if reconciler.tick.await_count >= 2:
                break
            await asyncio.sleep(0)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert [call.kwargs["scan"] for call in reconciler.tick.await_args_list[:2]] == [
        True,
        True,
    ]


async def test_clearance_writeback_completion_nudges_author_wakeup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = asyncio.Event()
    monkeypatch.setattr(server, "_author_wakeup_event", event)
    monkeypatch.setattr(server, "_get_client", lambda: object())
    monkeypatch.setattr(server, "_get_store", lambda: object())
    monkeypatch.setattr(server, "_get_config", lambda: None)
    monkeypatch.setattr(server, "_get_default_profile_name", lambda: None)
    monkeypatch.setattr(server, "_get_investigator", lambda: None)
    monkeypatch.setattr(
        "voyager.core.writeback.dispatch_route_writeback",
        AsyncMock(return_value={"status": "ok"}),
    )

    await server._process_route_writebacks(
        matched_slug="iterwheel-clearance",
        event="pull_request_review_comment",
        delivery_id="delivery-1",
        payload={
            "repository": {"full_name": "iterwheel/voyager-sandbox"},
            "pull_request": {"number": 42},
        },
        routes=[
            {
                "agent": CLEARANCE_AGENT_SLUG,
                "kind": "clearance",
                "validation": {"status": "ready", "conclusion": "neutral"},
            }
        ],
    )

    assert event.is_set()


async def test_healthz_reports_safe_author_wakeup_task_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = _cfg(
        tmp_path,
        AuthorWakeupConfig(
            enabled=True,
            allowed_repositories=("iterwheel/voyager-sandbox",),
        ),
    )
    task = asyncio.create_task(asyncio.Event().wait())
    monkeypatch.setattr(server, "_get_config", lambda: cfg)
    monkeypatch.setattr(server, "_author_wakeup_task", task)
    try:
        result = await server.healthz()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert result["author_wakeup"] == {"enabled": True, "running": True}
