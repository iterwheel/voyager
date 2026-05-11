"""Step definitions for GitHub App auth BDD scenarios."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

# CRITICAL: do NOT import from voyager.* at module top level — those modules
# don't have implementations yet, so top-level imports would crash pytest
# collection. Import lazily INSIDE step functions instead.

scenarios("../features/github_app.feature")

FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures" / "github_app"
TEST_KEY_PATH = FIXTURES_DIR / "test_private_key.pem"


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _load_fixture(name: str) -> dict[str, Any]:
    path = FIXTURES_DIR / f"{name}.json"
    return json.loads(path.read_text())


def _make_app_config(
    slug: str,
    app_id: str,
    installation_id: str = "",
    installations: dict[str, str] | None = None,
    key_path: Path | None = None,
) -> Any:
    """Build a minimal AppConfig-compatible object without importing voyager."""
    from unittest.mock import MagicMock

    cfg = MagicMock()
    cfg.slug = slug
    cfg.app_id = app_id
    cfg.installation_id = installation_id
    cfg.installations = installations or {}
    cfg.private_key_path = key_path if key_path is not None else TEST_KEY_PATH

    def _configured_install(repository: str | None) -> str | None:
        if not repository:
            return installation_id or None
        owner, _, _ = repository.partition("/")
        mapping = installations or {}
        return mapping.get(repository) or mapping.get(owner) or None

    cfg.configured_installation_id_for_repository = _configured_install
    return cfg


@dataclass
class ClientState:
    """Accumulated state across Given/When/Then steps in one scenario."""

    apps: dict[str, Any] = field(default_factory=dict)
    client: Any = None
    captured_requests: list[httpx.Request] = field(default_factory=list)
    result: Any = None
    raised: BaseException | None = None


# ---------------------------------------------------------------------------
# Shared fixture: per-scenario mutable state container
# ---------------------------------------------------------------------------


@pytest.fixture
def state() -> ClientState:
    return ClientState()


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------


@given(
    parsers.parse('a test GitHub App with slug "{slug}" and app_id "{app_id}"'),
    target_fixture="state",
)
def background_app(slug: str, app_id: str) -> ClientState:
    s = ClientState()
    cfg = _make_app_config(slug=slug, app_id=app_id)
    s.apps[slug] = cfg
    return s


@given("the app has a valid RSA private key")
def app_valid_private_key(state: ClientState) -> None:
    for cfg in state.apps.values():
        cfg.private_key_path = TEST_KEY_PATH


@given(parsers.parse('the app has installation_id "{installation_id}"'))
def app_installation_id(state: ClientState, installation_id: str) -> None:
    for slug, cfg in list(state.apps.items()):
        state.apps[slug] = _make_app_config(
            slug=cfg.slug,
            app_id=cfg.app_id,
            installation_id=installation_id,
            installations=cfg.installations,
            key_path=cfg.private_key_path,
        )


# ---------------------------------------------------------------------------
# Background — error paths
# ---------------------------------------------------------------------------


@given("the private key file does not exist")
def private_key_missing(state: ClientState) -> None:
    for slug, cfg in list(state.apps.items()):
        state.apps[slug] = _make_app_config(
            slug=cfg.slug,
            app_id=cfg.app_id,
            installation_id=cfg.installation_id,
            installations=cfg.installations,
            key_path=Path("/nonexistent/path/to/key.pem"),
        )


# ---------------------------------------------------------------------------
# GitHub mock transport helpers
# ---------------------------------------------------------------------------


def _make_transport(responses: list[httpx.Response]) -> httpx.MockTransport:
    """Return a MockTransport that serves responses in order."""
    index = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        i = index[0]
        if i < len(responses):
            index[0] += 1
            return responses[i]
        raise RuntimeError(f"Unexpected HTTP call #{i + 1}: {request.method} {request.url}")

    return httpx.MockTransport(handler)


def _json_response(status: int, body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        headers={"Content-Type": "application/json"},
        content=json.dumps(body).encode(),
    )


def _token_response() -> httpx.Response:
    return _json_response(200, _load_fixture("installation_token_response"))


def _expiring_token_response() -> httpx.Response:
    return _json_response(200, _load_fixture("installation_token_expiring_response"))


def _discovery_response() -> httpx.Response:
    return _json_response(200, _load_fixture("discover_installation_response"))


def _json_list_response(status: int, body: list[dict[str, Any]]) -> httpx.Response:
    """Like _json_response but for JSON-array bodies (e.g. /reviews pagination)."""
    return httpx.Response(
        status_code=status,
        headers={"Content-Type": "application/json"},
        content=json.dumps(body).encode(),
    )


def _make_recording_transport(
    responses: list[httpx.Response], captured: list[httpx.Request]
) -> httpx.MockTransport:
    """MockTransport that also records every request."""
    index = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        i = index[0]
        if i < len(responses):
            index[0] += 1
            return responses[i]
        raise RuntimeError(f"Unexpected HTTP call #{i + 1}: {request.method} {request.url}")

    return httpx.MockTransport(handler)


def _build_client(state: ClientState, responses: list[httpx.Response]) -> Any:
    """Construct a GitHubAppClient with a patched AsyncClient transport."""
    from voyager.core.github_app import GitHubAppClient  # lazy

    transport = _make_recording_transport(responses, state.captured_requests)

    class _PatchedClient(GitHubAppClient):
        def _async_client(self) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=transport, timeout=15)

    return _PatchedClient(state.apps)


# ---------------------------------------------------------------------------
# JWT scenarios — Given / When / Then
# ---------------------------------------------------------------------------


@when("a JWT is generated for the app")
def generate_jwt(state: ClientState) -> None:
    import jwt as pyjwt

    from voyager.core.github_app import GitHubAppClient  # lazy

    client = GitHubAppClient(state.apps)
    app_cfg = next(iter(state.apps.values()))
    token_str = client._app_jwt(app_cfg)
    # Decode without verifying signature so we can inspect claims
    decoded = pyjwt.decode(
        token_str,
        options={"verify_signature": False},
        algorithms=["RS256"],
    )
    header = pyjwt.get_unverified_header(token_str)
    state.result = {"token": token_str, "claims": decoded, "header": header}


@when("a JWT generation is attempted for the app")
def attempt_jwt_generation(state: ClientState) -> None:
    from voyager.core.github_app import GitHubAppClient  # lazy

    client = GitHubAppClient(state.apps)
    app_cfg = next(iter(state.apps.values()))
    try:
        client._app_jwt(app_cfg)
    except RuntimeError as exc:
        state.raised = exc


@then(parsers.parse('the JWT header algorithm is "{algorithm}"'))
def jwt_header_alg(state: ClientState, algorithm: str) -> None:
    assert state.result["header"]["alg"] == algorithm


@then(parsers.parse('the JWT claim "{claim}" equals "{value}"'))
def jwt_claim_equals(state: ClientState, claim: str, value: str) -> None:
    assert str(state.result["claims"][claim]) == value


@then("the JWT iat is approximately 60 seconds before now")
def jwt_iat_skew(state: ClientState) -> None:
    now = int(time.time())
    iat = state.result["claims"]["iat"]
    # iat should be ~60 s before now; allow ±5 s for test execution lag
    assert abs((now - 60) - iat) <= 5, f"iat={iat} expected ~{now - 60}"


@then("the JWT exp is within 10 minutes from now")
def jwt_exp_within_ten_minutes(state: ClientState) -> None:
    now = int(time.time())
    exp = state.result["claims"]["exp"]
    assert exp > now, "JWT must not already be expired"
    assert exp <= now + 600 + 5, f"exp={exp} is more than ~10 min from now={now}"


@then(parsers.parse('a RuntimeError is raised mentioning "{fragment}"'))
def runtime_error_raised(state: ClientState, fragment: str) -> None:
    assert isinstance(state.raised, RuntimeError), f"Expected RuntimeError, got {state.raised!r}"
    assert fragment in str(state.raised), f"{fragment!r} not in {state.raised!r}"


# ---------------------------------------------------------------------------
# Installation token — GitHub mock Given steps
# ---------------------------------------------------------------------------


@given("GitHub returns a valid installation token response")
def mock_valid_token(state: ClientState) -> None:
    existing = getattr(state, "_mock_responses", [])
    state._mock_responses = [*existing, _token_response()]  # type: ignore[attr-defined]


@given("GitHub returns a fresh installation token response after an expiring one")
def mock_expiring_then_fresh(state: ClientState) -> None:
    state._mock_responses = [_expiring_token_response(), _token_response()]  # type: ignore[attr-defined]


@given("an installation token has already been fetched")
def prefetch_token(state: ClientState) -> None:
    import asyncio

    responses: list[httpx.Response] = getattr(state, "_mock_responses", [_token_response()])
    state.client = _build_client(state, responses)
    asyncio.get_event_loop().run_until_complete(state.client.installation_token("test-bot"))


@given("an installation token with near-expiry has been fetched")
def prefetch_expiring_token(state: ClientState) -> None:
    import asyncio

    responses = getattr(state, "_mock_responses", [_expiring_token_response(), _token_response()])
    state.client = _build_client(state, responses)
    asyncio.get_event_loop().run_until_complete(state.client.installation_token("test-bot"))


@given("GitHub returns a generic 200 JSON response")
def mock_generic_200(state: ClientState) -> None:
    existing = getattr(state, "_mock_responses", [])
    state._mock_responses = [*existing, _json_response(200, {"number": 1, "title": "Test PR"})]  # type: ignore[attr-defined]


@given("GitHub returns a 204 No Content response")
def mock_204(state: ClientState) -> None:
    existing = getattr(state, "_mock_responses", [])
    state._mock_responses = [*existing, httpx.Response(204)]  # type: ignore[attr-defined]


@given("GitHub returns a GraphQL response with errors")
def mock_graphql_errors(state: ClientState) -> None:
    existing = getattr(state, "_mock_responses", [])
    state._mock_responses = [
        *existing,
        _json_response(200, {"errors": [{"message": "Field not found"}], "data": None}),
    ]  # type: ignore[attr-defined]


@given("GitHub returns a 401 response on the installation token endpoint")
def mock_401(state: ClientState) -> None:
    body = _load_fixture("github_api_error_401")
    state._mock_responses = [_json_response(401, body)]  # type: ignore[attr-defined]


@given("GitHub returns a malformed JSON response missing the token field")
def mock_malformed(state: ClientState) -> None:
    body = _load_fixture("github_api_error_malformed")
    state._mock_responses = [_json_response(200, body)]  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Installation ID configuration Given steps
# ---------------------------------------------------------------------------


@given("the app has no default installation_id")
def app_no_installation_id(state: ClientState) -> None:
    for slug, cfg in list(state.apps.items()):
        state.apps[slug] = _make_app_config(
            slug=cfg.slug,
            app_id=cfg.app_id,
            installation_id="",
            installations={},
            key_path=cfg.private_key_path,
        )


@given("the app has no installation mappings")
def app_no_mappings(state: ClientState) -> None:
    for slug, cfg in list(state.apps.items()):
        state.apps[slug] = _make_app_config(
            slug=cfg.slug,
            app_id=cfg.app_id,
            installation_id="",
            installations={},
            key_path=cfg.private_key_path,
        )


@given(parsers.parse('the app has repository "{repo}" mapped to installation_id "{install_id}"'))
def app_repo_mapping(state: ClientState, repo: str, install_id: str) -> None:
    for slug, cfg in list(state.apps.items()):
        mappings = dict(cfg.installations)
        mappings[repo] = install_id
        state.apps[slug] = _make_app_config(
            slug=cfg.slug,
            app_id=cfg.app_id,
            installation_id=cfg.installation_id,
            installations=mappings,
            key_path=cfg.private_key_path,
        )


@given(parsers.parse('the app has owner "{owner}" mapped to installation_id "{install_id}"'))
def app_owner_mapping(state: ClientState, owner: str, install_id: str) -> None:
    for slug, cfg in list(state.apps.items()):
        mappings = dict(cfg.installations)
        mappings[owner] = install_id
        state.apps[slug] = _make_app_config(
            slug=cfg.slug,
            app_id=cfg.app_id,
            installation_id=cfg.installation_id,
            installations=mappings,
            key_path=cfg.private_key_path,
        )


@given(parsers.parse('GitHub discovery returns installation_id "{install_id}" for "{repo}"'))
def mock_discovery_ok(state: ClientState, install_id: str, repo: str) -> None:
    body = dict(_load_fixture("discover_installation_response"))
    body["id"] = int(install_id)
    existing = getattr(state, "_mock_responses", [])
    state._mock_responses = [_json_response(200, body), *existing]  # type: ignore[attr-defined]


@given(parsers.parse("GitHub returns a valid installation token response for two calls"))
def mock_valid_token_twice(state: ClientState) -> None:
    existing = getattr(state, "_mock_responses", [])
    state._mock_responses = [*existing, _token_response()]  # type: ignore[attr-defined]


@given(parsers.parse('GitHub discovery returns 404 for "{repo}"'))
def mock_discovery_404(state: ClientState, repo: str) -> None:
    state._mock_responses = [httpx.Response(404)]  # type: ignore[attr-defined]


@given('an installation token has been fetched for repository "test-org/new-repo"')
def prefetch_token_for_repo(state: ClientState) -> None:
    import asyncio

    responses = getattr(state, "_mock_responses", [_discovery_response(), _token_response()])
    state.client = _build_client(state, responses)
    asyncio.get_event_loop().run_until_complete(
        state.client.installation_token("test-bot", repository="test-org/new-repo")
    )
    state.captured_requests.clear()


# ---------------------------------------------------------------------------
# Multi-app Given steps
# ---------------------------------------------------------------------------


@given(parsers.parse('a second GitHub App with slug "{slug}" and app_id "{app_id}"'))
def add_second_app(state: ClientState, slug: str, app_id: str) -> None:
    cfg = _make_app_config(slug=slug, app_id=app_id)
    state.apps[slug] = cfg


@given("the second app has a valid RSA private key")
def second_app_valid_key(state: ClientState) -> None:
    slugs = list(state.apps.keys())
    if len(slugs) >= 2:
        second_slug = slugs[1]
        cfg = state.apps[second_slug]
        state.apps[second_slug] = _make_app_config(
            slug=cfg.slug,
            app_id=cfg.app_id,
            installation_id=cfg.installation_id,
            installations=cfg.installations,
            key_path=TEST_KEY_PATH,
        )


@given(parsers.parse('the second app has installation_id "{installation_id}"'))
def second_app_installation_id(state: ClientState, installation_id: str) -> None:
    slugs = list(state.apps.keys())
    if len(slugs) >= 2:
        second_slug = slugs[1]
        cfg = state.apps[second_slug]
        state.apps[second_slug] = _make_app_config(
            slug=cfg.slug,
            app_id=cfg.app_id,
            installation_id=installation_id,
            installations=cfg.installations,
            key_path=cfg.private_key_path,
        )


# ---------------------------------------------------------------------------
# When — installation_token
# ---------------------------------------------------------------------------


@when("an installation token is requested")
def request_token(state: ClientState) -> None:
    import asyncio

    responses = getattr(state, "_mock_responses", [_token_response()])
    state.client = _build_client(state, responses)
    try:
        state.result = asyncio.get_event_loop().run_until_complete(
            state.client.installation_token("test-bot")
        )
    except Exception as exc:
        state.raised = exc


@when("an installation token is requested again")
def request_token_again(state: ClientState) -> None:
    import asyncio

    try:
        state.result = asyncio.get_event_loop().run_until_complete(
            state.client.installation_token("test-bot")
        )
    except Exception as exc:
        state.raised = exc


@when(parsers.parse('an installation token is requested for repository "{repo}"'))
def request_token_for_repo(state: ClientState, repo: str) -> None:
    import asyncio

    responses = getattr(state, "_mock_responses", [_token_response()])
    state.client = _build_client(state, responses)
    try:
        state.result = asyncio.get_event_loop().run_until_complete(
            state.client.installation_token("test-bot", repository=repo)
        )
    except Exception as exc:
        state.raised = exc


@when("an installation token without a repository is attempted")
def request_token_no_repo(state: ClientState) -> None:
    import asyncio

    from voyager.core.github_app import GitHubAppClient  # lazy

    client = GitHubAppClient(state.apps)
    try:
        asyncio.get_event_loop().run_until_complete(client.installation_token("test-bot"))
    except RuntimeError as exc:
        state.raised = exc


@when(parsers.parse('an installation token without configured id is attempted for "{repo}"'))
def request_token_no_configured_id_for_repo(state: ClientState, repo: str) -> None:
    import asyncio

    responses = getattr(state, "_mock_responses", [httpx.Response(404)])
    state.client = _build_client(state, responses)
    try:
        asyncio.get_event_loop().run_until_complete(
            state.client.installation_token("test-bot", repository=repo)
        )
    except RuntimeError as exc:
        state.raised = exc


@when(parsers.parse('an installation token is requested for app "{app_slug}"'))
def request_token_for_app(state: ClientState, app_slug: str) -> None:
    import asyncio

    import jwt as pyjwt

    responses = getattr(state, "_mock_responses", [_token_response()])
    captured_jwts: list[str] = []
    index = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        state.captured_requests.append(request)
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            captured_jwts.append(auth[len("Bearer ") :])
        i = index[0]
        if i < len(responses):
            index[0] += 1
            return responses[i]
        raise RuntimeError(f"Unexpected HTTP call #{i + 1}")

    transport = httpx.MockTransport(handler)

    from voyager.core.github_app import GitHubAppClient  # lazy

    class _PatchedClient(GitHubAppClient):
        def _async_client(self) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=transport, timeout=15)

    client = _PatchedClient(state.apps)
    asyncio.get_event_loop().run_until_complete(client.installation_token(app_slug))
    # Decode the JWT that was sent so we can assert its iss claim
    if captured_jwts:
        decoded = pyjwt.decode(
            captured_jwts[0],
            options={"verify_signature": False},
            algorithms=["RS256"],
        )
        state.result = {"jwt_claims": decoded}


@when('an installation token is requested again for repository "test-org/new-repo"')
def request_token_again_for_repo(state: ClientState) -> None:
    import asyncio

    try:
        state.result = asyncio.get_event_loop().run_until_complete(
            state.client.installation_token("test-bot", repository="test-org/new-repo")
        )
    except Exception as exc:
        state.raised = exc


# ---------------------------------------------------------------------------
# When — request() and graphql()
# ---------------------------------------------------------------------------


@when(parsers.parse('a GET request is made to path "{path}"'))
def make_get_request(state: ClientState, path: str) -> None:
    import asyncio

    responses = getattr(state, "_mock_responses", [_token_response()])
    state.client = _build_client(state, responses)
    try:
        state.result = asyncio.get_event_loop().run_until_complete(
            state.client.request("test-bot", "GET", path, repository="test-org/my-repo")
        )
    except Exception as exc:
        state.raised = exc


@when(parsers.parse('a DELETE request is made to path "{path}"'))
def make_delete_request(state: ClientState, path: str) -> None:
    import asyncio

    responses = getattr(state, "_mock_responses", [_token_response()])
    state.client = _build_client(state, responses)
    try:
        state.result = asyncio.get_event_loop().run_until_complete(
            state.client.request("test-bot", "DELETE", path, repository="test-org/my-repo")
        )
    except Exception as exc:
        state.raised = exc


@when("a GraphQL query is executed")
def execute_graphql(state: ClientState) -> None:
    import asyncio

    responses = getattr(state, "_mock_responses", [_token_response()])
    state.client = _build_client(state, responses)
    try:
        state.result = asyncio.get_event_loop().run_until_complete(
            state.client.graphql(
                "test-bot",
                "test-org/my-repo",
                query="query { viewer { login } }",
                variables={},
            )
        )
    except RuntimeError as exc:
        state.raised = exc


# ---------------------------------------------------------------------------
# Then — HTTP call assertions
# ---------------------------------------------------------------------------


def _last_token_request(state: ClientState) -> httpx.Request:
    """Return the most recent captured request."""
    assert state.captured_requests, "No HTTP requests were captured"
    return state.captured_requests[-1]


def _first_token_request(state: ClientState) -> httpx.Request:
    assert state.captured_requests, "No HTTP requests were captured"
    return state.captured_requests[0]


@then(parsers.parse('the HTTP call was POST to "{path_suffix}"'))
def http_call_was_post(state: ClientState, path_suffix: str) -> None:
    matching = [
        r
        for r in state.captured_requests
        if r.method == "POST" and str(r.url).endswith(path_suffix)
    ]
    assert matching, f"No POST to {path_suffix!r} found. Captured: " + str(
        [(r.method, str(r.url)) for r in state.captured_requests]
    )


@then(parsers.parse('the GET discovery call was made to "{path_suffix}"'))
def http_discovery_get(state: ClientState, path_suffix: str) -> None:
    matching = [
        r for r in state.captured_requests if r.method == "GET" and str(r.url).endswith(path_suffix)
    ]
    assert matching, f"No GET to {path_suffix!r} found. Captured: " + str(
        [(r.method, str(r.url)) for r in state.captured_requests]
    )


@then(parsers.parse('the request Authorization header starts with "{prefix}"'))
def request_auth_header_prefix(state: ClientState, prefix: str) -> None:
    req = _last_token_request(state)
    auth = req.headers.get("authorization", "")
    assert auth.startswith(prefix), f"Authorization header {auth!r} does not start with {prefix!r}"


@then(parsers.parse('the request Accept header is "{value}"'))
def request_accept_header(state: ClientState, value: str) -> None:
    req = _last_token_request(state)
    assert req.headers.get("accept") == value


@then(parsers.parse('the request X-GitHub-Api-Version header is "{value}"'))
def request_api_version_header(state: ClientState, value: str) -> None:
    req = _last_token_request(state)
    assert req.headers.get("x-github-api-version") == value


@then(parsers.parse('the returned token is "{token}"'))
def returned_token_equals(state: ClientState, token: str) -> None:
    assert state.result == token


@then("only one HTTP call was made in total")
def only_one_http_call(state: ClientState) -> None:
    assert len(state.captured_requests) == 1, (
        f"Expected 1 HTTP call but got {len(state.captured_requests)}"
    )


@then("two HTTP calls were made in total")
def two_http_calls(state: ClientState) -> None:
    assert len(state.captured_requests) == 2, (
        f"Expected 2 HTTP calls but got {len(state.captured_requests)}"
    )


@then("only one discovery GET call was made in total")
def only_one_discovery_call(state: ClientState) -> None:
    discovery_calls = [
        r for r in state.captured_requests if r.method == "GET" and "/installation" in str(r.url)
    ]
    assert len(discovery_calls) == 0, (
        f"Expected 0 new discovery calls (cached), got {len(discovery_calls)}"
    )


@then("the result is None")
def result_is_none(state: ClientState) -> None:
    assert state.result is None


# ---------------------------------------------------------------------------
# Then — error assertions
# ---------------------------------------------------------------------------


@then("an httpx.HTTPStatusError is raised")
def httpx_status_error_raised(state: ClientState) -> None:
    assert isinstance(state.raised, httpx.HTTPStatusError), (
        f"Expected HTTPStatusError, got {state.raised!r}"
    )


@then("a KeyError is raised")
def key_error_raised(state: ClientState) -> None:
    assert isinstance(state.raised, KeyError), f"Expected KeyError, got {state.raised!r}"


# ---------------------------------------------------------------------------
# Then — multi-app JWT iss assertion
# ---------------------------------------------------------------------------


@then(parsers.parse('the JWT iss claim used was "{expected_iss}"'))
def jwt_iss_was(state: ClientState, expected_iss: str) -> None:
    claims = (state.result or {}).get("jwt_claims", {})
    assert str(claims.get("iss")) == expected_iss, f"iss={claims.get('iss')!r} != {expected_iss!r}"


# ---------------------------------------------------------------------------
# Given / When / Then — Codex round 3: pull_request_reviews pagination
# ---------------------------------------------------------------------------


@given("GitHub returns 2 pages of PR reviews with 100 then 50 items")
def mock_paginated_reviews(state: ClientState) -> None:
    page1 = [
        {"id": i, "state": "COMMENTED", "submitted_at": "2024-01-01T00:00:00Z"} for i in range(100)
    ]
    page2 = [
        {"id": 100 + i, "state": "APPROVED", "submitted_at": "2024-01-02T00:00:00Z"}
        for i in range(50)
    ]
    existing = getattr(state, "_mock_responses", [])
    state._mock_responses = [  # type: ignore[attr-defined]
        *existing,
        _json_list_response(200, page1),
        _json_list_response(200, page2),
    ]


@when(
    parsers.parse('pull_request_reviews is called for "{repo}" PR {pr_number:d}'),
    target_fixture="reviews_result",
)
def call_pull_request_reviews(state: ClientState, repo: str, pr_number: int) -> list[dict]:
    import asyncio

    responses = getattr(state, "_mock_responses", [])
    state.client = _build_client(state, responses)
    return asyncio.get_event_loop().run_until_complete(
        state.client.pull_request_reviews("test-bot", repo, pr_number)
    )


@then(parsers.parse("pull_request_reviews returned {expected:d} items"))
def reviews_returned_count(reviews_result: list, expected: int) -> None:
    assert len(reviews_result) == expected, (
        f"pull_request_reviews returned {len(reviews_result)} items, expected {expected}"
    )


@then(parsers.parse("the reviews endpoint was called {expected:d} times"))
def reviews_endpoint_call_count(state: ClientState, expected: int) -> None:
    review_calls = [r for r in state.captured_requests if "/reviews" in str(r.url)]
    actual = len(review_calls)
    urls = [str(r.url) for r in review_calls]
    assert actual == expected, (
        f"reviews endpoint called {actual} times, expected {expected}: {urls}"
    )


# ---------------------------------------------------------------------------
# Given / When / Then — Codex round 5 P2: issue_comments pagination
# ---------------------------------------------------------------------------


@given("GitHub returns 2 pages of issue comments with 100 then 30 items")
def mock_paginated_issue_comments(state: ClientState) -> None:
    page1 = [{"id": i, "body": f"comment-{i}", "user": {"login": "alice"}} for i in range(100)]
    page2 = [
        {"id": 100 + i, "body": f"comment-{100 + i}", "user": {"login": "bob"}} for i in range(30)
    ]
    existing = getattr(state, "_mock_responses", [])
    state._mock_responses = [  # type: ignore[attr-defined]
        *existing,
        _json_list_response(200, page1),
        _json_list_response(200, page2),
    ]


@when(
    parsers.parse('issue_comments is called for "{repo}" issue {issue_number:d}'),
    target_fixture="comments_result",
)
def call_issue_comments(state: ClientState, repo: str, issue_number: int) -> list[dict]:
    import asyncio

    responses = getattr(state, "_mock_responses", [])
    state.client = _build_client(state, responses)
    return asyncio.get_event_loop().run_until_complete(
        state.client.issue_comments("test-bot", repo, issue_number)
    )


@then(parsers.parse("issue_comments returned {expected:d} items"))
def comments_returned_count(comments_result: list, expected: int) -> None:
    assert len(comments_result) == expected, (
        f"issue_comments returned {len(comments_result)} items, expected {expected}"
    )


@then(parsers.parse("the comments endpoint was called {expected:d} times"))
def comments_endpoint_call_count(state: ClientState, expected: int) -> None:
    comment_calls = [r for r in state.captured_requests if "/comments" in str(r.url)]
    actual = len(comment_calls)
    urls = [str(r.url) for r in comment_calls]
    assert actual == expected, (
        f"comments endpoint called {actual} times, expected {expected}: {urls}"
    )
