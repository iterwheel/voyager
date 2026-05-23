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
import logging
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
            "head": {
                "sha": "head-sha-abc1234",
                "repo": {"full_name": "iterwheel/sandbox"},
            },
            "base": {
                "ref": "main",
                "repo": {"full_name": "iterwheel/sandbox"},
            },
            "title": "Fix the bug",
            "number": PR,
            "user": {"login": "ryosaeba1985"},  # default PR author for existing scenarios
            # Issue #63: pushed_at defaults to None, so stale detection is off
            # unless a scenario explicitly sets it.
        }
        self.pr_payload_second_fetch: dict[str, Any] | None = None  # R5-P2: second-call head SHA
        self.fail_pull_request: bool = False
        self.fail_pull_request_httpx: bool = False  # Wave 7C-6: raises httpx.HTTPError
        self.pull_request_call_count: int = 0  # Wave 7C-6: tracks guard fetch calls
        self.fail_resolve: bool = False
        self.fail_resolve_http_error: bool = False  # CHG-1813: httpx.HTTPError (non-status)
        self.fail_resolve_status_error: int | None = None  # CHG-1813: HTTPStatusError with code
        self.fail_resolve_graphql_error: bool = False  # CHG-1813: GitHubGraphQLError
        self.fail_resolve_timeout: bool = False  # CHG-1813: builtin TimeoutError
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

        # Wave 7C-3: branch_protected stub controls
        self._branch_protected_result: bool = True
        self._branch_protected_raise: BaseException | None = None

        # Issue #62: fork PR head-repo accessibility stub
        self._head_repo_accessible: bool = True  # default: head repo IS accessible
        self._head_repo_check_call_count: int = 0
        self._head_repo_check_calls: list[str] = []

    # Wave 7B-3: pull_request_diff — optional diff text + call counter for
    # lazy-memoize scenarios. When diff_raise is set, pull_request_diff raises
    # that exception instead of returning diff_text.
    diff_text: str = ""
    diff_call_count: int = 0
    diff_raise: BaseException | None = None

    async def pull_request(self, app_slug: str, repo: str, pr: int) -> dict[str, Any]:
        self.pull_request_call_count += 1
        if self.fail_pull_request:
            raise RuntimeError("simulated pull_request fetch failure")
        if self.fail_pull_request_httpx:
            import httpx as _httpx

            raise _httpx.HTTPError("simulated httpx transport error")
        # R5-P2: return a different head SHA on the second fetch if configured
        if self.pull_request_call_count >= 2 and self.pr_payload_second_fetch is not None:
            payload = dict(self.pr_payload)
            payload["head"] = self.pr_payload_second_fetch
            return payload
        return self.pr_payload

    async def pull_request_diff(self, app_slug: str, repo: str, pull_number: int) -> str:
        self.diff_call_count += 1
        if self.diff_raise is not None:
            raise self.diff_raise
        return self.diff_text

    async def branch_protected(self, app_slug: str, repo: str, branch: str) -> bool:
        if self._branch_protected_raise is not None:
            raise self._branch_protected_raise
        return self._branch_protected_result

    async def check_head_repo_accessible(self, app_slug: str, head_repo: str) -> bool:
        self._head_repo_check_call_count += 1
        self._head_repo_check_calls.append(head_repo)
        # Cache simulation: once False, stays False (matching production behavior)
        return self._head_repo_accessible

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
        if self.fail_resolve_http_error:
            import httpx

            raise httpx.HTTPError("simulated transport-level resolveReviewThread failure")
        if self.fail_resolve_status_error is not None:
            import httpx

            raise httpx.HTTPStatusError(
                "FORBIDDEN",
                request=httpx.Request("POST", "https://api.github.com/graphql"),
                response=httpx.Response(self.fail_resolve_status_error),
            )
        if self.fail_resolve_graphql_error:
            from voyager.core.github_app import GitHubGraphQLError

            raise GitHubGraphQLError(
                [{"type": "FORBIDDEN", "message": "Resource not accessible by integration"}]
            )
        if self.fail_resolve_timeout:
            raise TimeoutError("simulated resolveReviewThread timeout")
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
            "body": "**P1** please address this nullable handling.",
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
                    "body": "**P1** please address this nullable handling.",
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
                    "body": "**P1** please address this nullable handling.",
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
                    "body": "**P1** the null dereference on line 10 is not guarded.",
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
                    "body": "**P1** the null dereference on line 10 is not guarded.",
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
        "captured_logs": [],  # Wave 7C-3: warning/info logs captured during pipeline run
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


