"""FastAPI webhook server — Iterwheel GitHub Bridge."""

from __future__ import annotations

import json
import logging
import os
from collections import deque
from datetime import UTC, datetime
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from voyager.bots.blueprint import route_blueprint_event
from voyager.bots.clearance import route_clearance_event
from voyager.bots.stack import route_stack_event
from voyager.core.security import match_signature
from voyager.core.writeback import dry_run_enabled

app = FastAPI(title="Iterwheel GitHub Bridge")

_log = logging.getLogger(__name__)
_recent_writebacks: deque[dict[str, Any]] = deque(maxlen=100)
_client: Any = None


def _get_client() -> Any:
    """Return a memoized GitHubAppClient, or None if config is unavailable."""
    global _client
    if _client is not None:
        return _client
    try:
        from voyager.core.config import load_config
        from voyager.core.github_app import GitHubAppClient

        cfg = load_config()
        _client = GitHubAppClient(cfg.apps)
        return _client
    except Exception:
        return None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def configured_webhook_secrets() -> dict[str, str]:
    """Build the slug→secret map from environment variables at request time.

    App-specific slugs (GITHUB_WEBHOOK_SECRET_*) are checked before the
    fallback repository-webhook secret so named slugs win on ambiguous matches.
    """
    secrets: dict[str, str] = {}
    for key, value in os.environ.items():
        if key.startswith("GITHUB_WEBHOOK_SECRET_") and value:
            slug = key[len("GITHUB_WEBHOOK_SECRET_") :].lower().replace("_", "-")
            secrets[slug] = value
    repository_secret = os.environ.get("GITHUB_REPOSITORY_WEBHOOK_SECRET", "")
    if repository_secret:
        secrets["repository-webhook"] = repository_secret
    return secrets


def _route_summaries(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "agent": route["agent"],
            "kind": route["kind"],
            "status": route["validation"]["status"],
            "conclusion": route["validation"]["conclusion"],
        }
        for route in routes
    ]


async def _process_route_writebacks(
    *,
    matched_slug: str,  # noqa: ARG001 — reserved for future per-slug client selection
    event: str,
    delivery_id: str,
    payload: dict[str, Any],
    routes: list[dict[str, Any]],
) -> None:
    """Background task: dispatch writeback actions for each matched route.

    Clearance routes carry a dynamic-enrichment marker — dispatch_route_writeback
    handles them by calling enrich_clearance_route first; Blueprint/Stack routes
    already have concrete writeback shapes and dispatch passes through.
    """
    from voyager.core.writeback import dispatch_route_writeback

    client = _get_client()
    if client is None:
        _log.warning(
            "writeback: no client available (config missing?), skipping %d routes", len(routes)
        )
        return

    repository: str | None = (payload.get("repository") or {}).get("full_name")
    for route in routes:
        try:
            result = await dispatch_route_writeback(client, route, repository=repository)
            _recent_writebacks.append({"delivery_id": delivery_id, "event": event, **result})
        except Exception:
            _log.exception("writeback failed for route %r", route.get("agent"))


@app.get("/")
async def root() -> dict[str, Any]:
    return {"ok": True, "service": "iterwheel-github-bridge", "health": "/healthz"}


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "iterwheel-github-bridge",
        "time": _utc_now(),
        "dry_run": dry_run_enabled(),
    }


@app.post("/github/webhook")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str = Header(default=""),
    x_github_delivery: str = Header(default=""),
    x_hub_signature_256: str | None = Header(default=None),
) -> dict[str, Any]:
    raw_body = await request.body()
    secrets = configured_webhook_secrets()
    if not secrets:
        raise HTTPException(status_code=503, detail="No GitHub webhook secrets are configured")

    matched_slug = match_signature(raw_body, x_hub_signature_256, secrets)
    if not matched_slug:
        raise HTTPException(status_code=401, detail="Invalid GitHub webhook signature")

    if not x_github_delivery:
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Delivery")

    if not x_github_event:
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Event")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    routes = [
        *route_blueprint_event(x_github_event, payload),
        *route_stack_event(x_github_event, payload),
        *route_clearance_event(x_github_event, payload),
    ]

    if routes:
        background_tasks.add_task(
            _process_route_writebacks,
            matched_slug=matched_slug,
            event=x_github_event,
            delivery_id=x_github_delivery,
            payload=payload,
            routes=routes,
        )

    return {
        "ok": True,
        "queued": bool(routes),
        "dry_run": dry_run_enabled(),
        "app": matched_slug,
        "event": x_github_event,
        "delivery_id": x_github_delivery,
        "routes": _route_summaries(routes),
        "writebacks": {"status": "queued", "scheduled": len(routes)},
    }
