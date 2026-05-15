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
import asyncio
import base64
import contextlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp used to filter pre-review writebacks (Codex GH-bot
    PR #15 P1). Matches voyager's `_utc_now` formatter so direct string
    comparison works (lexicographic == chronological for ISO-8601)."""
    return datetime.now(UTC).isoformat()


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
    voyager_url: str
    sandbox_repo: str
    base_branch: str
    test_bot: TestBotApp | None
    scenario_filter: list[str]  # only run scenarios whose id startswith any of these
    dry_run_sandbox: bool  # if True, log actions instead of hitting GitHub
    poll_timeout_s: int = 60
    poll_interval_s: float = 1.5
    voyager_e2e_token: str | None = None  # paired with VOYAGER_E2E_TOKEN on the server


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


def _close_open_prs_for_branch(sandbox_repo: str, branch: str) -> None:
    """Close any OPEN PRs that have ``branch`` as their head ref.

    Crashed prior runs may leave PRs open after the branch is gone; the
    subsequent run's branch DELETE then orphans the PR with an invalid
    head. Listing + closing first keeps the sandbox repo tidy and lets
    scenarios re-run idempotently.

    Best-effort: any error is swallowed so scenario startup never blocks
    on cleanup-of-debris from a previous run.
    """
    with contextlib.suppress(Exception):
        # GET /repos/{owner}/{repo}/pulls?state=open&head={owner}:{branch}
        owner = sandbox_repo.split("/", 1)[0]
        result = _run_gh(
            "api",
            "--method",
            "GET",
            f"repos/{sandbox_repo}/pulls",
            "-F",
            "state=open",
            "-F",
            f"head={owner}:{branch}",
            check=False,
        )
        if result.returncode != 0:
            return
        try:
            prs = json.loads(result.stdout)
        except json.JSONDecodeError:
            return
        if not isinstance(prs, list):
            return
        for pr in prs:
            pr_number = pr.get("number")
            if not isinstance(pr_number, int):
                continue
            with contextlib.suppress(Exception):
                _run_gh(
                    "api",
                    "--method",
                    "PATCH",
                    f"repos/{sandbox_repo}/pulls/{pr_number}",
                    "-f",
                    "state=closed",
                    check=False,
                )


def _create_branch_with_file(
    sandbox_repo: str,
    base_branch: str,
    branch: str,
    file_path: str,
    file_content: str,
    commit_message: str,
) -> str:
    """Create a new branch in sandbox with the given file, return the head sha.

    Implementation uses the GitHub Contents API via ``gh api`` (no local clone).
    Handles existing-branch state by deleting + recreating the ref so repeated
    runs of the same scenario are idempotent (Codex/Gemini/GLM r1 P2).
    """
    # 1. Get the base branch's head sha.
    base_ref = _run_gh("api", f"repos/{sandbox_repo}/git/refs/heads/{base_branch}")
    base_sha = json.loads(base_ref.stdout)["object"]["sha"]

    # 2a. Close any open PRs that have this branch as head — if a previous
    # crashed run left a PR open against this branch, deleting the branch
    # ref below would orphan the PR with an invalid head ref. Close first
    # (Codex GH-bot PR #15 P2 #15).
    _close_open_prs_for_branch(sandbox_repo, branch)

    # 2b. Delete any pre-existing branch with the same name (idempotency).
    with contextlib.suppress(subprocess.CalledProcessError):
        _run_gh(
            "api",
            "--method",
            "DELETE",
            f"repos/{sandbox_repo}/git/refs/heads/{branch}",
            check=False,
        )

    # 3. Create the new branch ref.
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

    # 4. PUT the file via Contents API on the new branch.
    content_b64 = base64.b64encode(file_content.encode("utf-8")).decode("ascii")
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
    head_sha = json.loads(result.stdout)["commit"]["sha"]
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
    return json.loads(result.stdout)


def _mint_test_bot_token(app: TestBotApp) -> str:
    """Mint an installation token for the test GitHub App.

    Uses voyager.core.github_app.GitHubAppClient.installation_token (public
    API) — same auth path voyager itself uses for iterwheel-clearance.
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
    return asyncio.run(client.installation_token(cfg.slug))


def _get_file_blob_sha(sandbox_repo: str, branch: str, file_path: str) -> str | None:
    """Return the current blob SHA for ``file_path`` on ``branch``, or None
    when the file doesn't exist OR when ``file_path`` resolves to a
    directory.

    Layered defenses:
      1. ``returncode != 0`` → None (404 or other gh error).
      2. ``json.JSONDecodeError`` → None (defensive).
      3. ``payload`` not a dict → None. Contents API returns a JSON ARRAY
         when the path resolves to a directory (Gemini r5 P1); the prior
         ``.get("sha")`` would raise AttributeError on the list.
      4. ``sha`` absent or not a non-empty string → None. Avoids the
         jq-emitted ``"null"`` literal (Gemini r4 P1).

    Branch is passed via ``-F ref=...`` rather than URL-query so branches
    containing ``#`` / ``+`` / ``%`` are handled correctly by gh (Gemini r5 P2).
    """
    result = _run_gh(
        "api",
        "--method",
        "GET",
        f"repos/{sandbox_repo}/contents/{file_path}",
        "-F",
        f"ref={branch}",
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    sha = payload.get("sha")
    return sha if isinstance(sha, str) and sha else None


def _force_push_new_content(
    sandbox_repo: str,
    branch: str,
    file_path: str,
    new_content: str,
    commit_message: str,
) -> str:
    """Update / create a file on the branch via Contents API. Returns the new
    head SHA. Used by scenarios with `force_push_after_review` to make the
    PR's head SHA stale relative to the webhook voyager just processed.
    """
    content_b64 = base64.b64encode(new_content.encode("utf-8")).decode("ascii")
    args = [
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
    ]
    existing_sha = _get_file_blob_sha(sandbox_repo, branch, file_path)
    if existing_sha:
        # Required by GitHub API when updating an existing file.
        args.extend(["-f", f"sha={existing_sha}"])
    result = _run_gh(*args)
    return json.loads(result.stdout)["commit"]["sha"]


def _review_inline_comment_ids(sandbox_repo: str, pr_number: int, review_id: int) -> list[int]:
    """Fetch the inline comment IDs of a posted review.

    Gemini r4 P1: ``POST /pulls/{n}/reviews`` returns the Review object only,
    NOT the inline comments it created. To get them we must call
    ``GET /pulls/{n}/reviews/{review_id}/comments`` after creation.

    Uses ``--paginate`` (Gemini r5 P2) so reviews with >30 inline comments
    aren't silently truncated — matrix.yaml's schema enforces
    one-comment-per-review-item today but the safety belt is cheap.
    """
    result = _run_gh(
        "api",
        "--paginate",
        f"repos/{sandbox_repo}/pulls/{pr_number}/reviews/{review_id}/comments",
        check=False,
    )
    if result.returncode != 0:
        return []
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [int(c["id"]) for c in payload if isinstance(c.get("id"), int)]


def _post_thread_reply(
    *,
    sandbox_repo: str,
    pr_number: int,
    comment_id: int,
    body: str,
) -> dict[str, Any]:
    """Post a reply to an existing review-thread comment. Uses the gh CLI's
    authenticated identity (ryosaeba1985 PAT in our setup) so the reply is
    attributed to the PR author, not the test bot — which is what voyager's
    ``latest_author_reply`` filter requires for F-class scenarios.
    """
    result = _run_gh(
        "api",
        "--method",
        "POST",
        f"repos/{sandbox_repo}/pulls/{pr_number}/comments/{comment_id}/replies",
        "-f",
        f"body={body}",
    )
    return json.loads(result.stdout)


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
# Voyager polling + comparator
# ---------------------------------------------------------------------------


def _extract_pr_number(writeback: dict[str, Any]) -> int | None:
    """Walk a writeback record looking for the PR number under a few known keys."""
    # Common shapes: top-level "pr_number", nested under route.validation, etc.
    candidates = [
        writeback.get("pr_number"),
        writeback.get("pr"),
        (writeback.get("route") or {}).get("pr_number"),
        ((writeback.get("route") or {}).get("validation") or {}).get("pr_number"),
        (writeback.get("planned") or {}).get("pr_number"),
    ]
    for c in candidates:
        if isinstance(c, int):
            return c
        if isinstance(c, str) and c.isdigit():
            return int(c)
    return None


def _flatten_writeback(writeback: dict[str, Any]) -> dict[str, Any]:
    """Pluck the fields we care about into a flat dict for easier comparison.

    The writeback record voyager emits is one of three shapes (see
    voyager/core/writeback.py:dispatch_route_writeback):
      1. Apply path:   {applied, dry_run, planned, comment_url}
      2. Stale skip:   {ok, skipped, automation}
      3. Error:        {applied: False, reason}

    Plus the top-level wrap adds {delivery_id, event}.

    Per-thread severity/finding_kind/investigator_verdict are NOT in the
    writeback today — they live inside the pipeline's thread state which the
    writeback record doesn't include. Exposing them is a Phase B task (likely
    a voyager-side change to ``compute_clearance_automation`` to include a
    ``threads_summary`` array). For Phase A, scenarios assert PR-level
    aggregated state (status / automation_status / writeback_skipped /
    label_present) which IS available here.
    """
    automation = writeback.get("automation") or {}
    planned = writeback.get("planned") or {}
    add_labels = planned.get("add_labels") or []
    return {
        # Top-level envelope (added in server._process_route_writebacks)
        "event": writeback.get("event"),
        "delivery_id": writeback.get("delivery_id"),
        # Apply-path fields
        "applied": writeback.get("applied"),
        "dry_run": writeback.get("dry_run"),
        "reason": writeback.get("reason"),
        "comment_url": writeback.get("comment_url"),
        # Stale-skip fields
        "ok": writeback.get("ok"),
        "skipped": writeback.get("skipped"),
        "writeback_skipped": writeback.get("skipped") == "stale_verdict",
        # Automation summary (from compute_clearance_automation)
        "status": automation.get("status"),
        "automation_status": automation.get("status"),
        "automation_reason": automation.get("reason"),
        "head_sha": automation.get("head_sha"),
        "unresolved_codex_thread_count": automation.get("unresolved_codex_thread_count"),
        "sync_actions_count": automation.get("sync_actions_count"),
        "investigator_error_count": automation.get("investigator_error_count"),
        # Planned label / reaction summary (apply path)
        "add_labels": add_labels,
        "label_present": add_labels[0] if add_labels else None,
        "add_reactions": planned.get("add_reactions") or [],
        # Raw record for debugging in failing scenarios
        "_raw": writeback,
    }


def _poll_for_writeback(
    *,
    voyager_url: str,
    pr_number: int,
    timeout_s: int = 60,
    interval_s: float = 1.5,
    auth_token: str | None = None,
    event_filter: tuple[str, ...] = ("pull_request_review", "pull_request_review_comment"),
    since_ts: str | None = None,
    repository: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Poll voyager's /e2e/recent_writebacks until we see a matching record.

    Filtering layered on top of `pr_number == pr_number`:
      - ``event_filter`` restricts to records emitted by the post-review
        webhooks (default: pull_request_review*). Without this, voyager's
        prior `pull_request opened` writeback can be returned first
        (Codex GH-bot PR #15 P1) because the deque is in arrival order.
      - ``since_ts``: ignore records older than this ISO timestamp. The
        runner captures a timestamp BEFORE posting the review and passes
        it here so any pre-review writeback is excluded from the match
        even if it shares the event type (defense-in-depth).

    Returns (writeback_dict, error_message). On success, error_message is None.
    On non-transient HTTP errors (404 = endpoint not enabled; 401/403 = auth
    failure), returns (None, "fail-fast reason") without retrying. Transient
    errors (network, 5xx) are retried until deadline; if deadline hits without
    a matching record, returns (None, "timed out").
    """
    deadline = time.time() + timeout_s
    url = f"{voyager_url.rstrip('/')}/e2e/recent_writebacks"
    headers: dict[str, str] = {}
    if auth_token:
        headers["X-Voyager-E2E-Token"] = auth_token

    last_transient: str | None = None
    with httpx.Client(timeout=5.0) as c:
        while time.time() < deadline:
            try:
                r = c.get(url, headers=headers)
            except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError) as exc:
                # Transient — retry until deadline.
                last_transient = f"{type(exc).__name__}: {exc}"
                time.sleep(interval_s)
                continue

            if r.status_code in (401, 403):
                return None, (
                    f"voyager auth rejected ({r.status_code}): the e2e endpoint "
                    f"requires VOYAGER_E2E_DEBUG=1 and a matching X-Voyager-E2E-Token "
                    f"header if VOYAGER_E2E_TOKEN is set."
                )
            if r.status_code == 404:
                return None, (
                    "voyager returned 404 for /e2e/recent_writebacks — endpoint is "
                    "gated by VOYAGER_E2E_DEBUG=1; start voyager with that env set."
                )
            if r.status_code >= 500:
                last_transient = f"voyager 5xx: HTTP {r.status_code}"
                time.sleep(interval_s)
                continue
            if r.status_code != 200:
                return None, f"unexpected HTTP {r.status_code}: {r.text[:200]}"

            # Scan all candidates and return the NEWEST matching record
            # (Codex r5 P2: the prior "filter + take first" semantic raced
            # with the marker-advance window — a writeback emitted while
            # the runner was capturing review_start_ts could be filtered
            # out as too-old). Newest-wins sidesteps the timing race
            # entirely: even if the marker is slightly ahead of the
            # desired writeback's ts, that writeback is just one of N
            # matches; we sort by ts and return the latest.
            candidates: list[dict[str, Any]] = []
            for wb in r.json().get("writebacks", []):
                if _extract_pr_number(wb) != pr_number:
                    continue
                # Repository scoping — PR numbers are only unique within a
                # repo, and voyager may handle multiple repos in the same
                # process (Codex GH-bot PR #15 P2 #6).
                if repository and wb.get("repository") and wb["repository"] != repository:
                    continue
                if event_filter and wb.get("event") not in event_filter:
                    continue
                if since_ts and (wb.get("ts") or "") < since_ts:
                    continue
                candidates.append(wb)
            if candidates:
                candidates.sort(key=lambda w: w.get("ts") or "")
                return candidates[-1], None
            time.sleep(interval_s)

    suffix = f" (last transient: {last_transient})" if last_transient else ""
    return None, f"timed out after {timeout_s}s waiting for PR #{pr_number}{suffix}"


def _compare(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Soft comparator — return list of mismatch messages (empty == pass).

    Each expected key is looked up in the flattened actual; mismatches are
    reported in 'key: expected X, got Y' form. Unknown expected keys (not in
    actual) are reported as 'key: not surfaced by voyager (actual missing)'.

    Keys with the ``_substring`` suffix do a partial-match against the base
    key's value. Treats ``None`` in actual as "no value" — substring check on
    None never matches (avoids the ``"None" in "NoneType"`` false-positive).
    """
    mismatches: list[str] = []
    for k, exp in expected.items():
        if k.endswith("_substring"):
            # e.g. expected: reason_substring: "low-priority"
            base = k.removesuffix("_substring")
            raw = actual.get(base)
            if raw is None:
                mismatches.append(
                    f"{base}: not surfaced by voyager (cannot do substring match on None)"
                )
                continue
            got = str(raw)
            if exp not in got:
                mismatches.append(f"{base}: expected substring {exp!r}, got {got!r}")
            continue
        if k not in actual:
            mismatches.append(f"{k}: not surfaced by voyager (actual missing)")
            continue
        got = actual[k]
        if got != exp:
            mismatches.append(f"{k}: expected {exp!r}, got {got!r}")
    return mismatches


def _cleanup_pr(sandbox_repo: str, pr_number: int | None, branch: str | None) -> None:
    """Best-effort close PR + delete branch. Either may be None (partial setup
    that failed before the resource was created)."""
    if pr_number is not None:
        with contextlib.suppress(Exception):
            _run_gh(
                "api",
                "--method",
                "PATCH",
                f"repos/{sandbox_repo}/pulls/{pr_number}",
                "-f",
                "state=closed",
                check=False,
            )
    if branch:
        with contextlib.suppress(Exception):
            _run_gh(
                "api",
                "--method",
                "DELETE",
                f"repos/{sandbox_repo}/git/refs/heads/{branch}",
                check=False,
            )


# ---------------------------------------------------------------------------
# Scenario execution (Phase A — polling + comparator + cleanup wired)
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
    created_branch: str | None = None  # set once the branch ref exists; cleanup uses this
    error: str | None = None
    actual: dict[str, Any] = {}
    verdict = "failed"

    try:
        # 1. Create branch + file.
        commit_msg = f"e2e: {sid} initial commit"
        head_sha = _create_branch_with_file(
            cfg.sandbox_repo, cfg.base_branch, branch, file_path, file_content, commit_msg
        )
        created_branch = branch  # branch now exists; tracked separately for cleanup

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

        # Mark the start-of-review timestamp; the poll filter uses it to
        # exclude voyager's pre-review `pull_request opened` writeback
        # (Codex GH-bot PR #15 P1).
        review_start_ts = _utc_now_iso()

        # 3. Post review threads + collect inline-comment IDs for thread_reply use.
        # POST /pulls/{n}/reviews returns only the Review object (no comments
        # array) — Gemini r4 P1. To get the inline-comment IDs we created, we
        # GET /pulls/{n}/reviews/{review_id}/comments after each post.
        posted_review_ids: list[int] = []
        for review in scenario.get("review", []):
            if "thread_reply" in review:
                # Wait until after the regular reviews are posted; then reply.
                continue
            posted = _post_review_thread(
                sandbox_repo=cfg.sandbox_repo,
                pr_number=pr_number,
                commit_sha=head_sha,
                path=review["path"],
                line=review["line"],
                body=review["body"],
                token=token,
            )
            review_id = posted.get("id")
            if isinstance(review_id, int):
                posted_review_ids.extend(
                    _review_inline_comment_ids(cfg.sandbox_repo, pr_number, review_id)
                )

        # 3b. Post any thread_reply entries against the parent review comment.
        for review in scenario.get("review", []):
            tr = review.get("thread_reply")
            if not tr:
                continue
            idx = tr.get("previous_thread_index", 0)
            if idx >= len(posted_review_ids):
                raise RuntimeError(
                    f"thread_reply references previous_thread_index={idx} but only "
                    f"{len(posted_review_ids)} reviews were posted earlier"
                )
            _post_thread_reply(
                sandbox_repo=cfg.sandbox_repo,
                pr_number=pr_number,
                comment_id=posted_review_ids[idx],
                body=tr["body"],
            )

        # 3c. Optional `force_push_after_review` hook for E-class scenarios
        # (make the head SHA stale after voyager observed the review).
        fp = setup.get("force_push_after_review")
        if fp:
            _force_push_new_content(
                cfg.sandbox_repo,
                branch,
                fp["file_path"],
                fp["new_content"],
                commit_message=f"e2e: {sid} force-push to stale the head",
            )

        # The previous code advanced `review_start_ts` AFTER posting the reply /
        # force-push to filter out earlier writebacks. Codex r6 P2 caught the
        # race window: voyager could emit the desired writeback DURING the
        # 50ms sleep with `ts < new_marker`, then since_ts would drop it.
        # We rely on:
        #   - the initial marker (captured pre-state-change) to exclude
        #     voyager's pre-review `pull_request opened` writeback
        #   - event_filter to require pull_request_review* event type
        #   - newest-ts wins in `_poll_for_writeback` to pick the latest
        #     post-state writeback among multiple
        # No timing assumptions needed.

        # 4. Poll voyager for our PR's writeback (post-review event only).
        writeback, poll_error = _poll_for_writeback(
            voyager_url=cfg.voyager_url,
            pr_number=pr_number,
            timeout_s=cfg.poll_timeout_s,
            interval_s=cfg.poll_interval_s,
            auth_token=cfg.voyager_e2e_token,
            since_ts=review_start_ts,
            repository=cfg.sandbox_repo,
        )
        if writeback is None:
            verdict = "failed"
            error = poll_error or f"no matching writeback record for PR #{pr_number}"
        else:
            actual = _flatten_writeback(writeback)
            # Preserve the PR identity in the dashboard payload (DeepSeek r1 P1).
            actual["pr_number"] = pr_number
            mismatches = _compare(expected, actual)
            if mismatches:
                verdict = "failed"
                error = "; ".join(mismatches)
            else:
                verdict = "passed"
                error = None

    except Exception as exc:
        verdict = "error"
        error = f"{type(exc).__name__}: {exc}"

    finally:
        # Single cleanup pass — runs once for both success and error paths.
        # Tracks `created_branch` independently of `pr_number` so an orphan
        # branch (PR creation failed after branch creation) still gets cleaned
        # (Codex/Gemini r1 P2 branch-leak).
        with contextlib.suppress(Exception):
            _cleanup_pr(cfg.sandbox_repo, pr_number, created_branch)

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


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def _load_matrix(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _filter_scenarios(
    scenarios: list[dict], filters: list[str], include_phases: list[str]
) -> tuple[list[dict], list[dict]]:
    """Apply --filter and --include-phase to the scenario list.

    Returns (selected, skipped_by_phase) so the runner can surface the
    skipped-by-phase scenarios to the dashboard as `skipped` rows (more
    honest than silent omission). Codex GH-bot PR #15 P2 #13: scenarios
    that aren't safe for the active phase (e.g. E1 needs Phase B sync)
    are gated out by default — Phase A runners never accidentally run
    them.
    """
    if filters:
        scenarios = [s for s in scenarios if any(s["id"].startswith(f) for f in filters)]
    selected: list[dict] = []
    skipped: list[dict] = []
    for s in scenarios:
        phase = (s.get("phase") or "A").upper()
        if phase in {p.upper() for p in include_phases}:
            selected.append(s)
        else:
            skipped.append(s)
    return selected, skipped


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Voyager E2E matrix runner")
    p.add_argument("--matrix", type=Path, default=Path("scripts/e2e/matrix.yaml"))
    p.add_argument("--dashboard", default="http://127.0.0.1:9099")
    p.add_argument(
        "--voyager",
        default="http://127.0.0.1:8000",
        help="Voyager server URL — polled at /e2e/recent_writebacks for decisions",
    )
    p.add_argument("--poll-timeout", type=int, default=60)
    p.add_argument("--poll-interval", type=float, default=1.5)
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
    p.add_argument(
        "--include-phase",
        action="append",
        default=None,
        help=(
            "Run scenarios with this `phase:` value (matrix.yaml). Repeatable. "
            "Default: ['A']. Pass `--include-phase A --include-phase B` to run both."
        ),
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
    include_phases = args.include_phase or ["A"]
    scenarios, phase_skipped = _filter_scenarios(matrix["scenarios"], args.filter, include_phases)

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

    # Phase-skipped scenarios surface to the dashboard so the operator can see
    # what was elided + why (Codex GH-bot PR #15 P2 #13).
    for s in phase_skipped:
        first_line = (s.get("description", "").strip().splitlines() or [""])[0]
        dashboard.update(
            {
                "id": s["id"],
                "category": s.get("category", "?"),
                "description": first_line,
                "status": "skipped",
                "expected": s.get("expected", {}),
                "error": (
                    f"phase={s.get('phase') or 'A'} excluded from current run "
                    f"(--include-phase {' '.join(include_phases)})"
                ),
                "started_at": time.time(),
                "finished_at": time.time(),
            }
        )

    print(
        f"Running {len(scenarios)} scenario(s) against {matrix['sandbox_repo']} "
        f"(skipping {len(phase_skipped)} out-of-phase)."
    )
    print(f"Dashboard: {args.dashboard}")
    if dry_run_sandbox:
        print("DRY_RUN_SANDBOX mode — no GitHub calls.")

    token = ""
    if not dry_run_sandbox:
        if test_bot is None:
            raise RuntimeError(
                "test_bot must be configured when not in --dry-run-sandbox mode; "
                "see scripts/e2e/README.md for setup."
            )
        token = _mint_test_bot_token(test_bot)

    cfg = RunnerConfig(
        matrix_path=args.matrix,
        dashboard_url=args.dashboard,
        voyager_url=args.voyager,
        sandbox_repo=matrix["sandbox_repo"],
        base_branch=matrix["base_branch"],
        test_bot=test_bot,
        scenario_filter=args.filter,
        dry_run_sandbox=dry_run_sandbox,
        poll_timeout_s=args.poll_timeout,
        poll_interval_s=args.poll_interval,
        voyager_e2e_token=os.environ.get("VOYAGER_E2E_TOKEN") or None,
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