@given("the stub GitHubAppClient fails on the resolveReviewThread mutation with an HTTP error")
def given_resolve_fails_http_error(ctx) -> None:
    ctx["client"].fail_resolve_http_error = True


@given(
    parsers.parse(
        "the stub GitHubAppClient fails on the resolveReviewThread mutation with HTTPStatusError {status:d}"
    )
)
def given_resolve_fails_status_error(ctx, status: int) -> None:
    ctx["client"].fail_resolve_status_error = status


@given("the stub GitHubAppClient fails on the resolveReviewThread mutation with a GraphQL error")
def given_resolve_fails_graphql_error(ctx) -> None:
    ctx["client"].fail_resolve_graphql_error = True


@given("the stub GitHubAppClient fails on the resolveReviewThread mutation with a TimeoutError")
def given_resolve_fails_timeout(ctx) -> None:
    ctx["client"].fail_resolve_timeout = True


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


class _CapturingHandler(logging.Handler):
    """Capture log records at DEBUG and above."""

    def __init__(self, sink: list[logging.LogRecord]) -> None:
        super().__init__(level=logging.DEBUG)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self._sink.append(record)


def _run_pipeline(ctx, *, dry_run: bool | None = None) -> None:
    from voyager.bots.clearance.pipeline import compute_clearance_automation

    old = os.environ.get("DRY_RUN")
    if dry_run is True:
        os.environ["DRY_RUN"] = "true"
    elif dry_run is False:
        os.environ["DRY_RUN"] = "false"

    log_records: list[logging.LogRecord] = []
    handler = _CapturingHandler(log_records)
    pipeline_logger = logging.getLogger("voyager.bots.clearance.pipeline")
    old_level = pipeline_logger.level
    pipeline_logger.setLevel(logging.DEBUG)
    pipeline_logger.addHandler(handler)
    try:
        ctx["automation"] = asyncio.run(
            compute_clearance_automation(
                ctx["client"],
                _route_for_pr(PR),
                repository=REPO,
                store=ctx["store"],
                investigator=ctx.get("investigator"),
                expected_sha=ctx.get("webhook_expected_sha"),
            )
        )
    except Exception as exc:
        ctx["raised"] = exc
    finally:
        pipeline_logger.removeHandler(handler)
        pipeline_logger.setLevel(old_level)
        ctx["captured_logs"] = log_records
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


# ---------------------------------------------------------------------------
# CHG-1813: Stage 1.5 writeback failure capture — Then steps
# ---------------------------------------------------------------------------


@then("the automation has writeback failure metadata")
def then_automation_has_writeback_failure(ctx) -> None:
    auto = ctx["automation"]
    assert auto is not None, f"raised={ctx.get('raised')}"
    assert "writeback_failures" in auto, (
        f"writeback_failures absent from automation keys: {list(auto.keys())}"
    )
    assert auto["writeback_failure_count"] >= 1, (
        f"writeback_failure_count={auto.get('writeback_failure_count')!r}"
    )


@then("the automation has no writeback failure metadata")
def then_automation_has_no_writeback_failure(ctx) -> None:
    auto = ctx["automation"]
    assert auto is not None, f"raised={ctx.get('raised')}"
    for key in ("writeback_failures", "writeback_failure_count", "writeback_failure_reason"):
        assert key not in auto, f"expected {key!r} to be absent, but found value {auto[key]!r}"


@then(parsers.parse("the automation writeback failure count is {count:d}"))
def then_automation_writeback_failure_count(ctx, count: int) -> None:
    auto = ctx["automation"]
    assert auto is not None, f"raised={ctx.get('raised')}"
    assert auto.get("writeback_failure_count") == count, (
        f"writeback_failure_count={auto.get('writeback_failure_count')!r}, expected {count}"
    )


