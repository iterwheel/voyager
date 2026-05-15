"""E2E test matrix runner — PR factory for voyager sandbox testing.

Reads scenarios from ``matrix.yaml`` and exercises each one end-to-end
against ``iterwheel/voyager-sandbox``:

  1. Create a branch + file via gh CLI (PR-author identity = ryosaeba1985)
  2. Open a PR
  3. POST review threads using the test GitHub App's installation token
     (the App's bot login is listed in ``VOYAGER_TEST_BOT_LOGINS`` so
     voyager treats it as Codex-equivalent)
  4. Wait for voyager to receive the webhook + process
  5. Read voyager's decision from its log file (or /healthz / writeback log)
  6. Compare against the scenario's ``expected`` block
  7. POST status updates to the dashboard server throughout

Run:
    # 1. Start dashboard:
    uv run uvicorn scripts.e2e.dashboard:app --port 9099 &

    # 2. Run matrix (DRY_RUN=true; voyager won't write labels):
    DRY_RUN=true uv run python scripts/e2e/run_matrix.py \\
        --matrix scripts/e2e/matrix.yaml \\
        --dashboard http://127.0.0.1:9099 \\
        --filter A1 --filter B1     # subset by scenario id prefix

This file is the **scaffold**. The five Phase-A scenarios in matrix.yaml
are wired through enough plumbing to demo end-to-end; full 30+ matrix and
robust cleanup belong in a follow-up commit once the scaffold is validated
against real sandbox runs.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TestBotApp:
    """Test GitHub App credentials for posting Codex-like reviews."""

    app_id: str
    installation_id: str
    private_key_path: Path
    bot_login: str  # e.g. "voyager-e2e-bot[bot]" — must appear in VOYAGER_TEST_BOT_LOGINS


@dataclass(frozen=True)
class RunnerConfig:
    matrix_path: Path
    dashboard_url: str
    sandbox_repo: str
    base_branch: str
    test_bot: TestBotApp
    scenario_filter: list[str]  # only run scenarios whose id startswith any of these
    dry_run_sandbox: bool  # if True, log actions instead of hitting GitHub


# ---------------------------------------------------------------------------
# Dashboard client
# ---------------------------------------------------------------------------


class Dashboard:
    """Thin POST client for scripts/e2e/dashboard.py."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=5.0)

    def reset(self) -> None:
        try:
            self._client.post(f"{self.base_url}/reset")
        except Exception as exc:
            print(f"[dashboard] reset failed (continuing): {exc}", file=sys.stderr)

    def update(self, payload: dict[str, Any]) -> None:
        try:
            self._client.post(f"{self.base_url}/scenario", json=payload)
        except Exception as exc:
            print(f"[dashboard] update failed (continuing): {exc}", file=sys.stderr)

    def close(self) -> None:
        self._client.close()


# ---------------------------------------------------------------------------
# GitHub interactions
# ---------------------------------------------------------------------------


def _run_gh(*args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    """Shell out to the `gh` CLI. Runs as the gh-CLI-authenticated identity
    (ryosaeba1985 for our setup) — used for branch + PR creation."""
    cmd = ["gh", *args]
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def _create_branch_with_file(
    sandbox_repo: str,
    base_branch: str,
    branch: str,
    file_path: str,
    file_content: str,
    commit_message: str,
) -> str:
    """Create a new branch in sandbox with the given file, return the head sha.

    Implementation uses the GitHub Contents API via `gh api` — we don't need
    a local clone. For now this is happy-path only; existing-branch handling
    is a follow-up.
    """
    # 1. Get the base branch's head sha.
    base_ref = _run_gh("api", f"repos/{sandbox_repo}/git/refs/heads/{base_branch}")
    import json as _json

    base_sha = _json.loads(base_ref.stdout)["object"]["sha"]

    # 2. Create the new branch ref.
    _run_gh(
        "api",
        "--method",
        "POST",
        f"repos/{sandbox_repo}/git/refs",
        "-f",
        f"ref=refs/heads/{branch}",
        "-f",
        f"sha={base_sha}",
    )

    # 3. PUT the file via Contents API on the new branch.
    import base64 as _b64

    content_b64 = _b64.b64encode(file_content.encode("utf-8")).decode("ascii")
    result = _run_gh(
        "api",
        "--method",
        "PUT",
        f"repos/{sandbox_repo}/contents/{file_path}",
        "-f",
        f"message={commit_message}",
        "-f",
        f"content={content_b64}",
        "-f",
        f"branch={branch}",
    )
    head_sha = _json.loads(result.stdout)["commit"]["sha"]
    return head_sha


def _open_pr(sandbox_repo: str, branch: str, base: str, title: str, body: str) -> dict[str, Any]:
    """Open a PR and return the parsed PR object."""
    result = _run_gh(
        "api",
        "--method",
        "POST",
        f"repos/{sandbox_repo}/pulls",
        "-f",
        f"title={title}",
        "-f",
        f"head={branch}",
        "-f",
        f"base={base}",
        "-f",
        f"body={body}",
    )
    import json as _json

    return _json.loads(result.stdout)


def _mint_test_bot_token(app: TestBotApp) -> str:
    """Mint an installation token for the test GitHub App.

    Uses voyager's GitHubAppClient JWT logic by importing it directly — same
    auth as voyager itself uses for the iterwheel-clearance App.
    """
    from voyager.core.config import AppConfig
    from voyager.core.github_app import GitHubAppClient

    cfg = AppConfig(
        slug="voyager-e2e-test",
        app_id=app.app_id,
        private_key_path=app.private_key_path,
        installation_id=app.installation_id,
        installations={},
    )
    client = GitHubAppClient({cfg.slug: cfg})
    # Use the client's internal token-fetch path. (Sync wrapper around the
    # async installation-token endpoint.)
    import asyncio as _asyncio

    token = _asyncio.run(client._installation_token(cfg.slug, app.installation_id))
    return token


def _post_review_thread(
    *,
    sandbox_repo: str,
    pr_number: int,
    commit_sha: str,
    path: str,
    line: int,
    body: str,
    token: str,
) -> dict[str, Any]:
    """Post a single-comment review on the PR using the test bot's installation token.

    A 'review' (POST /pulls/{n}/reviews with event=COMMENT and one comment)
    creates a review thread voyager will pick up via the pull_request_review
    webhook.
    """
    with httpx.Client(timeout=15.0) as c:
        r = c.post(
            f"https://api.github.com/repos/{sandbox_repo}/pulls/{pr_number}/reviews",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
            },
            json={
                "commit_id": commit_sha,
                "event": "COMMENT",
                "body": "",
                "comments": [{"path": path, "line": line, "body": body}],
            },
        )
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------------------
# Scenario execution (Phase A — happy path only; cleanup minimal)
# ---------------------------------------------------------------------------


