"""FastAPI webhook server — Iterwheel GitHub Bridge."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from voyager.bots.clearance.investigator import ThreadInvestigator

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from voyager.bots.assembly import route_assembly_event
from voyager.bots.blueprint import route_blueprint_event
from voyager.bots.changelog import route_changelog_event
from voyager.bots.cleanup import route_pr_merge_cleanup
from voyager.bots.clearance import route_clearance_event
from voyager.bots.review_fix import route_review_fix_event
from voyager.bots.stack import route_stack_event
from voyager.build_info import BUILD_COMMIT, VERSION
from voyager.core.security import match_signature
from voyager.core.writeback import dry_run_enabled

_log = logging.getLogger(__name__)
_recent_writebacks: deque[dict[str, Any]] = deque(maxlen=100)
_client: Any = None
_store: Any = None
_SENTINEL: Any = object()
_config: Any = _SENTINEL
_default_profile_name: Any = _SENTINEL
_investigator: Any = _SENTINEL
_drift_alert_task: asyncio.Task[None] | None = None
_stale_pr_task: asyncio.Task[None] | None = None
_ci_failing_task: asyncio.Task[None] | None = None


def _get_config() -> Any:
    """Return memoized VoyagerConfig, or None if config is unavailable."""
    global _config
    if _config is _SENTINEL:
        try:
            from voyager.core.config import load_config

            _config = load_config()
        except Exception:
            return None
    return _config


def _get_client() -> Any:
    """Return a memoized GitHubAppClient, or None if config is unavailable."""
    global _client
    if _client is not None:
        return _client
    try:
        from voyager.core.github_app import GitHubAppClient

        cfg = _get_config()
        if cfg is None:
            return None
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

        cfg = _get_config()
        if cfg is None:
            return None
        _store = StateStore(cfg.work_dir)
        return _store
    except Exception:
        return None


def _get_default_profile_name() -> str | None:
    """Return ``cfg.default_profile`` (may be None), or None if config unavailable."""
    global _default_profile_name
    if _default_profile_name is _SENTINEL:
        try:
            cfg = _get_config()
            if cfg is None:
                return None
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


def _deployed_version_drift_enabled() -> bool:
    return _truthy(os.environ.get("BRIDGE_DRIFT_ALERT_ENABLED"))


def _deployed_version_drift_interval_seconds() -> int:
    raw = os.environ.get("BRIDGE_DRIFT_ALERT_INTERVAL_SECONDS", "3600")
    try:
        return max(1, int(raw))
    except ValueError:
        _log.warning("Invalid BRIDGE_DRIFT_ALERT_INTERVAL_SECONDS=%r; using 3600", raw)
        return 3600


async def _deployed_version_drift_token(repository: str) -> str | None:
    env_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if env_token:
        return env_token

    client = _get_client()
    if client is None:
        return None

    app_slug = os.environ.get("BRIDGE_DRIFT_ALERT_APP_SLUG", "iterwheel-assembly")
    try:
        return cast("str | None", await client.installation_token(app_slug, repository=repository))
    except Exception:
        _log.exception("Failed to mint token for deployed-version drift alert")
        return None


async def _run_deployed_version_drift_check() -> None:
    repository = os.environ.get("BRIDGE_DRIFT_ALERT_REPOSITORY", "iterwheel/voyager")
    bridge_url = os.environ.get("BRIDGE_DRIFT_ALERT_BRIDGE_URL", "https://gh.iterwheel.com")
    token = await _deployed_version_drift_token(repository)
    if not token:
        _log.warning("Skipping deployed-version drift check: no GitHub token available")
        return

    from voyager.core.drift_check import run_drift_alert_once

    result = await run_drift_alert_once(
        repository,
        bridge_url,
        github_token=token,
    )
    if result["drifted"] is True:
        _log.warning(
            "Deployed-version drift detected: deployed=%s latest=%s alert_created=%s",
            result["deployed_version"],
            result["latest_tag"],
            result["alert_created"],
        )
    elif result["drifted"] is False:
        _log.info(
            "Deployed version matches latest release: deployed=%s latest=%s",
            result["deployed_version"],
            result["latest_tag"],
        )
    else:
        _log.warning("Deployed-version drift check inconclusive: %s", result["summary"])


async def _deployed_version_drift_loop() -> None:
    interval = _deployed_version_drift_interval_seconds()
    while True:
        try:
            await _run_deployed_version_drift_check()
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("Scheduled deployed-version drift check failed")
        await asyncio.sleep(interval)


async def _start_deployed_version_drift_schedule() -> None:
    global _drift_alert_task
    if not _deployed_version_drift_enabled():
        return
    if _drift_alert_task is not None and not _drift_alert_task.done():
        return
    _drift_alert_task = asyncio.create_task(
        _deployed_version_drift_loop(),
        name="deployed-version-drift-alert",
    )


async def _stop_deployed_version_drift_schedule() -> None:
    global _drift_alert_task
    if _drift_alert_task is None:
        return
    _drift_alert_task.cancel()
    with suppress(asyncio.CancelledError):
        await _drift_alert_task
    _drift_alert_task = None


def _stale_pr_enabled() -> bool:
    return _truthy(os.environ.get("BRIDGE_STALE_PR_ENABLED"))


def _stale_pr_interval_seconds() -> int:
    raw = os.environ.get("BRIDGE_STALE_PR_INTERVAL_SECONDS", "86400")
    try:
        return max(60, int(raw))
    except ValueError:
        _log.warning("Invalid BRIDGE_STALE_PR_INTERVAL_SECONDS=%r; using 86400", raw)
        return 86400


def _stale_pr_days() -> int:
    raw = os.environ.get("BRIDGE_STALE_PR_DAYS", "7")
    try:
        return max(1, int(raw))
    except ValueError:
        _log.warning("Invalid BRIDGE_STALE_PR_DAYS=%r; using 7", raw)
        return 7


def _stale_pr_repository() -> str:
    return os.environ.get("BRIDGE_STALE_PR_REPOSITORY", "iterwheel/voyager")


def _stale_pr_app_slug() -> str:
    return os.environ.get("BRIDGE_STALE_PR_APP_SLUG", "iterwheel-assembly")


async def _run_stale_pr_triage() -> None:
    repo = _stale_pr_repository()
    app_slug = _stale_pr_app_slug()
    stale_days = _stale_pr_days()
    if dry_run_enabled():
        _log.info(
            "DRY_RUN: would run stale_pr_triage repo=%s app_slug=%s stale_days=%d",
            repo,
            app_slug,
            stale_days,
        )
        return

    client = _get_client()
    if client is None:
        _log.warning("Skipping stale-PR triage: no GitHub client available")
        return

    from voyager.bots.stale_pr import run_stale_pr_triage as run_triage

    try:
        summary = await run_triage(client, app_slug, repo, stale_days=stale_days)
        _log.info(
            "stale_pr_triage: repo=%s checked=%d labeled=%d already_labeled=%d commented=%d fresh=%d",
            repo,
            summary["checked"],
            len(summary["labeled"]),
            len(summary["already_labeled"]),
            len(summary["commented"]),
            len(summary["skipped_fresh"]),
        )
    except Exception:
        _log.exception("stale_pr_triage failed for %s", repo)


async def _stale_pr_loop() -> None:
    interval = _stale_pr_interval_seconds()
    while True:
        try:
            await _run_stale_pr_triage()
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("Scheduled stale-PR triage failed")
        await asyncio.sleep(interval)


async def _start_stale_pr_schedule() -> None:
    global _stale_pr_task
    if not _stale_pr_enabled():
        return
    if _stale_pr_task is not None and not _stale_pr_task.done():
        return
    _stale_pr_task = asyncio.create_task(
        _stale_pr_loop(),
        name="stale-pr-triage",
    )


async def _stop_stale_pr_schedule() -> None:
    global _stale_pr_task
    if _stale_pr_task is None:
        return
    _stale_pr_task.cancel()
    with suppress(asyncio.CancelledError):
        await _stale_pr_task
    _stale_pr_task = None


def _ci_failing_enabled() -> bool:
    return _truthy(os.environ.get("BRIDGE_CI_FAILING_ENABLED"))


def _ci_failing_interval_seconds() -> int:
    raw = os.environ.get("BRIDGE_CI_FAILING_INTERVAL_SECONDS", "86400")
    try:
        return max(60, int(raw))
    except ValueError:
        _log.warning("Invalid BRIDGE_CI_FAILING_INTERVAL_SECONDS=%r; using 86400", raw)
        return 86400


def _ci_failing_repository() -> str:
    return os.environ.get("BRIDGE_CI_FAILING_REPOSITORY", "iterwheel/voyager")


def _ci_failing_app_slug() -> str:
    return os.environ.get("BRIDGE_CI_FAILING_APP_SLUG", "iterwheel-assembly")


def _ci_failing_agent_slug() -> str:
    from voyager.bots.ci_failing import CI_FAILING_AGENT_SLUG

    return CI_FAILING_AGENT_SLUG


async def _run_ci_failing_sweep() -> None:
    target_repo = _ci_failing_repository()
    app_slug = _ci_failing_app_slug()
    agent_slug = _ci_failing_agent_slug()
    cfg = _get_config()
    if dry_run_enabled(cfg):
        _log.info(
            "DRY_RUN: would run ci_failing_sweep repo=%s app_slug=%s agent_slug=%s",
            target_repo,
            app_slug,
            agent_slug,
        )
        return

    if not _repository_allowed_for_agent(target_repo, agent_slug, cfg):
        _log.warning(
            "Skipping CI-failing sweep: repository %s is not allow-listed for %s",
            target_repo,
            agent_slug,
        )
        return

    client = _get_client()
    if client is None:
        _log.warning("Skipping CI-failing sweep: no GitHub client available")
        return

    from voyager.bots.ci_failing import run_ci_failing_sweep as run_sweep

    try:
        summary = await run_sweep(client, app_slug, target_repo)
        _log.info(
            "ci_failing_sweep: repo=%s checked=%d flagged=%d cleared=%d "
            "already_failing=%d skipped=%d",
            target_repo,
            summary["checked"],
            len(summary["flagged"]),
            len(summary["cleared"]),
            len(summary["already_failing"]),
            len(summary["skipped_no_checks"]),
        )
    except Exception:
        _log.exception("ci_failing_sweep failed for %s", target_repo)


async def _ci_failing_loop() -> None:
    interval = _ci_failing_interval_seconds()
    while True:
        try:
            await _run_ci_failing_sweep()
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("Scheduled CI-failing sweep failed")
        await asyncio.sleep(interval)


async def _start_ci_failing_schedule() -> None:
    global _ci_failing_task
    if not _ci_failing_enabled():
        return
    if _ci_failing_task is not None and not _ci_failing_task.done():
        return
    _ci_failing_task = asyncio.create_task(
        _ci_failing_loop(),
        name="ci-failing-sweep",
    )


async def _stop_ci_failing_schedule() -> None:
    global _ci_failing_task
    if _ci_failing_task is None:
        return
    _ci_failing_task.cancel()
    with suppress(asyncio.CancelledError):
        await _ci_failing_task
    _ci_failing_task = None


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await _start_deployed_version_drift_schedule()
    await _start_ci_failing_schedule()
    await _start_stale_pr_schedule()
    try:
        yield
    finally:
        await _stop_ci_failing_schedule()
        await _stop_stale_pr_schedule()
        await _stop_deployed_version_drift_schedule()


app = FastAPI(title="Iterwheel GitHub Bridge", lifespan=_lifespan)


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


def _allowed_repositories_env_key(agent_slug: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", agent_slug).strip("_").upper()
    return f"BRIDGE_ALLOWED_REPOSITORIES_{normalized}"


def _parse_allowed_repositories(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip().lower() for item in re.split(r"[\s,]+", value) if item.strip()}


def _repository_pattern_matches(pattern: str, repository: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith("/*"):
        owner = pattern[:-2]
        repo_owner, separator, repo_name = repository.partition("/")
        return repo_owner == owner and separator == "/" and bool(repo_name) and "/" not in repo_name
    return pattern == repository


def _repository_allowed_for_agent(
    repository: str | None,
    agent_slug: str,
    cfg: Any | None = None,
) -> bool:
    """Return whether a route may run for this repository and agent.

    Production defaults to deny when no allow-list is configured. Dry-run keeps
    the historical permissive behavior so local routing tests and exploratory
    dry-runs do not need allow-list env setup.
    """
    specific_key = _allowed_repositories_env_key(agent_slug)
    if specific_key in os.environ:
        allowed = _parse_allowed_repositories(os.environ.get(specific_key))
    elif "BRIDGE_ALLOWED_REPOSITORIES" in os.environ:
        allowed = _parse_allowed_repositories(os.environ.get("BRIDGE_ALLOWED_REPOSITORIES"))
    else:
        bridge = getattr(cfg, "bridge", None)
        allowed = set(
            (getattr(bridge, "allowed_repositories", {}) or {}).get(agent_slug.lower(), ())
        )
    if not allowed:
        return dry_run_enabled(cfg)
    if not repository:
        return False
    normalized_repo = repository.strip().lower()
    return any(_repository_pattern_matches(pattern, normalized_repo) for pattern in allowed)


def _filter_routes_by_repository(
    routes: list[dict[str, Any]],
    repository: str | None,
    cfg: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    allowed: list[dict[str, Any]] = []
    denied: list[dict[str, Any]] = []
    for route in routes:
        agent_slug = str(route.get("agent") or "")
        if _repository_allowed_for_agent(repository, agent_slug, cfg):
            allowed.append(route)
        else:
            denied.append(route)
    return allowed, denied


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
    cfg = _get_config()
    # Codex GH-bot PR #15 P1: include pr_number + ts at the top level of every
    # writeback record so consumers (the e2e harness in particular) can match
    # records to the PR they created without spelunking through nested route
    # shapes that vary between apply / stale-skip / error paths.
    pr_number = _extract_pr_number_from_payload(payload)
    for route in routes:
        route_for_writeback = {**route, "delivery_id": delivery_id}
        try:
            result = await dispatch_route_writeback(
                client,
                route_for_writeback,
                repository=repository,
                store=store,
                default_profile_name=default_profile_name,
                investigator=investigator,
                cfg=cfg,
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
    cfg = _get_config()
    return {
        "ok": True,
        "service": "iterwheel-github-bridge",
        "time": _utc_now(),
        "dry_run": dry_run_enabled(cfg),
        "version": VERSION,
        "build_commit": BUILD_COMMIT,
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

    repository: str | None = (payload.get("repository") or {}).get("full_name")
    cfg = _get_config()
    candidate_routes = [
        *route_blueprint_event(x_github_event, payload),
        *route_stack_event(x_github_event, payload),
        *route_clearance_event(x_github_event, payload),
        *route_changelog_event(x_github_event, payload),
        *route_assembly_event(x_github_event, payload, cfg=cfg),
        *route_review_fix_event(x_github_event, payload, cfg=cfg),
        *route_pr_merge_cleanup(x_github_event, payload),
    ]
    routes, denied_routes = _filter_routes_by_repository(candidate_routes, repository, cfg)
    if denied_routes:
        _log.warning(
            "repository_allowlist_denied: repo=%r denied_routes=%s",
            repository,
            [route.get("agent") for route in denied_routes],
        )

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
        "dry_run": dry_run_enabled(cfg),
        "app": matched_slug,
        "event": x_github_event,
        "delivery_id": x_github_delivery,
        "routes": _route_summaries(routes),
        "writebacks": {"status": "queued", "scheduled": len(routes)},
        "filtered": {
            "status": "repository_not_allowed" if denied_routes else "none",
            "count": len(denied_routes),
            "routes": _route_summaries(denied_routes),
        },
    }
