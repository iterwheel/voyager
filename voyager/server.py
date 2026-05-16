"""FastAPI webhook server — Iterwheel GitHub Bridge."""

from __future__ import annotations

import json
import logging
import os
from collections import deque
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from voyager.bots.clearance.investigator import ThreadInvestigator

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from voyager.bots.blueprint import route_blueprint_event
from voyager.bots.clearance import route_clearance_event
from voyager.bots.stack import route_stack_event
from voyager.core.security import match_signature
from voyager.core.writeback import dry_run_enabled

app = FastAPI(title="Iterwheel GitHub Bridge")

_log = logging.getLogger(__name__)
_recent_writebacks: deque[dict[str, Any]] = deque(maxlen=100)
_client: Any = None
_store: Any = None
_SENTINEL: Any = object()
_default_profile_name: Any = _SENTINEL
_investigator: Any = _SENTINEL


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


def _get_store() -> Any:
    """Return a memoized StateStore, or None if config is unavailable.

    Degrades gracefully to legacy PR-body-only enrichment when config is
    missing — the pipeline is skipped but the writeback still proceeds.
    """
    global _store
    if _store is not None:
        return _store
    try:
        from voyager.bots.clearance.state import StateStore
        from voyager.core.config import load_config

        cfg = load_config()
        _store = StateStore(cfg.work_dir)
        return _store
    except Exception:
        return None


def _get_default_profile_name() -> str | None:
    """Return ``cfg.default_profile`` (may be None), or None if config unavailable."""
    global _default_profile_name
    if _default_profile_name is _SENTINEL:
        try:
            from voyager.core.config import load_config

            cfg = load_config()
            _default_profile_name = cfg.default_profile
        except Exception:
            _default_profile_name = None
    return cast("str | None", _default_profile_name)


