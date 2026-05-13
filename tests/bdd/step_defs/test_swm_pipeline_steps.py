"""Step definitions for Clearance pipeline (Phase 7B-1 + 7B-3) BDD scenarios.

Tests the deterministic webhook-driven per-thread verdict pipeline and the
Wave 7B-3 investigator augmentation path. Production code lives under
``voyager/bots/clearance/pipeline.py``; this file binds Gherkin scenarios to
fixtures + assertions.

Stubs:
- ``_StubGitHubAppClient`` — configurable canned responses + recording of
  every method invocation, so scenarios can both inject thread shapes and
  assert post-hoc on which mutations fired.
- ``_FakeInvestigator`` — in-memory ThreadInvestigator Protocol double. Returns
  canned InvestigationDecisions (or raises InvestigationError) with zero network.
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

    # Wave 7B-3: pull_request_diff — optional diff text + call counter for
    # lazy-memoize scenarios. When diff_raise is set, pull_request_diff raises
    # that exception instead of returning diff_text.
    diff_text: str = ""
    diff_call_count: int = 0
    diff_raise: BaseException | None = None

    async def pull_request(self, app_slug: str, repo: str, pr: int) -> dict[str, Any]:
        if self.fail_pull_request:
            raise RuntimeError("simulated pull_request fetch failure")
        return self.pr_payload

    async def pull_request_diff(self, app_slug: str, repo: str, pull_number: int) -> str:
        self.diff_call_count += 1
        if self.diff_raise is not None:
            raise self.diff_raise
        return self.diff_text

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


def _outdated_codex_thread(
    *,
    thread_id: str = THREAD_ID,
    path: str = "app.py",
    line: int = 10,
    codex_comment_id: int = CODEX_COMMENT_ID,
) -> dict[str, Any]:
    """A State B (isOutdated=True) Codex thread with no author reply."""
    return {
        "id": thread_id,
        "isResolved": False,
        "isOutdated": True,
        "path": path,
        "line": line,
        "startLine": None,
        "comments": {
            "nodes": [
                {
                    "databaseId": codex_comment_id,
                    "author": {"login": "chatgpt-codex-connector"},
                    "body": "P2: the null dereference on line 10 is not guarded.",
                    "url": "https://example/c/1",
                    "createdAt": "2026-05-11T12:00:00Z",
                }
            ]
        },
    }


def _fresh_codex_thread(
    *,
    thread_id: str = THREAD_ID,
    path: str = "app.py",
) -> dict[str, Any]:
    """A State A (not outdated, no replies) Codex thread."""
    return {
        "id": thread_id,
        "isResolved": False,
        "isOutdated": False,
        "path": path,
        "line": 10,
        "startLine": None,
        "comments": {
            "nodes": [
                {
                    "databaseId": CODEX_COMMENT_ID,
                    "author": {"login": "chatgpt-codex-connector"},
                    "body": "P2: the null dereference on line 10 is not guarded.",
                    "url": "https://example/c/1",
                    "createdAt": "2026-05-11T12:00:00Z",
                }
            ]
        },
    }


# Minimal unified diff that contains app.py at line 10. Used by 7B-3 investigator
# scenarios so that extract_anchor_excerpt returns a non-empty excerpt.
_SAMPLE_DIFF_APP_PY = """\
diff --git a/app.py b/app.py
index abc1234..def5678 100644
--- a/app.py
+++ b/app.py
@@ -8,6 +8,10 @@ def login(user):
     token = generate_token(user)
     return token
+def logout(user):
+    if user is None:
+        return None
+    return invalidate_token(user)
"""

# Unified diff covering both app.py (line 10) and lib/utils.py (line 5).
# Used by Scenario C to prove investigator receives different excerpts per path.
_SAMPLE_DIFF_TWO_PATHS = """\
diff --git a/app.py b/app.py
index abc1234..def5678 100644
--- a/app.py
+++ b/app.py
@@ -8,6 +8,10 @@ def login(user):
     token = generate_token(user)
     return token
