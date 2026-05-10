"""FastAPI webhook server — Iterwheel GitHub Bridge."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from voyager.bots.blueprint import route_blueprint_event
from voyager.bots.clearance import route_clearance_event
from voyager.bots.stack import route_stack_event
from voyager.core.security import match_signature

app = FastAPI(title="Iterwheel GitHub Bridge")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def dry_run_enabled() -> bool:
    return os.environ.get("DRY_RUN", "").lower() in {"1", "true", "yes"}


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
    matched_slug: str,
    event: str,
    delivery_id: str,
    payload: dict[str, Any],
    routes: list[dict[str, Any]],
) -> None:
    """Background task: placeholder for writeback processing (no real I/O in tests)."""


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
        "writebacks": "deferred" if routes else [],
    }
