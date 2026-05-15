"""Real-time E2E test dashboard — FastAPI + SSE.

Receives scenario-status updates from ``run_matrix.py`` over HTTP and
broadcasts them to a browser tab via Server-Sent Events. Single in-memory
state dict, keyed by scenario id, so a late-joining browser can still see
the full picture.

Run:
    cd /Users/frank/Projects/voyager
    uv run uvicorn scripts.e2e.dashboard:app --host 127.0.0.1 --port 9099

Open http://127.0.0.1:9099 in a browser.

Designed for sandbox testing only; no auth, no persistence — restarts wipe
state. Tail-of-file streaming would be an obvious extension if persistence
across crashes matters later.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="Voyager E2E Dashboard")

_TEMPLATE_DIR = Path(__file__).parent / "templates"

# In-memory state: scenario_id -> latest update.
# OrderedDict so the UI sees them in declaration order.
_state: OrderedDict[str, dict[str, Any]] = OrderedDict()

# Each connected browser tab gets a queue. Updates fan-out to all queues.
_subscribers: list[asyncio.Queue[str]] = []


class ScenarioUpdate(BaseModel):
    """Status update for a single scenario (POSTed by run_matrix.py)."""

    id: str
    category: str
    description: str | None = None
    status: str  # "queued" | "running" | "passed" | "failed" | "skipped" | "error"
    pr_number: int | None = None
    pr_url: str | None = None
    actual: dict[str, Any] | None = None
    expected: dict[str, Any] | None = None
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None


async def _broadcast(payload: dict[str, Any]) -> None:
    """Push the payload to every connected SSE subscriber."""
    import contextlib

    line = f"data: {json.dumps(payload)}\n\n"
    for q in list(_subscribers):
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(line)


@app.post("/scenario")
async def update_scenario(update: ScenarioUpdate) -> dict[str, Any]:
    """Runner POSTs status updates here as each scenario progresses."""
    payload = update.model_dump()
    payload["received_at"] = time.time()
    _state[update.id] = payload
    await _broadcast(payload)
    return {"ok": True, "id": update.id, "status": update.status}


@app.post("/reset")
async def reset() -> dict[str, Any]:
    """Wipe all in-memory state. Runner calls this at start-of-run."""
    _state.clear()
    await _broadcast({"_reset": True})
    return {"ok": True}


@app.get("/state")
async def get_state() -> dict[str, Any]:
    """Snapshot of all scenarios — useful for debugging / late page-load."""
    return {"scenarios": list(_state.values()), "count": len(_state)}


@app.get("/events")
async def events() -> StreamingResponse:
    """SSE stream: replays current state, then live-updates."""
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
    _subscribers.append(queue)

    async def gen():
        # Replay current state so the new subscriber sees the full picture.
        for s in _state.values():
            yield f"data: {json.dumps(s)}\n\n"
        try:
            while True:
                line = await queue.get()
                yield line
        finally:
            if queue in _subscribers:
                _subscribers.remove(queue)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    """Serve the dashboard UI."""
    return (_TEMPLATE_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "scenarios_known": len(_state),
        "subscribers": len(_subscribers),
        "uptime_s": time.time() - _start_time,
    }


_start_time = time.time()