+def logout(user):
+    if user is None:
+        return None
+    return invalidate_token(user)
diff --git a/lib/utils.py b/lib/utils.py
index 111aaaa..222bbbb 100644
--- a/lib/utils.py
+++ b/lib/utils.py
@@ -3,4 +3,8 @@ def helper():
     pass
+def safe_divide(a, b):
+    if b == 0:
+        raise ValueError("division by zero")
+    return a / b
"""


# ---------------------------------------------------------------------------
# Wave 7B-3: FakeInvestigator — in-memory ThreadInvestigator Protocol double
# ---------------------------------------------------------------------------


class _FakeInvestigator:
    """Test double for ThreadInvestigator Protocol. Returns canned decisions."""

    max_diff_chars = 20000

    def __init__(
        self,
        decisions: list[Any] | Any,
    ) -> None:
        # decisions may be a list of InvestigationDecision, or an InvestigationError
        # instance to raise, or a list that may contain an error sentinel.
        self._decisions = decisions
        self.calls: list[Any] = []

    async def investigate(self, item: Any) -> Any:
        from voyager.bots.clearance.investigator import InvestigationError

        self.calls.append(item)
        if isinstance(self._decisions, InvestigationError):
            raise self._decisions
        if isinstance(self._decisions, list):
            if not self._decisions:
                raise AssertionError("_FakeInvestigator: no more canned decisions")
            return self._decisions.pop(0)
        return self._decisions


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
        "investigator": None,  # Wave 7B-3: set by Given steps for investigator scenarios
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
                investigator=ctx.get("investigator"),
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


# ---------------------------------------------------------------------------
# Wave 7B-3: Given — investigator + diff setup
# ---------------------------------------------------------------------------


@given("no investigator is configured")
def given_no_investigator(ctx) -> None:
    ctx["investigator"] = None


@given(
    parsers.parse(
        'the stub PR "{repo}" #{pr:d} has 1 outdated Codex thread at path "{path}" line {line:d}'
    )
)
def given_outdated_codex_thread(ctx, repo: str, pr: int, path: str, line: int) -> None:
    ctx["client"].threads = [_outdated_codex_thread(path=path, line=line)]


@given(
    parsers.parse(
        'the stub PR "{repo}" #{pr:d} has 1 fresh Codex thread (State A) at path "{path}"'
    )
)
def given_fresh_codex_thread(ctx, repo: str, pr: int, path: str) -> None:
    ctx["client"].threads = [_fresh_codex_thread(path=path)]


@given(
    parsers.parse(
        'the stub PR "{repo}" #{pr:d} has 2 outdated Codex threads at path "{path}" line {line:d}'
    )
)
def given_two_outdated_codex_threads(ctx, repo: str, pr: int, path: str, line: int) -> None:
    ctx["client"].threads = [
        _outdated_codex_thread(thread_id="PRRT_codex_alpha", path=path, line=line),
        _outdated_codex_thread(
            thread_id="PRRT_codex_beta",
            path=path,
            line=line,
            codex_comment_id=CODEX_COMMENT_ID + 10,
        ),
    ]


@given(
    parsers.parse(
        'a fake investigator returning verdict "{verdict}" confidence {confidence:g} reason "{reason}"'
    )
)
def given_fake_investigator(ctx, verdict: str, confidence: float, reason: str) -> None:
    from voyager.bots.clearance.investigator import InvestigationDecision

    decision = InvestigationDecision(
        verdict=verdict,  # type: ignore[arg-type]
        confidence=confidence,
        reason=reason,
        evidence=[],
        raw_text=None,
    )
    ctx["investigator"] = _FakeInvestigator([decision])


@given(
    parsers.parse(
        'a fake investigator returning verdict "{verdict}" confidence {confidence:g} reason "{reason}" for each thread'
    )
)
def given_fake_investigator_multi(ctx, verdict: str, confidence: float, reason: str) -> None:
    from voyager.bots.clearance.investigator import InvestigationDecision

    decisions = [
        InvestigationDecision(
            verdict=verdict,  # type: ignore[arg-type]
            confidence=confidence,
            reason=reason,
            evidence=[],
            raw_text=None,
        ),
        InvestigationDecision(
            verdict=verdict,  # type: ignore[arg-type]
            confidence=confidence,
            reason=reason,
            evidence=[],
            raw_text=None,
        ),
    ]
    ctx["investigator"] = _FakeInvestigator(decisions)


@given(parsers.parse('a fake investigator that raises InvestigationError "{message}"'))
def given_fake_investigator_error(ctx, message: str) -> None:
    from voyager.bots.clearance.investigator import InvestigationError

    ctx["investigator"] = _FakeInvestigator(InvestigationError(message))


@given(parsers.parse('the stub client returns a sample diff for "{path}"'))
def given_stub_diff(ctx, path: str) -> None:
    ctx["client"].diff_text = _SAMPLE_DIFF_APP_PY


@given("the stub client records pull_request_diff calls")
def given_stub_records_diff_calls(ctx) -> None:
    ctx["client"].diff_text = _SAMPLE_DIFF_APP_PY


# ---------------------------------------------------------------------------
# Wave 7B-3: new exception-path + multi-path Given steps
# ---------------------------------------------------------------------------


@given("the stub client raises httpx.HTTPStatusError on pull_request_diff")
def given_stub_diff_raises_httpx(ctx) -> None:
    import httpx

    ctx["client"].diff_raise = httpx.HTTPStatusError(
        "server 500",
        request=httpx.Request("GET", "https://example.com/diff"),
        response=httpx.Response(500),
    )


@given(parsers.parse('a fake investigator returning unknown verdict "{verdict}"'))
def given_fake_investigator_unknown_verdict(ctx, verdict: str) -> None:
    from voyager.bots.clearance.investigator import InvestigationDecision

    decision = InvestigationDecision(
        verdict=verdict,  # type: ignore[arg-type]
        confidence=0.9,
        reason="Unknown verdict test",
        evidence=[],
        raw_text=None,
    )
    ctx["investigator"] = _FakeInvestigator([decision])


@given('the stub PR "iterwheel/sandbox" #49 has 2 outdated Codex threads at different paths')
def given_two_outdated_threads_different_paths(ctx) -> None:
    ctx["client"].threads = [
        _outdated_codex_thread(
            thread_id="PRRT_codex_alpha",
            path="app.py",
            line=10,
            codex_comment_id=CODEX_COMMENT_ID,
        ),
        _outdated_codex_thread(
            thread_id="PRRT_codex_beta",
            path="lib/utils.py",
            line=5,
            codex_comment_id=CODEX_COMMENT_ID + 10,
        ),
    ]


@given("the stub client returns a sample diff covering both paths")
def given_stub_diff_both_paths(ctx) -> None:
    ctx["client"].diff_text = _SAMPLE_DIFF_TWO_PATHS


# ---------------------------------------------------------------------------
# Wave 7B-3: When — pipeline with investigator
# ---------------------------------------------------------------------------


@when("compute_clearance_automation runs with investigator")
def when_run_with_investigator(ctx) -> None:
    _run_pipeline(ctx, dry_run=True)


@when("compute_clearance_automation runs with investigator and DRY_RUN false")
def when_run_with_investigator_dry_run_false(ctx) -> None:
    _run_pipeline(ctx, dry_run=False)


# ---------------------------------------------------------------------------
# Wave 7B-3: Then — investigator outcome assertions
# ---------------------------------------------------------------------------


def _first_thread(ctx) -> Any:
    """Return the first Thread model from the latest poll record."""
    latest = ctx["store"].latest_poll(REPO, PR)
    assert latest is not None, "no poll record found in store"
    assert latest.threads, "poll record has no threads"
    return latest.threads[0]


@then(parsers.parse('the thread verdict is "{verdict}"'))
def then_thread_verdict(ctx, verdict: str) -> None:
    t = _first_thread(ctx)
    assert t.verdict.value == verdict, f"thread.verdict={t.verdict!r}, expected {verdict!r}"


@then("the thread llm_verdict is None")
def then_llm_verdict_none(ctx) -> None:
    t = _first_thread(ctx)
    assert t.llm_verdict is None, f"expected llm_verdict=None, got {t.llm_verdict!r}"


@then(parsers.parse('the thread llm_verdict is "{expected}"'))
def then_llm_verdict(ctx, expected: str) -> None:
    t = _first_thread(ctx)
    assert t.llm_verdict == expected, f"thread.llm_verdict={t.llm_verdict!r}, expected {expected!r}"


@then(parsers.parse("the thread llm_confidence is {expected:g}"))
def then_llm_confidence(ctx, expected: float) -> None:
    t = _first_thread(ctx)
    assert t.llm_confidence is not None, "expected llm_confidence to be set, got None"
    assert abs(t.llm_confidence - expected) < 1e-6, (
        f"thread.llm_confidence={t.llm_confidence!r}, expected {expected!r}"
    )


@then(parsers.parse('the thread llm_reason contains "{substring}"'))
def then_llm_reason_contains(ctx, substring: str) -> None:
    t = _first_thread(ctx)
    assert t.llm_reason is not None, "expected llm_reason to be set, got None"
    assert substring in t.llm_reason, (
        f"thread.llm_reason={t.llm_reason!r} does not contain {substring!r}"
    )


@then(parsers.parse('the pipeline trigger is "{expected}"'))
def then_pipeline_trigger(ctx, expected: str) -> None:
    latest = ctx["store"].latest_poll(REPO, PR)
    assert latest is not None, "no poll record found in store"
    assert latest.trigger == expected, f"poll.trigger={latest.trigger!r}, expected {expected!r}"


@then(parsers.parse('the pipeline trigger contains "{substring}"'))
def then_pipeline_trigger_contains(ctx, substring: str) -> None:
    latest = ctx["store"].latest_poll(REPO, PR)
    assert latest is not None, "no poll record found in store"
    assert substring in latest.trigger, (
        f"poll.trigger={latest.trigger!r} does not contain {substring!r}"
    )


@then("the investigator was never called")
def then_investigator_never_called(ctx) -> None:
    inv = ctx.get("investigator")
    assert inv is not None, "no investigator configured in ctx"
    assert inv.calls == [], f"expected 0 investigator calls, got {len(inv.calls)}: {inv.calls!r}"


@then(parsers.parse("the investigator was called {count:d} times"))
def then_investigator_called_n_times(ctx, count: int) -> None:
    inv = ctx.get("investigator")
    assert inv is not None, "no investigator configured in ctx"
    assert len(inv.calls) == count, f"expected {count} investigator calls, got {len(inv.calls)}"


@then(parsers.parse("pull_request_diff was called {count:d} time"))
def then_diff_called_once(ctx, count: int) -> None:
    assert ctx["client"].diff_call_count == count, (
        f"expected pull_request_diff called {count}x, got {ctx['client'].diff_call_count}"
    )


@then(parsers.parse("pull_request_diff was called {count:d} times"))
def then_diff_called_n_times(ctx, count: int) -> None:
    assert ctx["client"].diff_call_count == count, (
        f"expected pull_request_diff called {count}x, got {ctx['client'].diff_call_count}"
    )


@then("the investigator received different diff excerpts for each thread")
def then_investigator_different_excerpts(ctx) -> None:
    inv = ctx.get("investigator")
    assert inv is not None, "no investigator configured in ctx"
    assert len(inv.calls) == 2, f"expected 2 investigator calls, got {len(inv.calls)}"
    excerpt_0 = inv.calls[0].diff_excerpt
    excerpt_1 = inv.calls[1].diff_excerpt
    assert excerpt_0 != excerpt_1, (
        f"expected different diff excerpts per thread but both are:\n{excerpt_0!r}"
    )


# ---------------------------------------------------------------------------
# Wave 7B-3 hardening #5: investigator failure-mode contract — Given steps
# ---------------------------------------------------------------------------


@given(
    parsers.parse('a fake investigator that raises InvestigationError for all threads "{message}"')
)
def given_fake_investigator_error_multi(ctx, message: str) -> None:
    """A _FakeInvestigator that raises InvestigationError on every call."""
    from voyager.bots.clearance.investigator import InvestigationError

    class _AlwaysErrorInvestigator:
        max_diff_chars = 20000

        def __init__(self, msg: str) -> None:
            self._msg = msg
            self.calls: list[Any] = []

        async def investigate(self, item: Any) -> Any:
            self.calls.append(item)
            raise InvestigationError(self._msg)

    ctx["investigator"] = _AlwaysErrorInvestigator(message)


# ---------------------------------------------------------------------------
# Wave 7B-3 hardening #5: investigator failure-mode contract — Then steps
# ---------------------------------------------------------------------------


@then(parsers.parse("the automation investigator_error_count is {count:d}"))
def then_automation_investigator_error_count(ctx, count: int) -> None:
    auto = ctx["automation"]
    assert auto is not None, f"raised={ctx.get('raised')}"
    assert "investigator_error_count" in auto, (
        f"investigator_error_count absent from automation keys: {list(auto.keys())}"
    )
    assert auto["investigator_error_count"] == count, (
        f"investigator_error_count={auto['investigator_error_count']!r}, expected {count}"
    )


@then(parsers.parse('the automation investigator_error_thread_ids contains "{thread_id}"'))
def then_automation_investigator_error_thread_ids_contains(ctx, thread_id: str) -> None:
    auto = ctx["automation"]
    assert auto is not None, f"raised={ctx.get('raised')}"
    ids = auto.get("investigator_error_thread_ids", [])
    assert thread_id in ids, f"thread_id {thread_id!r} not in investigator_error_thread_ids={ids!r}"


@then(parsers.parse('the automation investigator_error_reason contains "{substring}"'))
def then_automation_investigator_error_reason_contains(ctx, substring: str) -> None:
    auto = ctx["automation"]
    assert auto is not None, f"raised={ctx.get('raised')}"
    reason = auto.get("investigator_error_reason") or ""
    assert substring in reason, (
        f"substring {substring!r} not in investigator_error_reason={reason!r}"
    )


@then("the automation has no investigator_error_fields")
def then_automation_has_no_investigator_error_fields(ctx) -> None:
    auto = ctx["automation"]
    assert auto is not None, f"raised={ctx.get('raised')}"
    for key in (
        "investigator_error_count",
        "investigator_error_thread_ids",
        "investigator_error_reason",
    ):
        assert key not in auto, f"expected {key!r} to be absent, but found value {auto[key]!r}"


@then(
    parsers.parse('the snapshot evidence llm_error for thread "{thread_id}" contains "{substring}"')
)
def then_snapshot_evidence_llm_error_contains(ctx, thread_id: str, substring: str) -> None:
    snap = ctx["store"].read_thread(REPO, PR, thread_id)
    assert snap is not None, f"no snapshot found for thread_id={thread_id!r}"
    llm_error = snap.evidence.llm_error
    assert llm_error is not None, "expected evidence.llm_error to be set, got None"
    assert llm_error != "", f"expected evidence.llm_error to be non-empty, got {llm_error!r}"
    assert substring.lower() in llm_error.lower(), (
        f"substring {substring!r} not in evidence.llm_error={llm_error!r}"
    )
