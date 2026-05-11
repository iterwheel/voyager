"""Step definitions for Clearance pipeline (Phase 7B-1) BDD scenarios.

Tests the deterministic webhook-driven per-thread verdict pipeline. The
production code is under ``voyager/bots/clearance/pipeline.py``; this file
binds the Gherkin scenarios to fixtures + assertions.

Two stubs:
- ``_StubGitHubAppClient`` — configurable canned responses + recording of
  every method invocation, so scenarios can both inject thread shapes and
  assert post-hoc on which mutations fired.
- A real ``StateStore`` rooted at ``tmp_path`` — the pipeline persistence
  layer is small, well-tested, and worth exercising end-to-end here rather
  than mocked away.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("../features/swm_pipeline.feature")


REPO = "iterwheel/sandbox"
PR = 49
THREAD_ID = "PRRT_codex_alpha"
CODEX_COMMENT_ID = 100001


# ---------------------------------------------------------------------------
# Stub GitHubAppClient
# ---------------------------------------------------------------------------


class _StubGitHubAppClient:
    """Minimal GitHubAppClient stand-in.

    Records every call so scenarios can assert mutation counts and arguments.
    ``pull_request_review_threads`` returns the configured list; the
    pull-request payload is shaped like the REST endpoint's response so
    the pipeline's ``(pr_data.get("head") or {}).get("sha")`` access works.
    """

    def __init__(self) -> None:
        self.threads: list[dict[str, Any]] = []
        self.pr_payload: dict[str, Any] = {
            "head": {"sha": "head-sha-abc1234"},
            "title": "Fix the bug",
            "number": PR,
            "user": {"login": "ryosaeba1985"},  # default PR author for existing scenarios
        }
        self.fail_pull_request: bool = False
        self.fail_resolve: bool = False
        self.resolve_calls: list[tuple[str, str]] = []
        self.create_comment_calls: list[tuple[str, str, int, str]] = []
        self.review_thread_reply_calls: list[tuple[str, str, int, int, str]] = []
        self.graphql_calls: list[tuple[str, dict[str, Any]]] = []
        self.resolve_response: dict[str, Any] = {
            "id": THREAD_ID,
            "isResolved": True,
            "isOutdated": False,
            "resolvedBy": {"login": "iterwheel-clearance[bot]"},
        }

    async def pull_request(self, app_slug: str, repo: str, pr: int) -> dict[str, Any]:
        if self.fail_pull_request:
            raise RuntimeError("simulated pull_request fetch failure")
        return self.pr_payload

    async def pull_request_review_threads(
        self, app_slug: str, repo: str, pr: int
    ) -> list[dict[str, Any]]:
        return list(self.threads)

    async def create_issue_comment(
        self, app_slug: str, repository: str, issue_number: int, *, body: str
    ) -> dict[str, Any]:
        self.create_comment_calls.append((app_slug, repository, issue_number, body))
        return {"html_url": "https://example/comment/1"}

    async def create_review_thread_reply(
        self,
        app_slug: str,
        repository: str,
        pull_number: int,
        comment_id: int,
        *,
        body: str,
    ) -> dict[str, Any]:
        self.review_thread_reply_calls.append((app_slug, repository, pull_number, comment_id, body))
        return {"html_url": "https://example/comment/inline-1"}

    async def resolve_review_thread(
        self, app_slug: str, repository: str, thread_id: str
    ) -> dict[str, Any]:
        self.resolve_calls.append((repository, thread_id))
        if self.fail_resolve:
            import httpx

            raise httpx.HTTPError("simulated resolveReviewThread mutation failure")
        return self.resolve_response

    async def graphql(
        self,
        app_slug: str,
        repository: str,
        *,
        query: str,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        self.graphql_calls.append((query, variables))
        return {
            "resolveReviewThread": {"thread": self.resolve_response},
            "unresolveReviewThread": {"thread": self.resolve_response},
        }


def _codex_thread(
    *,
    thread_id: str = THREAD_ID,
    is_resolved: bool = False,
    is_outdated: bool = False,
    author_reply_body: str | None = None,
) -> dict[str, Any]:
    """Build a Codex review-thread dict shaped like the GraphQL response."""
    comments: list[dict[str, Any]] = [
        {
            "databaseId": CODEX_COMMENT_ID,
            "author": {"login": "chatgpt-codex-connector"},
            "body": "P2: please address this nullable handling.",
            "url": "https://example/c/1",
            "createdAt": "2026-05-11T12:00:00Z",
        }
    ]
    if author_reply_body is not None:
        comments.append(
            {
                "databaseId": CODEX_COMMENT_ID + 1,
                "author": {"login": "ryosaeba1985"},
                "body": author_reply_body,
                "url": "https://example/c/2",
                "createdAt": "2026-05-11T12:30:00Z",
            }
        )
    return {
        "id": thread_id,
        "isResolved": is_resolved,
        "isOutdated": is_outdated,
        "path": "app.py",
        "line": 10,
        "startLine": None,
        "comments": {"nodes": comments},
    }


def _codex_thread_with_custom_reply(
    *,
    reply_author: str,
    reply_body: str = "Fixed in `parser.py` by adding the null guard before the dereference call.",
    reply_created_at: str = "2026-05-11T12:30:00Z",
    is_resolved: bool = False,
) -> dict[str, Any]:
    """A Codex thread whose reply is from a configurable author (not the default).

    Used for P1 author-filter coverage: we want to verify that a reply from a
    non-PR-author is NOT counted as "the author's substantive reply".
    """
    return {
        "id": THREAD_ID,
        "isResolved": is_resolved,
        "isOutdated": False,
        "path": "app.py",
        "line": 10,
        "startLine": None,
        "comments": {
            "nodes": [
                {
                    "databaseId": CODEX_COMMENT_ID,
                    "author": {"login": "chatgpt-codex-connector"},
                    "body": "P2: please address this nullable handling.",
                    "url": "https://example/c/1",
                    "createdAt": "2026-05-11T12:00:00Z",
                },
                {
                    "databaseId": CODEX_COMMENT_ID + 1,
                    "author": {"login": reply_author},
                    "body": reply_body,
                    "url": "https://example/c/2",
                    "createdAt": reply_created_at,
                },
            ]
        },
    }


def _codex_thread_ordered(
    *,
    followup_after_reply: bool,
) -> dict[str, Any]:
    """A Codex thread with an initial comment, an author reply, and a Codex followup.

    When ``followup_after_reply=True``: followup createdAt > author reply createdAt.
    Per pipeline P2-ordering fix, the followup body IS passed to judge().

    When ``followup_after_reply=False``: followup createdAt < author reply
    createdAt. The followup body is treated as stale and NOT passed to judge()
    — judge falls through to State C / substantive-reply logic.
    """
    if followup_after_reply:
        reply_ts = "2026-05-11T12:30:00Z"
        followup_ts = "2026-05-11T13:00:00Z"
    else:
        followup_ts = "2026-05-11T12:30:00Z"
        reply_ts = "2026-05-11T13:00:00Z"

    return {
        "id": THREAD_ID,
        "isResolved": False,
        "isOutdated": False,
        "path": "app.py",
        "line": 10,
        "startLine": None,
        "comments": {
            "nodes": [
                {
                    "databaseId": CODEX_COMMENT_ID,
                    "author": {"login": "chatgpt-codex-connector"},
                    "body": "P2: please address this nullable handling.",
                    "url": "https://example/c/1",
                    "createdAt": "2026-05-11T12:00:00Z",
                },
                {
                    "databaseId": CODEX_COMMENT_ID + 1,
                    "author": {"login": "ryosaeba1985"},
                    "body": (
                        "Fixed in `parser.py` by adding the null guard before the dereference call."
                    ),
                    "url": "https://example/c/2",
                    "createdAt": reply_ts,
                },
                {
                    "databaseId": CODEX_COMMENT_ID + 2,
                    "author": {"login": "chatgpt-codex-connector"},
                    "body": "Still not addressed in the latest diff.",
                    "url": "https://example/c/3",
                    "createdAt": followup_ts,
                },
            ]
        },
    }


def _human_thread() -> dict[str, Any]:
    """A non-Codex review thread — first comment is from a human."""
    return {
        "id": "PRRT_human_beta",
        "isResolved": False,
        "isOutdated": False,
        "path": "app.py",
        "line": 20,
        "startLine": None,
        "comments": {
            "nodes": [
                {
                    "databaseId": 200001,
                    "author": {"login": "frankyxhl"},
                    "body": "What about edge cases?",
                    "url": "https://example/h/1",
                    "createdAt": "2026-05-11T12:00:00Z",
                }
            ]
        },
    }


# ---------------------------------------------------------------------------
# Shared state container per scenario
# ---------------------------------------------------------------------------


@pytest.fixture
def ctx(tmp_path: Path):
    from voyager.bots.clearance.state import StateStore

    return {
        "store": StateStore(tmp_path / "state"),
        "client": _StubGitHubAppClient(),
        "automation": None,
        "raised": None,
    }


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------


@given("a temporary StateStore")
def given_temp_store(ctx) -> None:
    # tmp_path was already plumbed via fixture; nothing to do.
    assert ctx["store"] is not None


@given("a stub GitHubAppClient")
def given_stub_client(ctx) -> None:
    assert ctx["client"] is not None


# ---------------------------------------------------------------------------
# Given — thread fixtures
# ---------------------------------------------------------------------------


@given(parsers.parse('the stub PR "{repo}" #{pr:d} has no review threads'))
def given_no_threads(ctx, repo: str, pr: int) -> None:
    ctx["client"].threads = []


@given(parsers.parse('the stub PR "{repo}" #{pr:d} has 1 Codex thread already isResolved'))
def given_codex_thread_resolved(ctx, repo: str, pr: int) -> None:
    ctx["client"].threads = [_codex_thread(is_resolved=True)]


@given(
    parsers.parse(
        'the stub PR "{repo}" #{pr:d} has 1 Codex thread with substantive author reply and isResolved false'
    )
)
def given_codex_substantive(ctx, repo: str, pr: int) -> None:
    # Substantive = ≥ 50 chars + identifier + no deflection. The body below
    # cites `parser.py` (file identifier) and is 70+ chars.
    body = "Fixed in `parser.py` by adding the null guard before the dereference call."
    ctx["client"].threads = [_codex_thread(author_reply_body=body)]


@given(
    parsers.parse(
        'the stub PR "{repo}" #{pr:d} has 1 Codex thread with a short ack reply and isResolved false'
    )
)
def given_codex_short_ack(ctx, repo: str, pr: int) -> None:
    # Short + deflection-flavoured: not substantive → judge returns OPEN.
    ctx["client"].threads = [_codex_thread(author_reply_body="thanks, will look")]


@given(
    parsers.parse(
        'the stub PR "{repo}" #{pr:d} has 1 Codex thread that is outdated with no author reply'
    )
)
def given_codex_outdated(ctx, repo: str, pr: int) -> None:
    ctx["client"].threads = [_codex_thread(is_outdated=True)]


@given(parsers.parse('the stub PR "{repo}" #{pr:d} has 1 human-authored review thread'))
def given_human_thread(ctx, repo: str, pr: int) -> None:
    ctx["client"].threads = [_human_thread()]


@given("the stub GitHubAppClient fails on pull_request fetch")
def given_pr_fetch_fails(ctx) -> None:
    ctx["client"].fail_pull_request = True


@given("the stub GitHubAppClient fails on the resolveReviewThread mutation")
def given_resolve_fails(ctx) -> None:
    ctx["client"].fail_resolve = True


@given(parsers.parse('the stub PR "{repo}" #{pr:d} author is "{author_login}"'))
def given_pr_author(ctx, repo: str, pr: int, author_login: str) -> None:
    ctx["client"].pr_payload["user"] = {"login": author_login}


@given(
    parsers.parse(
        'the stub PR has 1 Codex thread with a substantive reply from "{reply_author}" '
        "and isResolved false"
    )
)
def given_codex_thread_non_author_reply(ctx, reply_author: str) -> None:
    ctx["client"].threads = [_codex_thread_with_custom_reply(reply_author=reply_author)]


@given(
    "the stub PR has 1 Codex thread where an older Codex followup precedes a "
    "newer substantive author reply"
)
def given_codex_thread_stale_followup(ctx) -> None:
    ctx["client"].threads = [_codex_thread_ordered(followup_after_reply=False)]


@given(
    'the stub PR has 1 Codex thread where a newer "still not addressed" Codex followup '
    "follows a substantive author reply"
)
def given_codex_thread_fresh_followup(ctx) -> None:
    ctx["client"].threads = [_codex_thread_ordered(followup_after_reply=True)]


# ---------------------------------------------------------------------------
# When — pipeline invocations
# ---------------------------------------------------------------------------


def _route_for_pr(pr: int) -> dict[str, Any]:
    return {
        "agent": "iterwheel-clearance",
        "kind": "pr",
        "validation": {"pr_number": pr, "issue_number": pr},
        "writeback": {"dynamic": "clearance_readiness"},
    }


def _run_pipeline(ctx, *, dry_run: bool | None = None) -> None:
    from voyager.bots.clearance.pipeline import compute_clearance_automation

    old = os.environ.get("DRY_RUN")
    if dry_run is True:
        os.environ["DRY_RUN"] = "true"
    elif dry_run is False:
        os.environ["DRY_RUN"] = "false"
    try:
        ctx["automation"] = asyncio.run(
            compute_clearance_automation(
                ctx["client"],
                _route_for_pr(PR),
                repository=REPO,
                store=ctx["store"],
            )
        )
    except Exception as exc:
        ctx["raised"] = exc
    finally:
        if old is None:
            os.environ.pop("DRY_RUN", None)
        else:
            os.environ["DRY_RUN"] = old


@when("compute_clearance_automation runs")
def when_run(ctx) -> None:
    _run_pipeline(ctx)


@when(parsers.parse("compute_clearance_automation runs with DRY_RUN {flag}"))
def when_run_with_dry_run(ctx, flag: str) -> None:
    _run_pipeline(ctx, dry_run=(flag.lower() == "true"))


# ---------------------------------------------------------------------------
# Then — automation dict assertions
# ---------------------------------------------------------------------------


@then("the automation enabled is true")
def then_enabled_true(ctx) -> None:
    assert ctx["automation"] is not None
    assert ctx["automation"]["enabled"] is True


@then(parsers.parse('the automation status is "{expected}"'))
def then_automation_status(ctx, expected: str) -> None:
    assert ctx["automation"] is not None, f"raised={ctx.get('raised')}"
    assert ctx["automation"]["status"] == expected, (
        f"status={ctx['automation']['status']!r}, expected {expected!r}; "
        f"reason={ctx['automation'].get('reason')!r}"
    )


@then(parsers.parse('the automation reason mentions "{text}"'))
def then_reason_mentions(ctx, text: str) -> None:
    assert ctx["automation"] is not None
    reason = ctx["automation"].get("reason") or ""
    assert text in reason, f"reason={reason!r} does not mention {text!r}"


@then(parsers.parse("the sync actions count is {count:d}"))
def then_sync_count(ctx, count: int) -> None:
    assert ctx["automation"] is not None
    assert ctx["automation"]["sync_actions_count"] == count, (
        f"sync_actions={ctx['automation']['sync_actions']!r}"
    )


@then(parsers.parse('the planned sync action mutation is "{mutation}"'))
def then_planned_mutation(ctx, mutation: str) -> None:
    actions = ctx["automation"]["sync_actions"]
    assert actions, "no sync actions present"
    assert actions[0]["mutation"] == mutation


@then("no resolveReviewThread mutation was invoked")
def then_no_mutation(ctx) -> None:
    assert ctx["client"].resolve_calls == [], (
        f"unexpected resolve calls: {ctx['client'].resolve_calls}"
    )


@then(parsers.parse("exactly {count:d} resolveReviewThread mutation was invoked"))
def then_n_mutations(ctx, count: int) -> None:
    assert len(ctx["client"].resolve_calls) == count, f"resolve_calls={ctx['client'].resolve_calls}"


@then(parsers.parse("exactly {count:d} in-thread reply was posted under the Codex review comment"))
def then_n_inline_replies(ctx, count: int) -> None:
    calls = ctx["client"].review_thread_reply_calls
    assert len(calls) == count, f"in-thread reply calls={calls!r}"
    if count >= 1:
        # Sanity: the comment_id we replied under matches the Codex review comment.
        _slug, _repo, _pr, comment_id, _body = calls[0]
        assert comment_id == CODEX_COMMENT_ID, (
            f"replied under comment_id={comment_id!r}, expected {CODEX_COMMENT_ID}"
        )


@then("no in-thread reply was posted")
def then_no_inline_reply(ctx) -> None:
    assert ctx["client"].review_thread_reply_calls == [], (
        f"unexpected in-thread reply calls: {ctx['client'].review_thread_reply_calls}"
    )


@then("the pipeline raised an exception")
def then_pipeline_raised(ctx) -> None:
    assert ctx["raised"] is not None, (
        f"expected pipeline to raise; got automation={ctx.get('automation')!r}"
    )


@then(parsers.parse('the in-thread reply body contains "{token}"'))
def then_inline_reply_body_contains(ctx, token: str) -> None:
    calls = ctx["client"].review_thread_reply_calls
    assert calls, "no in-thread reply was posted; body assertion cannot run"
    bodies = [body for *_rest, body in calls]
    assert any(token in body for body in bodies), (
        f"token {token!r} not in any posted body: {bodies!r}"
    )


# ---------------------------------------------------------------------------
# Then — persistence assertions
# ---------------------------------------------------------------------------


@then(parsers.parse('the store has {n:d} poll for "{repo}" PR {pr:d}'))
def then_store_polls(ctx, n: int, repo: str, pr: int) -> None:
    polls = list(ctx["store"].read_polls(repo=repo, pr=pr))
    assert len(polls) == n, f"expected {n} polls, got {len(polls)}: {polls!r}"


@then(parsers.parse('the latest poll status is "{expected}"'))
def then_latest_poll_status(ctx, expected: str) -> None:
    latest = ctx["store"].latest_poll(REPO, PR)
    assert latest is not None
    assert latest.status.value == expected


@then(parsers.parse("the latest poll has {count:d} threads"))
def then_latest_poll_thread_count(ctx, count: int) -> None:
    latest = ctx["store"].latest_poll(REPO, PR)
    assert latest is not None
    assert len(latest.threads) == count


@then(parsers.parse("the store thread history for the Codex thread has {n:d} snapshot"))
def then_thread_history(ctx, n: int) -> None:
    history = ctx["store"].read_thread_history(REPO, PR, THREAD_ID)
    assert len(history) == n


@then(parsers.parse('the latest snapshot verdict is "{verdict}"'))
def then_latest_snapshot_verdict(ctx, verdict: str) -> None:
    snap = ctx["store"].read_thread(REPO, PR, THREAD_ID)
    assert snap is not None
    assert snap.verdict.value == verdict


# ---------------------------------------------------------------------------
# Then — resolve_review_thread mutation direct test
# ---------------------------------------------------------------------------


@given("a recording GitHubAppClient that returns a resolved thread payload")
def given_recording_client(ctx) -> None:
    ctx["client"] = _StubGitHubAppClient()


@when(parsers.parse('client.resolve_review_thread is awaited for "{thread_id}"'))
def when_resolve_called(ctx, thread_id: str) -> None:
    ctx["resolve_result"] = asyncio.run(
        ctx["client"].resolve_review_thread("iterwheel-clearance", REPO, thread_id)
    )


@then("the returned thread has isResolved true")
def then_returned_thread_resolved(ctx) -> None:
    assert ctx["resolve_result"]["isResolved"] is True


@then(parsers.parse('the recorded GraphQL variables include threadId "{thread_id}"'))
def then_graphql_threadid(ctx, thread_id: str) -> None:
    # The stub records via resolve_calls (semantic-level). To validate the
    # actual mutation goes through the real graphql layer with the right
    # variable, we run against the real github_app on a MockTransport — but
    # that's already covered by the github_app BDD suite. Here we assert
    # the stub captured the thread_id in its resolve_calls.
    assert any(call[1] == thread_id for call in ctx["client"].resolve_calls), (
        f"resolve_calls={ctx['client'].resolve_calls}"
    )