def _get_investigator() -> ThreadInvestigator | None:
    """Return a memoized ``DeepSeekInvestigator`` built from the default profile, or None.

    Returns None when:
    - ``cfg.default_profile`` is unset
    - ``cfg.default_profile`` doesn't resolve to a known profile (already
      validated at config load, but defensive)
    - No api_key resolvable. Resolution distinguishes "env unset" from
      "env explicitly empty": if ``VOYAGER_DEEPSEEK_API_KEY`` is set in
      ``os.environ`` (even to ``""``), that value wins — an empty value
      means the operator intentionally cleared the key, and the TOML
      fallback is NOT consulted. Only when the env var is truly absent
      does ``cfg.deepseek_api_key`` apply.
    - any exception during construction

    Returning None keeps the bridge running deterministically — pipeline's
    AUGMENT rule (Wave 7B-3 D1=B) means State B + code_changed=True still
    resolves via judge() without an investigator; State B + code_changed=False
    falls back to the pre-investigator OPEN verdict. Same degradation
    semantics as ``_get_store`` returning None: feature off, no crash.
    """
    global _investigator
    if _investigator is _SENTINEL:
        try:
            from voyager.bots.clearance.investigator import build_investigator_from_profile
            from voyager.core.config import load_config

            cfg = load_config()
            name = cfg.default_profile
            # 12-factor: env wins. Distinguish unset (None → fall through to
            # TOML) from explicit-empty ("" → operator intent to disable;
            # do NOT consult TOML). os.environ.get returns None for unset
            # and "" for `export VAR=""`.
            env_value = os.environ.get("VOYAGER_DEEPSEEK_API_KEY")
            api_key = env_value if env_value is not None else (cfg.deepseek_api_key or "")
            if not name or name not in cfg.profiles or not api_key:
                _investigator = None
            else:
                _investigator = build_investigator_from_profile(
                    cfg.profiles[name],
                    api_key=api_key,
                )
        except Exception:
            _investigator = None
    return cast("ThreadInvestigator | None", _investigator)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _webhook_debug_context(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Small webhook identity fields for e2e provenance filtering."""
    sender = payload.get("sender") or {}
    review = payload.get("review") or {}
    comment = payload.get("comment") or {}
    context: dict[str, Any] = {
        "action": payload.get("action"),
        "sender_login": sender.get("login"),
    }
    if event == "pull_request_review":
        context.update(
            {
                "review_id": review.get("id"),
                "review_state": review.get("state"),
                "review_user_login": (review.get("user") or {}).get("login"),
            }
        )
    if event == "pull_request_review_comment":
        context.update(
            {
                "review_comment_id": comment.get("id"),
                "review_comment_in_reply_to_id": comment.get("in_reply_to_id"),
                "review_id": comment.get("pull_request_review_id"),
                "review_comment_user_login": (comment.get("user") or {}).get("login"),
            }
        )
    return {key: value for key, value in context.items() if value is not None}


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

    store = _get_store()
    repository: str | None = (payload.get("repository") or {}).get("full_name")
    default_profile_name = _get_default_profile_name()
    investigator = _get_investigator()
    # Codex GH-bot PR #15 P1: include pr_number + ts at the top level of every
    # writeback record so consumers (the e2e harness in particular) can match
    # records to the PR they created without spelunking through nested route
    # shapes that vary between apply / stale-skip / error paths.
    pr_number = _extract_pr_number_from_payload(payload)
    for route in routes:
        try:
            result = await dispatch_route_writeback(
                client,
                route,
                repository=repository,
                store=store,
                default_profile_name=default_profile_name,
                investigator=investigator,
            )
            _recent_writebacks.append(
                {
                    "delivery_id": delivery_id,
                    "event": event,
                    "repository": repository,
                    "pr_number": pr_number,
                    "ts": _utc_now(),
                    "webhook": _webhook_debug_context(event, payload),
                    **result,
                }
            )
        except Exception:
            _log.exception("writeback failed for route %r", route.get("agent"))


def _extract_pr_number_from_payload(payload: dict[str, Any]) -> int | None:
    """Best-effort PR number lookup across GitHub webhook payload shapes.

    GitHub puts the PR number in different keys depending on the event type:
      - pull_request / pull_request_review / pull_request_review_comment:
        payload["pull_request"]["number"]
      - issue_comment on a PR: payload["issue"]["number"] (PRs are issues)
      - check_suite: payload["check_suite"]["pull_requests"][0]["number"]
    """
    pr = payload.get("pull_request") or {}
    pr_num = pr.get("number")
    if isinstance(pr_num, int):
        return pr_num
    issue = payload.get("issue") or {}
    issue_num = issue.get("number")
    if isinstance(issue_num, int) and issue.get("pull_request"):
        return issue_num
    check_suite = payload.get("check_suite") or {}
    prs = check_suite.get("pull_requests") or []
    if prs:
        first_num = prs[0].get("number")
        if isinstance(first_num, int):
            return first_num
    return None


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


def _truthy(s: str | None) -> bool:
    return (s or "").strip().lower() in {"1", "true", "yes", "y", "on"}


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


@app.get("/e2e/recent_writebacks", include_in_schema=False)
async def e2e_recent_writebacks(
    request: Request,
    x_voyager_e2e_token: str | None = Header(default=None),
) -> JSONResponse:
    """Debug endpoint for the e2e test harness — returns the in-memory
    writeback deque. Layered defense-in-depth (trinity round-1 P1):

      1. ``VOYAGER_E2E_DEBUG=1`` env required (404 otherwise — doesn't leak
         the endpoint's existence)
      2. Request client must be loopback (127.0.0.1 / ::1 / localhost). A
         tunnel or LAN client gets 404. Operators wanting non-loopback access
         must opt in with VOYAGER_E2E_ALLOW_NON_LOOPBACK=1.
      3. If ``VOYAGER_E2E_TOKEN`` env is set, the request must carry the
         matching ``X-Voyager-E2E-Token`` header. Constant-time compare via
         secrets.compare_digest to avoid timing oracle.
      4. ``Cache-Control: no-store, max-age=0`` on the response so the
         writeback record isn't cached by intermediaries.

    Pair with ``scripts/e2e/run_matrix.py``.
    """
    if not _truthy(os.environ.get("VOYAGER_E2E_DEBUG")):
        raise HTTPException(status_code=404, detail="Not found")

    if not _truthy(os.environ.get("VOYAGER_E2E_ALLOW_NON_LOOPBACK")):
        client_host = (request.client.host if request.client else "") or ""
        if client_host not in _LOOPBACK_HOSTS:
            _log.warning(
                "e2e endpoint rejected non-loopback client %r (set "
                "VOYAGER_E2E_ALLOW_NON_LOOPBACK=1 to override)",
                client_host,
            )
            raise HTTPException(status_code=404, detail="Not found")

    expected_token = os.environ.get("VOYAGER_E2E_TOKEN")
    if expected_token:
        import secrets as _secrets

        if not x_voyager_e2e_token or not _secrets.compare_digest(
            expected_token, x_voyager_e2e_token
        ):
            raise HTTPException(status_code=401, detail="missing or invalid e2e token")

    return JSONResponse(
        content={
            "count": len(_recent_writebacks),
            "writebacks": list(_recent_writebacks),
        },
        headers={"Cache-Control": "no-store, max-age=0"},
    )


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