@then(parsers.parse('the automation writeback failure reason starts with "{prefix}"'))
def then_automation_writeback_failure_reason_starts_with(ctx, prefix: str) -> None:
    auto = ctx["automation"]
    assert auto is not None, f"raised={ctx.get('raised')}"
    reason = auto.get("writeback_failure_reason") or ""
    assert reason.startswith(prefix), (
        f"writeback_failure_reason={reason!r} does not start with {prefix!r}"
    )


@then(parsers.parse('the Stage 1.5 action result has applied false with operation "{operation}"'))
def then_stage15_action_failed(ctx, operation: str) -> None:
    auto = ctx["automation"]
    assert auto is not None, f"raised={ctx.get('raised')}"
    actions = auto.get("sync_actions") or []
    failed = [a for a in actions if (a.get("result") or {}).get("applied") is False]
    assert failed, f"no failed Stage 1.5 actions found in sync_actions: {actions!r}"
    result = failed[0]["result"]
    assert result.get("applied") is False, f"expected applied=False, got {result.get('applied')!r}"
    assert result.get("operation") == operation, (
        f"expected operation={operation!r}, got {result.get('operation')!r}"
    )


@then("the thread GitHub state was not mutated")
def then_thread_github_state_was_not_mutated(ctx) -> None:
    """Assert no thread was resolved in GitHub state (snapshots/threads unchanged)."""
    auto = ctx["automation"]
    assert auto is not None, f"raised={ctx.get('raised')}"
    # Check the latest snapshot to ensure github_state.isResolved is still False
    snap = ctx["store"].read_thread(REPO, PR, THREAD_ID)
    if snap and snap.github_state:
        assert snap.github_state.isResolved is False, (
            f"Expected isResolved=False, got {snap.github_state.isResolved!r}"
        )


@then(parsers.parse('the Stage 1.5 action result error_class is "{expected_class}"'))
def then_stage15_action_error_class(ctx, expected_class: str) -> None:
    auto = ctx["automation"]
    assert auto is not None, f"raised={ctx.get('raised')}"
    actions = auto.get("sync_actions") or []
    failed = [a for a in actions if (a.get("result") or {}).get("applied") is False]
    assert failed, f"no failed Stage 1.5 actions found in sync_actions: {actions!r}"
    result = failed[0]["result"]
    assert result.get("error_class") == expected_class, (
        f"expected error_class={expected_class!r}, got {result.get('error_class')!r}"
    )


# ---------------------------------------------------------------------------
# Wave 7C-3: severity evaluator wiring — helpers + Given / Then steps
# ---------------------------------------------------------------------------

# Codex comment body with P1 badge + required_check_coupling cue
# (contains "required", "check", and "paths-ignore")
_P1_COUPLING_BODY = (
    "![P1 Badge](https://img.shields.io/badge/P1-critical-red)\n"
    "This workflow uses `required` status checks that are excluded via `paths-ignore`.\n"
    "The `check` runs will be skipped for changes to docs/ and the branch protection\n"
    "rule requires them — this coupling means a failing check can silently bypass.\n"
)

# P2 badge, no coupling cue
_P2_NO_COUPLING_BODY = "**P2**: Nullable dereference on line 42 — guard before calling `.value`.\n"

# No badge at all
_NO_BADGE_BODY = (
    "The function does not handle empty input — add an early return for the empty-list case.\n"
)