def _run_scenario(
    scenario: dict[str, Any],
    cfg: RunnerConfig,
    dashboard: Dashboard,
    token: str,
) -> None:
    sid = scenario["id"]
    category = scenario.get("category", "?")
    description = (
        scenario.get("description", "").strip().splitlines()[0]
        if scenario.get("description")
        else ""
    )
    expected = scenario.get("expected", {})

    started_at = time.time()
    dashboard.update(
        {
            "id": sid,
            "category": category,
            "description": description,
            "status": "running",
            "started_at": started_at,
            "expected": expected,
        }
    )

    if cfg.dry_run_sandbox:
        print(f"[{sid}] DRY-RUN-SANDBOX: would create PR + post review. Skipping real calls.")
        dashboard.update(
            {
                "id": sid,
                "category": category,
                "description": description,
                "status": "skipped",
                "started_at": started_at,
                "finished_at": time.time(),
                "expected": expected,
                "error": "dry-run-sandbox mode (DRY_RUN_SANDBOX=1)",
            }
        )
        return

    setup = scenario["setup"]
    branch = setup["branch"]
    file_path = setup["file_path"]
    file_content = setup["file_content"]

    pr_number: int | None = None
    pr_url: str | None = None
    error: str | None = None
    actual: dict[str, Any] = {}

    try:
        # 1. Create branch + file.
        commit_msg = f"e2e: {sid} initial commit"
        head_sha = _create_branch_with_file(
            cfg.sandbox_repo, cfg.base_branch, branch, file_path, file_content, commit_msg
        )
        # 2. Open PR.
        pr = _open_pr(
            cfg.sandbox_repo,
            branch,
            cfg.base_branch,
            title=f"[E2E {sid}] {description[:60]}",
            body=f"Auto-generated by scripts/e2e/run_matrix.py.\n\nScenario: `{sid}`\n\n{description}",
        )
        pr_number = pr["number"]
        pr_url = pr["html_url"]
        actual["pr_number"] = pr_number
        dashboard.update(
            {
                "id": sid,
                "category": category,
                "description": description,
                "status": "running",
                "started_at": started_at,
                "pr_number": pr_number,
                "pr_url": pr_url,
                "expected": expected,
            }
        )

        # 3. Post review threads.
        for review in scenario.get("review", []):
            if "thread_reply" in review:
                # TODO Phase B: implement thread-reply posting using author PAT.
                continue
            _post_review_thread(
                sandbox_repo=cfg.sandbox_repo,
                pr_number=pr_number,
                commit_sha=head_sha,
                path=review["path"],
                line=review["line"],
                body=review["body"],
                token=token,
            )

        # 4. Wait for voyager to process the webhook.
        # TODO Phase B: poll voyager's writeback log / state store for a marker.
        # For Phase A, just sleep and report a placeholder "processed" status.
        time.sleep(8)  # crude — replace with real polling

        # 5. TODO Phase B: capture actual decision from voyager's logs / state.
        actual["status"] = "TODO_capture_from_voyager_logs"

        # 6. Compare.
        # TODO Phase B: implement deep-eq of actual vs expected with reasonable
        # tolerance (e.g., ignore unrelated fields).
        passed = False  # always FAIL in Phase A until comparator is wired
        verdict = "passed" if passed else "failed"
        if not passed:
            error = "Phase A scaffold: comparator not yet implemented (TODO)"

    except Exception as exc:
        verdict = "error"
        error = f"{type(exc).__name__}: {exc}"

    dashboard.update(
        {
            "id": sid,
            "category": category,
            "description": description,
            "status": verdict,
            "started_at": started_at,
            "finished_at": time.time(),
            "pr_number": pr_number,
            "pr_url": pr_url,
            "expected": expected,
            "actual": actual,
            "error": error,
        }
    )

    # TODO Phase B: cleanup branch + close PR after assertion.


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def _load_matrix(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _filter_scenarios(scenarios: list[dict], filters: list[str]) -> list[dict]:
    if not filters:
        return scenarios
    return [s for s in scenarios if any(s["id"].startswith(f) for f in filters)]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Voyager E2E matrix runner")
    p.add_argument("--matrix", type=Path, default=Path("scripts/e2e/matrix.yaml"))
    p.add_argument("--dashboard", default="http://127.0.0.1:9099")
    p.add_argument(
        "--filter",
        action="append",
        default=[],
        help="Run only scenarios whose id startswith this prefix (repeatable)",
    )
    p.add_argument(
        "--dry-run-sandbox",
        action="store_true",
        help="Don't actually hit GitHub; just stream skip events to the dashboard",
    )
    return p.parse_args()


def _build_test_bot() -> TestBotApp:
    """Read test-App credentials from env (operator-set) or fall back to known paths."""
    app_id = os.environ.get("VOYAGER_E2E_TEST_BOT_APP_ID", "")
    installation_id = os.environ.get("VOYAGER_E2E_TEST_BOT_INSTALLATION_ID", "")
    pem_path_str = os.environ.get(
        "VOYAGER_E2E_TEST_BOT_PEM",
        str(Path.home() / ".voyager" / "secrets" / "voyager-e2e-bot.pem"),
    )
    bot_login = os.environ.get("VOYAGER_E2E_TEST_BOT_LOGIN", "voyager-e2e-bot[bot]")

    missing = [
        k
        for k, v in [
            ("VOYAGER_E2E_TEST_BOT_APP_ID", app_id),
            ("VOYAGER_E2E_TEST_BOT_INSTALLATION_ID", installation_id),
        ]
        if not v
    ]
    if missing:
        raise SystemExit(
            f"Missing required env vars: {', '.join(missing)}.\n"
            f"Set them after registering the test GitHub App (see scripts/e2e/README.md)."
        )

    pem_path = Path(pem_path_str).expanduser()
    if not pem_path.exists():
        raise SystemExit(
            f"Test-bot PEM not found at {pem_path}. Place the App's private key there or set VOYAGER_E2E_TEST_BOT_PEM."
        )

    return TestBotApp(
        app_id=app_id,
        installation_id=installation_id,
        private_key_path=pem_path,
        bot_login=bot_login,
    )


def main() -> int:
    args = _parse_args()
    matrix = _load_matrix(args.matrix)
    scenarios = _filter_scenarios(matrix["scenarios"], args.filter)

    dry_run_sandbox = args.dry_run_sandbox or bool(os.environ.get("DRY_RUN_SANDBOX"))
    test_bot = None if dry_run_sandbox else _build_test_bot()

    dashboard = Dashboard(args.dashboard)
    dashboard.reset()

    # Pre-register all scenarios as "queued" so the UI shows the full list immediately.
    for s in scenarios:
        first_line = (s.get("description", "").strip().splitlines() or [""])[0]
        dashboard.update(
            {
                "id": s["id"],
                "category": s.get("category", "?"),
                "description": first_line,
                "status": "queued",
                "expected": s.get("expected", {}),
            }
        )

    print(f"Running {len(scenarios)} scenario(s) against {matrix['sandbox_repo']}.")
    print(f"Dashboard: {args.dashboard}")
    if dry_run_sandbox:
        print("DRY_RUN_SANDBOX mode — no GitHub calls.")

    token = ""
    if not dry_run_sandbox:
        assert test_bot is not None  # nosec
        token = _mint_test_bot_token(test_bot)

    cfg = RunnerConfig(
        matrix_path=args.matrix,
        dashboard_url=args.dashboard,
        sandbox_repo=matrix["sandbox_repo"],
        base_branch=matrix["base_branch"],
        test_bot=test_bot,  # type: ignore[arg-type]
        scenario_filter=args.filter,
        dry_run_sandbox=dry_run_sandbox,
    )

    for s in scenarios:
        try:
            _run_scenario(s, cfg, dashboard, token)
        except Exception as exc:
            print(f"[{s['id']}] runner crashed: {exc}", file=sys.stderr)

    dashboard.close()
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
