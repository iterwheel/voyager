from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx


class _StaleGuardFailClient:
    async def pull_request(self, app_slug: str, repo: str, pull_number: int) -> dict[str, Any]:
        raise httpx.HTTPError("https://api.github.com/pulls?token=ghp_SECRET")


def _clearance_route() -> dict[str, Any]:
    return {
        "agent": "iterwheel-clearance",
        "kind": "clearance_readiness",
        "validation": {"pr_number": 77, "issue_number": 77},
        "writeback": {"dynamic": "clearance_readiness"},
    }


async def _empty_enriched_route(
    client, route, *, repository: str, automation=None
) -> dict[str, Any]:
    return {
        "agent": route["agent"],
        "kind": route["kind"],
        "validation": {**route["validation"], "issue_number": route["validation"]["pr_number"]},
        "writeback": {},
    }


def test_dispatch_compute_fallback_reason_does_not_leak_exception_message(
    monkeypatch,
    caplog,
) -> None:
    import voyager.bots.clearance as clearance_pkg
    import voyager.bots.clearance.pipeline as pipeline_mod
    from voyager.core.writeback import dispatch_route_writeback

    async def fail_compute(*args, **kwargs):
        raise RuntimeError("https://api.github.com/graphql?token=ghp_SECRET")

    monkeypatch.setattr(pipeline_mod, "compute_clearance_automation", fail_compute)
    monkeypatch.setattr(clearance_pkg, "enrich_clearance_route", _empty_enriched_route)
    monkeypatch.setenv("DRY_RUN", "true")
    caplog.set_level(logging.WARNING, logger="voyager.core.writeback")

    result = asyncio.run(
        dispatch_route_writeback(
            object(),
            _clearance_route(),
            repository="iterwheel/voyager",
            store=object(),
        )
    )

    text = str(result) + caplog.text
    assert "RuntimeError" in result["automation"]["reason"]
    assert "ghp_" not in text
    assert "token=" not in text
    assert "https://" not in text


def test_dispatch_wires_default_known_limitation_store(monkeypatch) -> None:
    import voyager.bots.clearance as clearance_pkg
    import voyager.bots.clearance.known_limitations as known_limitations_mod
    import voyager.bots.clearance.pipeline as pipeline_mod
    from voyager.core.writeback import dispatch_route_writeback

    captured: dict[str, Any] = {}

    class FakeKnownLimitationStore:
        pass

    async def capture_compute(*args, **kwargs):
        _ = args
        captured.update(kwargs)
        return {
            "enabled": True,
            "status": "ready",
            "reason": "ready",
            "sync_actions": [],
            "sync_actions_count": 0,
        }

    monkeypatch.setattr(
        known_limitations_mod,
        "KnownLimitationStore",
        FakeKnownLimitationStore,
    )
    monkeypatch.setattr(pipeline_mod, "compute_clearance_automation", capture_compute)
    monkeypatch.setattr(clearance_pkg, "enrich_clearance_route", _empty_enriched_route)
    monkeypatch.setenv("DRY_RUN", "true")

    result = asyncio.run(
        dispatch_route_writeback(
            object(),
            _clearance_route(),
            repository="iterwheel/voyager",
            store=object(),
        )
    )

    assert result["automation"]["status"] == "ready"
    assert isinstance(captured["known_limitation_store"], FakeKnownLimitationStore)


def test_dispatch_enrichment_fallback_reason_does_not_leak_exception_message(
    monkeypatch,
    caplog,
) -> None:
    import voyager.bots.clearance as clearance_pkg
    from voyager.core.writeback import dispatch_route_writeback

    async def fail_enrich(*args, **kwargs):
        raise RuntimeError("Authorization: Bearer ghp_SECRET")

    monkeypatch.setattr(clearance_pkg, "enrich_clearance_route", fail_enrich)
    monkeypatch.setenv("DRY_RUN", "true")
    caplog.set_level(logging.WARNING, logger="voyager.core.writeback")

    result = asyncio.run(
        dispatch_route_writeback(object(), _clearance_route(), repository="iterwheel/voyager")
    )

    text = str(result) + caplog.text
    assert result["reason"] == "clearance enrichment failed: RuntimeError"
    assert "ghp_" not in text
    assert "Authorization" not in text
    assert "Bearer" not in text


def test_dispatch_stale_guard_fallback_log_does_not_leak_exception_message(
    monkeypatch,
    caplog,
) -> None:
    import voyager.bots.clearance as clearance_pkg
    import voyager.bots.clearance.pipeline as pipeline_mod
    from voyager.core.writeback import dispatch_route_writeback

    async def compute_with_head(*args, **kwargs):
        return {
            "enabled": True,
            "status": "ready",
            "reason": "ready",
            "sync_actions": [],
            "sync_actions_count": 0,
            "head_sha": "old-sha",
        }

    monkeypatch.setattr(pipeline_mod, "compute_clearance_automation", compute_with_head)
    monkeypatch.setattr(clearance_pkg, "enrich_clearance_route", _empty_enriched_route)
    monkeypatch.setenv("DRY_RUN", "false")
    caplog.set_level(logging.INFO, logger="voyager.core.writeback")

    result = asyncio.run(
        dispatch_route_writeback(
            _StaleGuardFailClient(),
            _clearance_route(),
            repository="iterwheel/voyager",
            store=object(),
        )
    )

    text = str(result) + caplog.text
    assert result["applied"] is True
    assert "HTTPError" in text
    assert "ghp_" not in text
    assert "token=" not in text
    assert "https://" not in text