def _severity_codex_thread(
    *,
    thread_id: str = THREAD_ID,
    comment_body: str,
    is_resolved: bool = False,
    is_outdated: bool = False,
    author_reply_body: str | None = None,
) -> dict[str, Any]:
    """Build a Codex thread with a configurable first-comment body for severity extraction."""
    comments: list[dict[str, Any]] = [
        {
            "databaseId": CODEX_COMMENT_ID,
            "author": {"login": "chatgpt-codex-connector"},
            "body": comment_body,
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


@given(
    'the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with P1 badge and required_check_coupling body'
)
def given_p1_coupling_thread(ctx) -> None:
    ctx["client"].threads = [_severity_codex_thread(comment_body=_P1_COUPLING_BODY)]


@given('the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with no severity badge')
def given_no_badge_thread(ctx) -> None:
    ctx["client"].threads = [_severity_codex_thread(comment_body=_NO_BADGE_BODY)]


@given('the stub PR "iterwheel/sandbox" #49 has 1 Codex thread with P2 badge and no coupling cue')
def given_p2_no_coupling_thread(ctx) -> None:
    ctx["client"].threads = [_severity_codex_thread(comment_body=_P2_NO_COUPLING_BODY)]


@given(parsers.parse('the base branch is "{branch}"'))
def given_base_branch(ctx, branch: str) -> None:
    ctx["client"].pr_payload["base"] = {"ref": branch}


@given("the stub branch_protected returns False")
def given_branch_not_protected(ctx) -> None:
    ctx["client"]._branch_protected_result = False


@given("the stub branch_protected returns True")
def given_branch_protected(ctx) -> None:
    ctx["client"]._branch_protected_result = True


@given("the stub branch_protected raises a transport error")
def given_branch_protected_raises(ctx) -> None:
    import httpx

    ctx["client"]._branch_protected_raise = httpx.HTTPError("simulated transport error")


@then(parsers.parse('the thread codex_severity is "{expected}"'))
def then_thread_codex_severity(ctx, expected: str) -> None:
    t = _first_thread(ctx)
    assert t.codex_severity.value == expected, (
        f"thread.codex_severity={t.codex_severity!r}, expected {expected!r}"
    )


@then(parsers.parse('the thread effective_severity is "{expected}"'))
def then_thread_effective_severity(ctx, expected: str) -> None:
    t = _first_thread(ctx)
    assert t.effective_severity.value == expected, (
        f"thread.effective_severity={t.effective_severity!r}, expected {expected!r}"
    )


@then(parsers.parse('the thread demotion_reason contains "{substring}"'))
def then_thread_demotion_reason_contains(ctx, substring: str) -> None:
    t = _first_thread(ctx)
    reason = t.demotion_reason or ""
    assert substring in reason, f"thread.demotion_reason={reason!r} does not contain {substring!r}"


@then("the thread demotion_reason is None")
def then_thread_demotion_reason_none(ctx) -> None:
    t = _first_thread(ctx)
    assert t.demotion_reason is None, (
        f"expected thread.demotion_reason=None, got {t.demotion_reason!r}"
    )


@then("a severity_demoted log was emitted")
def then_severity_demoted_log(ctx) -> None:
    records = ctx.get("captured_logs", [])
    matched = any(
        "severity_demoted" in (record.getMessage())
        for record in records
        if record.levelno >= logging.INFO
    )
    assert matched, "No 'severity_demoted' log record found. Records: " + str(
        [r.getMessage() for r in records]
    )


# ---------------------------------------------------------------------------
# Wave 7C commit 5: head_sha in automation dict
# ---------------------------------------------------------------------------


@then(parsers.parse('the automation head_sha is "{sha}"'))
def then_automation_head_sha(ctx, sha: str) -> None:
    auto = ctx["automation"]
    assert auto is not None, f"raised={ctx.get('raised')}"
    assert "head_sha" in auto, f"head_sha absent from automation keys: {list(auto.keys())}"
    assert auto["head_sha"] == sha, f"automation head_sha={auto['head_sha']!r}, expected {sha!r}"


# ---------------------------------------------------------------------------
# Wave 7C commit 6: stale-verdict guard in dispatch_route_writeback
# ---------------------------------------------------------------------------


@given(parsers.parse('a stub automation with head_sha "{sha}" and status "{status}"'))
def given_stub_automation_with_head_sha(ctx, sha: str, status: str) -> None:
    ctx["stub_automation"] = {
        "enabled": True,
        "status": status,
        "head_sha": sha,
        "sync_actions": [],
        "sync_actions_count": 0,
    }


@given(parsers.parse('a stub automation with no head_sha and status "{status}"'))
def given_stub_automation_no_head_sha(ctx, status: str) -> None:
    ctx["stub_automation"] = {
        "enabled": True,
        "status": status,
        "sync_actions": [],
        "sync_actions_count": 0,
    }


@given(parsers.parse('the current PR head sha is "{sha}"'))
def given_current_pr_head_sha(ctx, sha: str) -> None:
    ctx["client"].pr_payload["head"] = {"sha": sha}


@given("the stub client fails on pull_request with an httpx error")
def given_pull_request_fails_httpx(ctx) -> None:
    ctx["client"].fail_pull_request_httpx = True


def _run_dispatch(ctx, *, dry_run: bool, repo: str, pr: int) -> None:
    """Invoke dispatch_route_writeback with a monkeypatched compute_clearance_automation.

    The stale-guard in dispatch_route_writeback runs AFTER compute_clearance_automation
    returns the automation dict. To test the guard in isolation we patch
    compute_clearance_automation to return ctx["stub_automation"] directly, then
    also patch enrich_clearance_route so the downstream enrichment path does not
    attempt real GitHub calls.
    """
    import asyncio
    import importlib

    route = {
        "agent": "iterwheel-clearance",
        "kind": "pr",
        "validation": {"pr_number": pr, "issue_number": pr},
        "writeback": {"dynamic": "clearance_readiness"},
    }
    stub_automation = ctx["stub_automation"]

    async def _fake_compute(
        client,
        route,
        *,
        repository,
        store=None,
        default_profile_name=None,
        investigator=None,
        expected_sha=None,
    ):
        return stub_automation

    async def _fake_enrich(client, route, *, repository, automation=None):
        # Return a minimal concrete route so apply_route_writeback can run.
        return {
            "agent": route["agent"],
            "kind": route["kind"],
            "validation": {**route["validation"]},
            "writeback": {},
        }

    pipeline_mod = importlib.import_module("voyager.bots.clearance.pipeline")
    clearance_pkg = importlib.import_module("voyager.bots.clearance")

    original_compute = getattr(pipeline_mod, "compute_clearance_automation", None)
    original_enrich = getattr(clearance_pkg, "enrich_clearance_route", None)

    dispatch_log_records: list[logging.LogRecord] = []
    handler = _CapturingHandler(dispatch_log_records)
    wb_logger = logging.getLogger("voyager.core.writeback")
    old_level = wb_logger.level
    wb_logger.setLevel(logging.DEBUG)
    wb_logger.addHandler(handler)

    old_env = os.environ.get("DRY_RUN")
    os.environ["DRY_RUN"] = "false" if not dry_run else "true"

    try:
        pipeline_mod.compute_clearance_automation = _fake_compute
        clearance_pkg.enrich_clearance_route = _fake_enrich

        from voyager.core.writeback import dispatch_route_writeback

        ctx["dispatch_result"] = asyncio.run(
            dispatch_route_writeback(
                ctx["client"],
                route,
                repository=repo,
                store=object(),  # non-None so the pipeline branch executes
            )
        )
    finally:
        if original_compute is not None:
            pipeline_mod.compute_clearance_automation = original_compute
        if original_enrich is not None:
            clearance_pkg.enrich_clearance_route = original_enrich
        wb_logger.removeHandler(handler)
        wb_logger.setLevel(old_level)
        if old_env is None:
            os.environ.pop("DRY_RUN", None)
        else:
            os.environ["DRY_RUN"] = old_env
        ctx["dispatch_logs"] = dispatch_log_records


@when(parsers.parse('dispatch_route_writeback runs with DRY_RUN {flag} for PR {pr:d} on "{repo}"'))
def when_dispatch_stale_guard(ctx, flag: str, pr: int, repo: str) -> None:
    _run_dispatch(ctx, dry_run=(flag.lower() == "true"), repo=repo, pr=pr)


@then("the writeback was not skipped")
def then_writeback_not_skipped(ctx) -> None:
    result = ctx.get("dispatch_result")
    assert result is not None, "dispatch_route_writeback did not return a result"
    assert result.get("skipped") != "stale_verdict", (
        f"expected writeback to proceed but got skipped result: {result!r}"
    )


@then("no stale_verdict_skip log was emitted")
def then_no_stale_verdict_skip_log(ctx) -> None:
    records = ctx.get("dispatch_logs", [])
    matched = any("stale_verdict_skip" in r.getMessage() for r in records)
    assert not matched, "Unexpected stale_verdict_skip log. Records: " + str(
        [r.getMessage() for r in records]
    )


@then(parsers.parse('the dispatch result is skipped with reason "{reason}"'))
def then_dispatch_skipped(ctx, reason: str) -> None:
    result = ctx.get("dispatch_result")
    assert result is not None, "dispatch_route_writeback did not return a result"
    assert result.get("ok") is True, f"expected ok=True in skipped result, got: {result!r}"
    assert result.get("skipped") == reason, (
        f"expected skipped={reason!r}, got skipped={result.get('skipped')!r}"
    )


@then(parsers.parse('the dispatch automation status is "{status}"'))
def then_dispatch_automation_status(ctx, status: str) -> None:
    result = ctx.get("dispatch_result")
    assert result is not None
    automation = result.get("automation") or {}
    assert automation.get("status") == status, (
        f"expected automation.status={status!r}, got {automation.get('status')!r}"
    )


@then(
    parsers.parse(
        'a stale_verdict_skip log was emitted with expected_sha "{expected_sha}" '
        'and actual_sha "{actual_sha}"'
    )
)
def then_stale_verdict_skip_log(ctx, expected_sha: str, actual_sha: str) -> None:
    records = ctx.get("dispatch_logs", [])
    matched = any("stale_verdict_skip" in r.getMessage() for r in records)
    assert matched, "No stale_verdict_skip log found. Records: " + str(
        [r.getMessage() for r in records]
    )
    log_text = " ".join(r.getMessage() for r in records if "stale_verdict_skip" in r.getMessage())
    assert expected_sha in log_text, (
        f"expected_sha={expected_sha!r} not found in stale_verdict_skip log: {log_text!r}"
    )
    assert actual_sha in log_text, (
        f"actual_sha={actual_sha!r} not found in stale_verdict_skip log: {log_text!r}"
    )


@then("a stale_guard_failed_fail_open log was emitted")
def then_stale_guard_failed_log(ctx) -> None:
    records = ctx.get("dispatch_logs", [])
    matched = any("stale_guard_failed_fail_open" in r.getMessage() for r in records)
    assert matched, "No stale_guard_failed_fail_open log found. Records: " + str(
        [r.getMessage() for r in records]
    )


@then("a writeback_skipped_stale_verdict log was emitted")
def then_writeback_skipped_stale_verdict_log(ctx) -> None:
    records = ctx.get("dispatch_logs", [])
    matched = any("writeback_skipped_stale_verdict" in r.getMessage() for r in records)
    assert matched, "No writeback_skipped_stale_verdict log found. Records: " + str(
        [r.getMessage() for r in records]
    )


@then("pull_request was never called")
def then_pull_request_never_called(ctx) -> None:
    count = ctx["client"].pull_request_call_count
    assert count == 0, f"expected pull_request to be called 0 times, got {count}"


# ---------------------------------------------------------------------------
# Fix 2 (Codex P2): pre-mutation stale guard inside compute_clearance_automation
# ---------------------------------------------------------------------------


@given(parsers.parse('the webhook expected_sha is "{sha}"'))
def given_webhook_expected_sha(ctx, sha: str) -> None:
    ctx["webhook_expected_sha"] = sha


@given(parsers.parse('the stub PR current head sha advanced to "{sha}"'))
def given_stub_pr_head_advanced(ctx, sha: str) -> None:
    ctx["client"].pr_payload["head"] = {**ctx["client"].pr_payload["head"], "sha": sha}


@given(parsers.parse('the stub PR current head sha is "{sha}"'))
def given_stub_pr_current_head_sha(ctx, sha: str) -> None:
    ctx["client"].pr_payload["head"] = {**ctx["client"].pr_payload["head"], "sha": sha}


@given(parsers.parse('the stub PR initial head sha is "{sha}"'))
def given_stub_pr_initial_head_sha(ctx, sha: str) -> None:
    ctx["client"].pr_payload["head"] = {**ctx["client"].pr_payload["head"], "sha": sha}


@given(parsers.parse('the stub PR head advances on the second pull_request call to "{sha}"'))
def given_stub_pr_head_advances_on_second_call(ctx, sha: str) -> None:
    ctx["client"].pr_payload_second_fetch = {"sha": sha}


@given(parsers.parse('the stub PR head is stable at "{sha}" on all fetches'))
def given_stub_pr_head_stable(ctx, sha: str) -> None:
    ctx["client"].pr_payload["head"] = {**ctx["client"].pr_payload["head"], "sha": sha}
    ctx["client"].pr_payload_second_fetch = None


@then(
    parsers.parse(
        'a pipeline_stale_verdict_skip log was emitted with expected_sha "{expected_sha}"'
        ' and actual_sha "{actual_sha}"'
    )
)
def then_pipeline_stale_verdict_skip_log(ctx, expected_sha: str, actual_sha: str) -> None:
    records = ctx.get("captured_logs", [])
    matched = any("pipeline_stale_verdict_skip" in r.getMessage() for r in records)
    assert matched, "No pipeline_stale_verdict_skip log found. Records: " + str(
        [r.getMessage() for r in records]
    )
    log_text = " ".join(
        r.getMessage() for r in records if "pipeline_stale_verdict_skip" in r.getMessage()
    )
    assert expected_sha in log_text, (
        f"expected_sha={expected_sha!r} not found in pipeline_stale_verdict_skip log: {log_text!r}"
    )
    assert actual_sha in log_text, (
        f"actual_sha={actual_sha!r} not found in pipeline_stale_verdict_skip log: {log_text!r}"
    )


# ---------------------------------------------------------------------------
# Issue #63: State A investigator eligibility (codex_review_stale)
# ---------------------------------------------------------------------------


@given("the PR was pushed after the Codex review")
def given_pr_pushed_after_codex(ctx) -> None:
    """Set pushed_at to a timestamp newer than _fresh_codex_thread's createdAt."""
    ctx["client"].pr_payload["pushed_at"] = "2026-05-12T00:00:00Z"


@given("the PR was not pushed after the Codex review")
def given_pr_not_pushed_after_codex(ctx) -> None:
    """Set pushed_at to a timestamp older than _fresh_codex_thread's createdAt."""
    ctx["client"].pr_payload["pushed_at"] = "2026-05-10T00:00:00Z"


# Issue #62: fork PR head-repo accessibility (UnsupportedContext)
# ---------------------------------------------------------------------------


@given(parsers.parse('the stub PR is from fork "{head_repo}"'))
def given_fork_pr(ctx, head_repo: str) -> None:
    """Configure the stub PR payload as a fork PR."""
    ctx["client"].pr_payload["head"]["repo"]["full_name"] = head_repo
    # base repo stays as iterwheel/sandbox (the default)


@given("the fork head repo is accessible")
def given_fork_head_accessible(ctx) -> None:
    ctx["client"]._head_repo_accessible = True


@given("the fork head repo is not accessible")
def given_fork_head_not_accessible(ctx) -> None:
    ctx["client"]._head_repo_accessible = False


@then("exactly 0 resolveReviewThread mutations were invoked")
def then_zero_resolve_mutations(ctx) -> None:
    count = len(ctx["client"].resolve_calls)
    assert count == 0, f"expected 0 resolveReviewThread calls, got {count}"


@then("the Stage 1.5 action suggested_action mentions the fork repo")
def then_stage15_suggested_fork(ctx) -> None:
    auto = ctx["automation"]
    assert auto is not None, f"raised={ctx.get('raised')}"
    actions = auto.get("sync_actions") or []
    unsupported = [
        a for a in actions if (a.get("result") or {}).get("error_class") == "UnsupportedContext"
    ]
    assert unsupported, f"no UnsupportedContext actions found in sync_actions: {actions!r}"
    result = unsupported[0]["result"]
    suggested = result.get("suggested_action") or ""
    assert "fork" in suggested.lower(), f"expected 'fork' in suggested_action, got {suggested!r}"
    assert "install" in suggested.lower(), (
        f"expected 'install' in suggested_action, got {suggested!r}"
    )
