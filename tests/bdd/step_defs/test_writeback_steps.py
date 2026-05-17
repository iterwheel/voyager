"""Step definitions for apply_route_writeback BDD scenarios."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

# CRITICAL: do NOT import from voyager.* at module top level — import lazily
# INSIDE step functions to avoid collection-time crashes.

scenarios("../features/writeback.feature")

FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures" / "github_app"
TEST_KEY_PATH = FIXTURES_DIR / "test_private_key.pem"

_TOKEN_BODY = json.dumps(
    {
        "token": "ghs_test_installation_token_abc123",
        "expires_at": "2099-12-31T23:59:59Z",
    }
).encode()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json_response(status: int, body: Any) -> httpx.Response:
    content = json.dumps(body) if not isinstance(body, bytes) else body
    return httpx.Response(
        status_code=status,
        headers={"Content-Type": "application/json"},
        content=content if isinstance(content, bytes) else content.encode(),
    )


def _token_response() -> httpx.Response:
    return httpx.Response(
        status_code=200,
        headers={"Content-Type": "application/json"},
        content=_TOKEN_BODY,
    )


def _make_transport(
    responses: list[httpx.Response], captured: list[httpx.Request]
) -> httpx.MockTransport:
    index = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        i = index[0]
        if i < len(responses):
            index[0] += 1
            return responses[i]
        raise RuntimeError(f"Unexpected HTTP call #{i + 1}: {request.method} {request.url}")

    return httpx.MockTransport(handler)


def _make_app_config(
    slug: str = "iterwheel-blueprint",
    installation_id: str = "55544433",
) -> Any:
    cfg = MagicMock()
    cfg.slug = slug
    cfg.app_id = "1234567"
    cfg.installation_id = installation_id
    cfg.installations = {}
    cfg.private_key_path = TEST_KEY_PATH

    def _configured_install(repository: str | None) -> str | None:
        return installation_id or None

    cfg.configured_installation_id_for_repository = _configured_install
    return cfg


def _build_client(
    apps: dict[str, Any], responses: list[httpx.Response], captured: list[httpx.Request]
) -> Any:
    from voyager.core.github_app import GitHubAppClient  # lazy

    transport = _make_transport(responses, captured)

    class _PatchedClient(GitHubAppClient):
        def _async_client(self) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=transport, timeout=15)

    return _PatchedClient(apps)


# ---------------------------------------------------------------------------
# Per-scenario state
# ---------------------------------------------------------------------------


@dataclass
class WritebackState:
    apps: dict[str, Any] = field(default_factory=dict)
    client: Any = None
    route: dict[str, Any] = field(default_factory=dict)
    repository: str | None = "iterwheel/voyager-sandbox"
    captured: list[httpx.Request] = field(default_factory=list)
    responses: list[httpx.Response] = field(default_factory=list)
    result: dict[str, Any] | None = None
    results: list[dict[str, Any]] = field(default_factory=list)
    dry_run_env: str = "true"


@pytest.fixture
def state() -> WritebackState:
    return WritebackState()


# ---------------------------------------------------------------------------
# Given — client setup
# ---------------------------------------------------------------------------


@given("a writeback client with a recording transport", target_fixture="state")
def given_client_no_http() -> WritebackState:
    s = WritebackState()
    cfg = _make_app_config()
    s.apps[cfg.slug] = cfg
    # No HTTP calls expected (dry-run or skipped) — responses list stays empty
    s.client = _build_client(s.apps, s.responses, s.captured)
    return s


@given("a writeback client with a recording transport for label changes", target_fixture="state")
def given_client_label_changes() -> WritebackState:
    s = WritebackState()
    cfg = _make_app_config()
    s.apps[cfg.slug] = cfg
    # Sequence: token, DELETE label, POST add labels
    s.responses = [
        _token_response(),
        _json_response(204, {}),  # DELETE label (204 No Content)
        _json_response(200, [{"name": "backlog"}]),  # POST add labels
    ]
    s.client = _build_client(s.apps, s.responses, s.captured)
    return s


@given("a writeback client with a recording transport for reaction changes", target_fixture="state")
def given_client_reaction_changes() -> WritebackState:
    s = WritebackState()
    cfg = _make_app_config()
    s.apps[cfg.slug] = cfg
    # Sequence: token, GET reactions (to find bot's reaction id), DELETE reaction, POST add reaction
    s.responses = [
        _token_response(),
        _json_response(
            200, [{"id": 9876, "content": "eyes", "user": {"login": "iterwheel-blueprint[bot]"}}]
        ),
        _json_response(204, {}),  # DELETE reaction
        _json_response(200, {"id": 9877, "content": "rocket"}),  # POST add reaction
    ]
    s.client = _build_client(s.apps, s.responses, s.captured)
    return s


@given("a writeback client with a recording transport for upsert comment", target_fixture="state")
def given_client_upsert_comment() -> WritebackState:
    s = WritebackState()
    cfg = _make_app_config()
    s.apps[cfg.slug] = cfg
    # Sequence: token, GET comments (for upsert search — no existing comment on page 1), POST new comment
    s.responses = [
        _token_response(),
        _json_response(200, []),  # GET issue comments (no existing bot comment)
        _json_response(
            200,
            {
                "id": 1001,
                "html_url": "https://github.com/iterwheel/voyager-sandbox/issues/42#issuecomment-1001",
            },
        ),
    ]
    s.client = _build_client(s.apps, s.responses, s.captured)
    return s


class _InMemoryCommentClient:
    def __init__(self) -> None:
        self.comments: list[dict[str, Any]] = []
        self.upsert_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []

    async def upsert_issue_comment(
        self,
        app_slug: str,
        repo: str,
        issue_number: int,
        *,
        marker: str,
        body: str,
    ) -> dict[str, Any]:
        self.upsert_calls.append(
            {
                "app_slug": app_slug,
                "repo": repo,
                "issue_number": issue_number,
                "marker": marker,
                "body": body,
            }
        )
        for comment in self.comments:
            if marker in str(comment.get("body") or ""):
                comment["body"] = body
                return dict(comment)
        comment = {
            "id": len(self.comments) + 1,
            "body": body,
            "html_url": f"https://github.test/{repo}/issues/{issue_number}#issuecomment-1",
        }
        self.comments.append(comment)
        return dict(comment)

    async def create_issue_comment(
        self,
        app_slug: str,
        repo: str,
        issue_number: int,
        *,
        body: str,
    ) -> dict[str, Any]:
        self.create_calls.append(
            {
                "app_slug": app_slug,
                "repo": repo,
                "issue_number": issue_number,
                "body": body,
            }
        )
        comment = {
            "id": len(self.comments) + 1,
            "body": body,
            "html_url": f"https://github.test/{repo}/issues/{issue_number}#issuecomment-new",
        }
        self.comments.append(comment)
        return dict(comment)


@given("an in-memory writeback client for clearance comments", target_fixture="state")
def given_in_memory_clearance_comments() -> WritebackState:
    s = WritebackState()
    s.client = _InMemoryCommentClient()
    return s


@given("a writeback client with a recording transport for append comment", target_fixture="state")
def given_client_append_comment() -> WritebackState:
    s = WritebackState()
    cfg = _make_app_config()
    s.apps[cfg.slug] = cfg
    # Sequence: token, POST comment (direct, no upsert search)
    s.responses = [
        _token_response(),
        _json_response(
            200,
            {
                "id": 1002,
                "html_url": "https://github.com/iterwheel/voyager-sandbox/issues/42#issuecomment-1002",
            },
        ),
    ]
    s.client = _build_client(s.apps, s.responses, s.captured)
    return s


# ---------------------------------------------------------------------------
# Given — route setup
# ---------------------------------------------------------------------------


@given(
    parsers.parse(
        'a route for "{slug}" on issue {issue_number:d} with add label "{add_label}" and remove label "{remove_label}"'
    )
)
def given_route_labels(
    state: WritebackState, slug: str, issue_number: int, add_label: str, remove_label: str
) -> None:
    state.route = {
        "agent": slug,
        "kind": "issues",
        "validation": {"status": "routed", "conclusion": "valid", "issue_number": issue_number},
        "writeback": {
            "labels": {"add": [add_label], "remove": [remove_label]},
        },
    }


@given(
    parsers.parse(
        'a route for "{slug}" on issue {issue_number:d} with remove reaction "{remove_reaction}" and add reaction "{add_reaction}"'
    )
)
def given_route_reactions(
    state: WritebackState, slug: str, issue_number: int, remove_reaction: str, add_reaction: str
) -> None:
    state.route = {
        "agent": slug,
        "kind": "issues",
        "validation": {"status": "routed", "conclusion": "valid", "issue_number": issue_number},
        "writeback": {
            "reactions": {"add": [add_reaction], "remove": [remove_reaction]},
        },
    }


@given(
    parsers.re(
        r'a route for "(?P<slug>[^"]+)" on issue (?P<issue_number>\d+) with comment body "(?P<body>[^"]*)" marker "(?P<marker>[^"]*)" mode "(?P<mode>[^"]+)"'
    )
)
def given_route_comment(
    state: WritebackState, slug: str, issue_number: str, body: str, marker: str, mode: str
) -> None:
    state.route = {
        "agent": slug,
        "kind": "issues",
        "validation": {
            "status": "routed",
            "conclusion": "valid",
            "issue_number": int(issue_number),
        },
        "writeback": {
            "comment_body": body,
            "comment_marker": marker,
            "comment_mode": mode,
        },
    }


@given(parsers.parse('a route for "{slug}" with no issue_number'))
def given_route_no_issue(state: WritebackState, slug: str) -> None:
    state.route = {
        "agent": slug,
        "kind": "issues",
        "validation": {"status": "routed", "conclusion": "valid"},
        "writeback": {},
    }


# ---------------------------------------------------------------------------
# Given — dry run env
# ---------------------------------------------------------------------------


@given(parsers.parse('DRY_RUN is "{value}"'))
def given_dry_run(state: WritebackState, value: str) -> None:
    state.dry_run_env = value


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when(
    parsers.parse('apply_route_writeback is called with repository "{repository}"'),
    target_fixture="state",
)
def when_apply_writeback(state: WritebackState, repository: str) -> WritebackState:
    import asyncio

    from voyager.core.writeback import apply_route_writeback  # lazy

    old = os.environ.get("DRY_RUN")
    os.environ["DRY_RUN"] = state.dry_run_env
    try:
        state.result = asyncio.run(
            apply_route_writeback(state.client, state.route, repository=repository)
        )
    finally:
        if old is None:
            os.environ.pop("DRY_RUN", None)
        else:
            os.environ["DRY_RUN"] = old
    return state


@when("apply_route_writeback is called with repository None", target_fixture="state")
def when_apply_writeback_none_repo(state: WritebackState) -> WritebackState:
    import asyncio

    from voyager.core.writeback import apply_route_writeback  # lazy

    old = os.environ.get("DRY_RUN")
    os.environ["DRY_RUN"] = state.dry_run_env
    try:
        state.result = asyncio.run(
            apply_route_writeback(state.client, state.route, repository=None)
        )
    finally:
        if old is None:
            os.environ.pop("DRY_RUN", None)
        else:
            os.environ["DRY_RUN"] = old
    return state


@when(
    parsers.parse(
        'apply_route_writeback is called twice with updated comment body "{body}" for repository "{repository}"'
    ),
    target_fixture="state",
)
def when_apply_writeback_twice_with_updated_comment(
    state: WritebackState, body: str, repository: str
) -> WritebackState:
    import asyncio

    from voyager.core.writeback import apply_route_writeback  # lazy

    old = os.environ.get("DRY_RUN")
    os.environ["DRY_RUN"] = state.dry_run_env
    try:
        first = asyncio.run(apply_route_writeback(state.client, state.route, repository=repository))
        state.route["writeback"]["comment_body"] = body
        second = asyncio.run(
            apply_route_writeback(state.client, state.route, repository=repository)
        )
        state.results = [first, second]
        state.result = second
    finally:
        if old is None:
            os.environ.pop("DRY_RUN", None)
        else:
            os.environ["DRY_RUN"] = old
    return state


# ---------------------------------------------------------------------------
# dispatch_route_writeback — Codex round 1 P1 (PR #7)
# ---------------------------------------------------------------------------


@given(parsers.parse("a clearance dynamic route on PR {pr:d}"))
def given_clearance_dynamic_route(state: WritebackState, pr: int) -> None:
    """Mimic the shape that route_clearance_event() returns.

    Clearance bot routes carry only a dynamic-enrichment marker — the real
    labels/comment/reactions come from enrich_clearance_route(), which fetches
    the live PR snapshot.

    Also register an iterwheel-clearance AppConfig in state.apps so the test
    client knows the slug (GitHubAppClient holds state.apps by reference, so
    mutating the dict after client construction is observed).
    """
    if "iterwheel-clearance" not in state.apps:
        state.apps["iterwheel-clearance"] = _make_app_config(slug="iterwheel-clearance")

    state.route = {
        "agent": "iterwheel-clearance",
        "kind": "clearance_readiness",
        "validation": {
            "pr_number": pr,
            "issue_number": pr,
            "status": "clearance_pending",
            "conclusion": "neutral",
        },
        "writeback": {"dynamic": "clearance_readiness"},
    }


@given("enrich_clearance_route is stubbed to return a concrete writeback")
def stub_enrich_clearance_route(state: WritebackState, monkeypatch) -> None:
    """Stub the clearance bot's enrich function so the dispatcher can be tested
    without mocking the full 3-call GitHub snapshot flow (pull_request +
    pull_request_reviews + pull_request_review_threads).
    """

    async def fake_enrich(
        client, route, *, repository: str, automation=None
    ) -> dict:  # pragma: no cover - signature mirrors the real function
        return {
            "agent": route["agent"],
            "kind": route["kind"],
            "validation": {**route["validation"], "issue_number": route["validation"]["pr_number"]},
            "writeback": {
                "labels": {
                    "add": ["clearance-ready"],
                    "remove": ["clearance-pending"],
                },
                "reactions": {"add": [], "remove": []},
            },
        }

    import voyager.bots.clearance as clearance_pkg

    monkeypatch.setattr(clearance_pkg, "enrich_clearance_route", fake_enrich)


@when(
    parsers.parse('dispatch_route_writeback is called with repository "{repository}"'),
    target_fixture="state",
)
def when_dispatch_writeback(state: WritebackState, repository: str) -> WritebackState:
    import asyncio

    from voyager.core.writeback import dispatch_route_writeback  # lazy

    old = os.environ.get("DRY_RUN")
    os.environ["DRY_RUN"] = state.dry_run_env
    try:
        state.result = asyncio.run(
            dispatch_route_writeback(state.client, state.route, repository=repository)
        )
    finally:
        if old is None:
            os.environ.pop("DRY_RUN", None)
        else:
            os.environ["DRY_RUN"] = old
    return state


@when("dispatch_route_writeback is called with repository None", target_fixture="state")
def when_dispatch_writeback_none_repo(state: WritebackState) -> WritebackState:
    import asyncio

    from voyager.core.writeback import dispatch_route_writeback  # lazy

    old = os.environ.get("DRY_RUN")
    os.environ["DRY_RUN"] = state.dry_run_env
    try:
        state.result = asyncio.run(
            dispatch_route_writeback(state.client, state.route, repository=None)
        )
    finally:
        if old is None:
            os.environ.pop("DRY_RUN", None)
        else:
            os.environ["DRY_RUN"] = old
    return state


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------


@then("the result has applied false")
def then_applied_false(state: WritebackState) -> None:
    assert state.result is not None
    assert state.result.get("applied") is False, f"Expected applied=False, got: {state.result}"


@then("the result has applied true")
def then_applied_true(state: WritebackState) -> None:
    assert state.result is not None
    assert state.result.get("applied") is True, f"Expected applied=True, got: {state.result}"


@then("the result has dry_run true")
def then_dry_run_true(state: WritebackState) -> None:
    assert state.result.get("dry_run") is True, f"Expected dry_run=True, got: {state.result}"


@then("the result has dry_run false")
def then_dry_run_false(state: WritebackState) -> None:
    assert state.result.get("dry_run") is False, f"Expected dry_run=False, got: {state.result}"


@then(parsers.parse('the result planned add_labels contains "{label}"'))
def then_planned_add_labels(state: WritebackState, label: str) -> None:
    planned = state.result.get("planned") or {}
    assert label in planned.get("add_labels", []), f"Expected {label!r} in add_labels: {planned}"


@then(parsers.parse('the result planned remove_labels contains "{label}"'))
def then_planned_remove_labels(state: WritebackState, label: str) -> None:
    planned = state.result.get("planned") or {}
    assert label in planned.get("remove_labels", []), (
        f"Expected {label!r} in remove_labels: {planned}"
    )


@then("no HTTP requests were made")
def then_no_requests(state: WritebackState) -> None:
    assert state.captured == [], (
        f"Expected no HTTP requests, got: {[str(r.url) for r in state.captured]}"
    )


@then("the first non-token request is a DELETE to remove the label")
def then_first_non_token_is_delete(state: WritebackState) -> None:
    non_token = [r for r in state.captured if "/access_tokens" not in str(r.url)]
    assert non_token, "No non-token requests found"
    first = non_token[0]
    assert first.method == "DELETE", f"Expected DELETE, got {first.method}: {first.url}"
    assert "/labels/" in str(first.url), f"Expected label DELETE URL, got: {first.url}"


@then("a subsequent request is a POST to add labels")
def then_subsequent_post_labels(state: WritebackState) -> None:
    non_token = [r for r in state.captured if "/access_tokens" not in str(r.url)]
    posts = [r for r in non_token if r.method == "POST" and "/labels" in str(r.url)]
    assert posts, f"No POST /labels request found in: {[(r.method, str(r.url)) for r in non_token]}"


@then("a DELETE request was made for the reaction")
def then_delete_reaction(state: WritebackState) -> None:
    non_token = [r for r in state.captured if "/access_tokens" not in str(r.url)]
    deletes = [r for r in non_token if r.method == "DELETE" and "/reactions/" in str(r.url)]
    assert deletes, f"No DELETE reaction request: {[(r.method, str(r.url)) for r in non_token]}"


@then("a POST request was made to add the reaction")
def then_post_reaction(state: WritebackState) -> None:
    non_token = [r for r in state.captured if "/access_tokens" not in str(r.url)]
    posts = [r for r in non_token if r.method == "POST" and "/reactions" in str(r.url)]
    assert posts, f"No POST reaction request: {[(r.method, str(r.url)) for r in non_token]}"


@then("the result comment_url is set")
def then_comment_url_set(state: WritebackState) -> None:
    assert state.result.get("comment_url"), f"comment_url not set: {state.result}"


@then("a POST request was made to create the comment directly")
def then_post_comment_direct(state: WritebackState) -> None:
    non_token = [r for r in state.captured if "/access_tokens" not in str(r.url)]
    # For append mode, should POST directly without any GET comments first
    gets = [r for r in non_token if r.method == "GET"]
    posts = [r for r in non_token if r.method == "POST" and "/comments" in str(r.url)]
    assert not gets, (
        f"Unexpected GET requests in append mode: {[(r.method, str(r.url)) for r in gets]}"
    )
    assert posts, f"No POST comment request: {[(r.method, str(r.url)) for r in non_token]}"


@then("exactly one in-memory comment exists")
def then_one_in_memory_comment(state: WritebackState) -> None:
    assert len(state.client.comments) == 1, f"comments={state.client.comments!r}"


@then(parsers.parse('the in-memory comment body is "{body}"'))
def then_in_memory_comment_body(state: WritebackState, body: str) -> None:
    assert state.client.comments[0]["body"] == body, f"comments={state.client.comments!r}"


@then("no direct append comments were created")
def then_no_direct_append_comments(state: WritebackState) -> None:
    assert state.client.create_calls == [], f"create_calls={state.client.create_calls!r}"


@then(parsers.parse('the result reason mentions "{text}"'))
def then_reason_mentions(state: WritebackState, text: str) -> None:
    reason = state.result.get("reason", "")
    assert text in reason, f"Expected {text!r} in reason: {reason!r}"
