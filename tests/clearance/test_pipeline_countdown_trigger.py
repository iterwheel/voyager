"""CHG-1842: in-process Countdown trigger touch from the Clearance pipeline.

GitHub suppresses webhook delivery of a GitHub App's own comments, so the
CHG-1841 webhook route never fires on repositories where only the Clearance
App delivers events. These tests cover the in-process touch at every
post-success RESOLVED-comment seam inside ``_maybe_sync_stage_15``: the
normal resolve close-reason reply, the delegated-resolver fallback reply, and
the manual-close reply — gated by the same
``_repository_allowed_for_agent(repo, COUNTDOWN_AGENT_SLUG, cfg)`` predicate
the webhook route uses, and fail-open on touch failure.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import pytest

from voyager.bots.clearance.models import (
    Evidence,
    GitHubThreadState,
    Severity,
    Thread,
    ThreadSnapshot,
    Verdict,
)
from voyager.bots.clearance.pipeline import _maybe_sync_stage_15


class _WritebackClient:
    def __init__(self) -> None:
        self.reply_calls: list[tuple[str, str, int, int, str]] = []
        self.resolve_calls: list[tuple[str, str, str]] = []
        self.thread_comments: list[dict[str, Any]] = []
        self.resolver_viewer_can_resolve_by_app: dict[str, bool] = {
            "iterwheel-assembly": True,
        }

    async def create_review_thread_reply(
        self,
        app_slug: str,
        repository: str,
        pull_number: int,
        comment_id: int,
        *,
        body: str,
    ) -> dict[str, Any]:
        self.reply_calls.append((app_slug, repository, pull_number, comment_id, body))
        database_id = 100100 + len(self.thread_comments)
        self.thread_comments.append(
            {
                "databaseId": database_id,
                "author": {"login": "iterwheel-clearance"},
                "body": body,
                "createdAt": f"2026-05-11T12:{45 + len(self.thread_comments):02d}:00Z",
            }
        )
        return {"html_url": "https://example/reply"}

    async def pull_request_review_threads(
        self, app_slug: str, repository: str, pull_number: int
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": "PRRT_alpha",
                "isResolved": False,
                "isOutdated": False,
                "viewerCanResolve": self.resolver_viewer_can_resolve_by_app.get(
                    app_slug,
                    app_slug == "iterwheel-assembly",
                ),
                "comments": {"nodes": list(self.thread_comments)},
            }
        ]

    async def check_head_repo_accessible(self, app_slug: str, head_repo: str) -> bool:
        return True

    async def resolve_review_thread(
        self, app_slug: str, repository: str, thread_id: str
    ) -> dict[str, Any]:
        self.resolve_calls.append((app_slug, repository, thread_id))
        return {
            "id": thread_id,
            "isResolved": True,
            "resolvedBy": {"login": f"{app_slug}[bot]"},
        }


def _thread(verdict: Verdict) -> Thread:
    return Thread(
        id="PRRT_alpha",
        comment_id=100001,
        path="app.py",
        line=10,
        codex_severity=Severity.P1,
        effective_severity=Severity.P1,
        verdict=verdict,
        verdict_reason="unit-test verdict",
        github_isResolved=False,
    )


def _snapshot(
    *, viewer_can_resolve: bool = True, verdict: Verdict = Verdict.OPEN
) -> ThreadSnapshot:
    now = datetime.now(UTC).replace(microsecond=0)
    return ThreadSnapshot(
        thread_id="PRRT_alpha",
        repo="iterwheel/sandbox",
        pr=49,
        first_seen=now,
        last_polled=now,
        codex_comment_id=100001,
        path="app.py",
        current_line=10,
        codex_severity=Severity.P1,
        effective_severity=Severity.P1,
        verdict=verdict,
        evidence=Evidence(),
        github_state=GitHubThreadState(
            isResolved=False,
            isOutdated=False,
            viewerCanResolve=viewer_can_resolve,
        ),
    )


REPO = "iterwheel/sandbox"


@pytest.fixture(autouse=True)
def _clean_allowlist_env(monkeypatch):
    for key in list(os.environ):
        if key == "BRIDGE_ALLOWED_REPOSITORIES" or key.startswith("BRIDGE_ALLOWED_REPOSITORIES_"):
            monkeypatch.delenv(key, raising=False)


async def _sync(
    client, thread, snapshot, *, trigger_path, allowed_repo=REPO, monkeypatch, pr_author_login=None
):
    monkeypatch.setenv("COUNTDOWN_TRIGGER_PATH", str(trigger_path))
    if allowed_repo is not None:
        monkeypatch.setenv("BRIDGE_ALLOWED_REPOSITORIES", allowed_repo)
    return await _maybe_sync_stage_15(
        client=client,
        repository=REPO,
        threads=[thread],
        snapshots=[snapshot],
        pr=49,
        head_sha="head-sha-abc1234",
        dry_run=False,
        now=datetime.now(UTC).replace(microsecond=0),
        pr_author_login=pr_author_login,
    )


@pytest.mark.asyncio
async def test_resolved_normal_close_reply_touches_trigger(tmp_path, monkeypatch) -> None:
    trigger = tmp_path / "countdown-resolve-loop.trigger"
    client = _WritebackClient()

    actions = await _sync(
        client,
        _thread(Verdict.RESOLVED),
        _snapshot(viewer_can_resolve=True, verdict=Verdict.RESOLVED),
        trigger_path=trigger,
        monkeypatch=monkeypatch,
    )

    assert actions[0].result["in_thread_reply"]["posted"] is True
    assert trigger.exists()


@pytest.mark.asyncio
async def test_resolved_manual_close_reply_touches_trigger(tmp_path, monkeypatch) -> None:
    trigger = tmp_path / "countdown-resolve-loop.trigger"
    client = _WritebackClient()

    actions = await _sync(
        client,
        _thread(Verdict.RESOLVED),
        _snapshot(viewer_can_resolve=False),
        trigger_path=trigger,
        monkeypatch=monkeypatch,
    )

    assert actions[0].result["in_thread_reply"]["posted"] is True
    assert "<!-- clearance-manual-close:" in client.reply_calls[0][4]
    assert trigger.exists()


@pytest.mark.asyncio
async def test_resolved_delegated_resolver_reply_touches_trigger(tmp_path, monkeypatch) -> None:
    trigger = tmp_path / "countdown-resolve-loop.trigger"
    client = _WritebackClient()

    actions = await _sync(
        client,
        _thread(Verdict.RESOLVED),
        _snapshot(viewer_can_resolve=False),
        trigger_path=trigger,
        monkeypatch=monkeypatch,
        pr_author_login="iterwheel-assembly[bot]",
    )

    assert actions[0].result["fallback"] is True
    assert trigger.exists()


@pytest.mark.asyncio
async def test_open_verdict_does_not_touch_trigger(tmp_path, monkeypatch) -> None:
    trigger = tmp_path / "countdown-resolve-loop.trigger"
    client = _WritebackClient()

    await _sync(
        client,
        _thread(Verdict.OPEN),
        _snapshot(verdict=Verdict.OPEN),
        trigger_path=trigger,
        monkeypatch=monkeypatch,
    )

    assert not trigger.exists()


@pytest.mark.asyncio
async def test_needs_human_judgment_verdict_does_not_touch_trigger(tmp_path, monkeypatch) -> None:
    trigger = tmp_path / "countdown-resolve-loop.trigger"
    client = _WritebackClient()

    await _sync(
        client,
        _thread(Verdict.NEEDS_HUMAN_JUDGMENT),
        _snapshot(verdict=Verdict.NEEDS_HUMAN_JUDGMENT),
        trigger_path=trigger,
        monkeypatch=monkeypatch,
    )

    assert not trigger.exists()


@pytest.mark.asyncio
async def test_denied_repository_does_not_touch_trigger(tmp_path, monkeypatch) -> None:
    trigger = tmp_path / "countdown-resolve-loop.trigger"
    client = _WritebackClient()

    actions = await _sync(
        client,
        _thread(Verdict.RESOLVED),
        _snapshot(viewer_can_resolve=True, verdict=Verdict.RESOLVED),
        trigger_path=trigger,
        allowed_repo="some-org/other-repo",
        monkeypatch=monkeypatch,
    )

    assert actions[0].result["in_thread_reply"]["posted"] is True
    assert not trigger.exists()


@pytest.mark.asyncio
async def test_touch_failure_does_not_affect_writeback_result(tmp_path, monkeypatch) -> None:
    from voyager.bots.clearance import pipeline as pipeline_module

    def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline_module, "touch_trigger_file", _raise)
    monkeypatch.setenv("BRIDGE_ALLOWED_REPOSITORIES", REPO)
    client = _WritebackClient()

    actions = await _maybe_sync_stage_15(
        client=client,
        repository=REPO,
        threads=[_thread(Verdict.RESOLVED)],
        snapshots=[_snapshot(viewer_can_resolve=True, verdict=Verdict.RESOLVED)],
        pr=49,
        head_sha="head-sha-abc1234",
        dry_run=False,
        now=datetime.now(UTC).replace(microsecond=0),
    )

    assert actions[0].result["in_thread_reply"]["posted"] is True
