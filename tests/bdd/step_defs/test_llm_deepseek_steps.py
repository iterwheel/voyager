"""Step definitions for DeepSeek LLM adapter BDD scenarios."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

# CRITICAL: do NOT import from voyager.* at module top level — those modules
# don't have implementations yet, so top-level imports would crash pytest
# collection. Import lazily INSIDE step functions instead.

scenarios("../features/llm_deepseek.feature")

FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures" / "llm"

# ---------------------------------------------------------------------------
# Assumption flags (Wave 3B)
#
# ASSUMED: DeepSeekClient exposes a testability seam `_async_client()` that
# returns an httpx.AsyncClient.  The openai SDK accepts an `http_client`
# kwarg on AsyncOpenAI — our adapter must thread the seam through there.
# We subclass DeepSeekClient and override `_async_client()` to inject a
# MockTransport, exactly as done for GitHubAppClient in test_github_app_steps.
#
# ASSUMED: extra_body is passed through to the openai SDK as-is.  To inspect
# it we capture the raw httpx request body and JSON-decode it.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _load_fixture(name: str) -> dict[str, Any]:
    path = FIXTURES_DIR / f"{name}.json"
    return json.loads(path.read_text())


def _json_response(status: int, body: dict[str, Any] | str) -> httpx.Response:
    if isinstance(body, str):
        content = body.encode()
        headers = {"Content-Type": "application/json"}
    else:
        content = json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
    return httpx.Response(status_code=status, headers=headers, content=content)


def _fixture_response(fixture_name: str) -> httpx.Response:
    return _json_response(200, _load_fixture(fixture_name))


def _error_response(status: int) -> httpx.Response:
    body = {"error": {"message": f"HTTP {status} error", "type": "api_error", "code": status}}
    return _json_response(status, body)


def _make_recording_transport(
    responses: list[httpx.Response],
    captured_requests: list[httpx.Request],
) -> httpx.MockTransport:
    """MockTransport that records every request and serves responses in order."""
    index = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        i = index[0]
        if i < len(responses):
            index[0] += 1
            return responses[i]
        raise RuntimeError(f"Unexpected HTTP call #{i + 1}: {request.method} {request.url}")

    return httpx.MockTransport(handler)


def _make_timeout_transport(captured_requests: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        raise httpx.ReadTimeout("timed out", request=request)

    return httpx.MockTransport(handler)


def _make_malformed_transport(captured_requests: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(
            status_code=200,
            headers={"Content-Type": "application/json"},
            content=b"not valid json {{{",
        )

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# Shared scenario state container
# ---------------------------------------------------------------------------


@dataclass
class DSState:
    """Accumulated state across Given/When/Then steps in one scenario."""

    api_key: str = "sk-test-key"
    model: str = "deepseek-v4-pro"
    base_url: str = "https://api.deepseek.com"
    client: Any = None
    captured_requests: list[httpx.Request] = field(default_factory=list)
    mock_responses: list[httpx.Response] = field(default_factory=list)
    mock_transport_factory: Any = None  # callable(captured) -> MockTransport
    result: Any = None
    raised: BaseException | None = None


@pytest.fixture
def state() -> DSState:
    return DSState()


# ---------------------------------------------------------------------------
# Helper: build the patched DeepSeekClient with injected transport
# ---------------------------------------------------------------------------


def _build_client(s: DSState) -> Any:
    """Construct a DeepSeekClient with a patched _async_client() transport seam."""
    from voyager.llm.deepseek import DeepSeekClient  # lazy

    if s.mock_transport_factory is not None:
        transport = s.mock_transport_factory(s.captured_requests)
    else:
        transport = _make_recording_transport(s.mock_responses, s.captured_requests)

    class _PatchedClient(DeepSeekClient):
        def _async_client(self) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=transport, timeout=10)

    return _PatchedClient(api_key=s.api_key, base_url=s.base_url, model=s.model)


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------


@given(
    parsers.parse('a DeepSeekClient with api_key "{api_key}" and model "{model}"'),
    target_fixture="state",
)
def background_client(api_key: str, model: str) -> DSState:
    s = DSState()
    s.api_key = api_key
    s.model = model
    return s


# ---------------------------------------------------------------------------
# Given — mock API responses
# ---------------------------------------------------------------------------


@given("the DeepSeek API returns a thinking-enabled response")
def mock_thinking_enabled(state: DSState) -> None:
    state.mock_responses = [_fixture_response("deepseek_thinking_enabled")]


@given("the DeepSeek API returns a thinking-disabled response")
def mock_thinking_disabled(state: DSState) -> None:
    state.mock_responses = [_fixture_response("deepseek_thinking_disabled")]


@given("the DeepSeek API returns a reasoning_effort_high response")
def mock_effort_high(state: DSState) -> None:
    state.mock_responses = [_fixture_response("deepseek_reasoning_effort_high")]


@given("the DeepSeek API returns a reasoning_effort_max response")
def mock_effort_max(state: DSState) -> None:
    state.mock_responses = [_fixture_response("deepseek_reasoning_effort_max")]


@given("the DeepSeek API returns a multi-turn response")
def mock_multi_turn(state: DSState) -> None:
    state.mock_responses = [_fixture_response("deepseek_multi_turn_with_reasoning")]


@given("the DeepSeek API returns a 400 error for missing reasoning_content")
def mock_400(state: DSState) -> None:
    body = {
        "error": {
            "message": "400 Bad Request: reasoning_content is required for assistant turns on V4",
            "type": "invalid_request_error",
            "code": 400,
        }
    }
    state.mock_responses = [_json_response(400, body)]


@given("the DeepSeek API returns a tool-call response with thinking")
def mock_tool_call(state: DSState) -> None:
    state.mock_responses = [_fixture_response("deepseek_tool_call_with_thinking")]


@given("the DeepSeek API returns a tool-result follow-up response")
def mock_tool_result_follow_up(state: DSState) -> None:
    state.mock_responses = [_fixture_response("deepseek_tool_result_follow_up")]


@given("the DeepSeek API returns a 401 response")
def mock_401(state: DSState) -> None:
    state.mock_responses = [_error_response(401)]


@given("the DeepSeek API returns a 429 response")
def mock_429(state: DSState) -> None:
    state.mock_responses = [_error_response(429)]


@given("the DeepSeek API returns a 500 response")
def mock_500(state: DSState) -> None:
    state.mock_responses = [_error_response(500)]


@given("the DeepSeek API raises a timeout")
def mock_timeout(state: DSState) -> None:
    state.mock_transport_factory = _make_timeout_transport


@given("the DeepSeek API returns malformed JSON")
def mock_malformed_json(state: DSState) -> None:
    state.mock_transport_factory = _make_malformed_transport


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


def _run(coro: Any) -> Any:
    """Drive an async coroutine from a sync pytest-bdd step.

    ``asyncio.run`` is the documented replacement for the deprecated
    ``asyncio.get_event_loop().run_until_complete(...)`` pattern. The latter
    leaks event-loop state across tests in Python 3.14 — a previous
    ``asyncio.run`` closes the thread's loop, then ``get_event_loop()`` in a
    subsequent test returns the closed loop and ``run_until_complete`` errors
    with "Event loop is closed". GLM-5.1 H4 review flag, expanded to every
    step-def helper that drives async code.
    """
    return asyncio.run(coro)


@when(parsers.parse('complete is called with a user message "{text}"'))
def call_complete_simple(state: DSState, text: str) -> None:
    from voyager.llm.deepseek import Message  # lazy

    state.client = _build_client(state)
    messages = [Message(role="user", content=text)]
    try:
        state.result = _run(state.client.complete(messages))
    except Exception as exc:
        state.raised = exc


@when("complete is called with thinking enabled")
def call_complete_thinking_on(state: DSState) -> None:
    from voyager.llm.deepseek import Message  # lazy

    state.client = _build_client(state)
    messages = [Message(role="user", content="What is the capital of France?")]
    try:
        state.result = _run(state.client.complete(messages, thinking=True))
    except Exception as exc:
        state.raised = exc


@when("complete is called with thinking disabled")
def call_complete_thinking_off(state: DSState) -> None:
    from voyager.llm.deepseek import Message  # lazy

    state.client = _build_client(state)
    messages = [Message(role="user", content="What is the capital of France?")]
    try:
        state.result = _run(state.client.complete(messages, thinking=False))
    except Exception as exc:
        state.raised = exc


@when(parsers.parse('complete is called with thinking enabled and reasoning_effort "{effort}"'))
def call_complete_with_effort(state: DSState, effort: str) -> None:
    from voyager.llm.deepseek import Message  # lazy

    state.client = _build_client(state)
    messages = [Message(role="user", content="What is the capital of France?")]
    try:
        state.result = _run(state.client.complete(messages, thinking=True, reasoning_effort=effort))
    except Exception as exc:
        state.raised = exc


@when("complete is called with a prior assistant turn carrying reasoning_content")
def call_complete_multiturn_with_reasoning(state: DSState) -> None:
    from voyager.llm.deepseek import Message  # lazy

    state.client = _build_client(state)
    messages = [
        Message(role="user", content="What is the capital of France?"),
        Message(
            role="assistant",
            content="The capital of France is Paris.",
            reasoning_content="I recalled that Paris is the capital of France.",
        ),
        Message(role="user", content="What is its population?"),
    ]
    try:
        state.result = _run(state.client.complete(messages, thinking=True))
    except Exception as exc:
        state.raised = exc


@when("complete is called with a prior assistant turn missing reasoning_content")
def call_complete_multiturn_without_reasoning(state: DSState) -> None:
    from voyager.llm.deepseek import Message  # lazy

    state.client = _build_client(state)
    messages = [
        Message(role="user", content="What is the capital of France?"),
        # reasoning_content intentionally omitted — should cause 400
        Message(role="assistant", content="The capital of France is Paris."),
        Message(role="user", content="What is its population?"),
    ]
    try:
        state.result = _run(state.client.complete(messages, thinking=True))
    except Exception as exc:
        state.raised = exc


@when("complete is called with thinking enabled and a tool definition")
def call_complete_with_tools(state: DSState) -> None:
    from voyager.llm.deepseek import Message  # lazy

    state.client = _build_client(state)
    messages = [Message(role="user", content="Post a review comment on PR #42.")]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "post_pr_comment",
                "description": "Post a comment on a pull request.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "body": {"type": "string"},
                        "pr_number": {"type": "integer"},
                    },
                    "required": ["body", "pr_number"],
                },
            },
        }
    ]
    try:
        state.result = _run(state.client.complete(messages, thinking=True, tools=tools))
    except Exception as exc:
        state.raised = exc


@when("complete is called with a tool result message in the history")
def call_complete_with_tool_result(state: DSState) -> None:
    from voyager.llm.deepseek import Message  # lazy

    state.client = _build_client(state)
    messages = [
        Message(role="user", content="Post a review comment on PR #42."),
        Message(
            role="assistant",
            reasoning_content="I should call post_pr_comment.",
            content=None,
            tool_calls=None,
        ),
        Message(
            role="tool",
            tool_call_id="call_abc123",
            content="Comment posted successfully.",
        ),
    ]
    try:
        state.result = _run(state.client.complete(messages, thinking=True))
    except Exception as exc:
        state.raised = exc


@when("complete is called with an assistant turn containing tool_calls and a tool result")
def call_complete_with_assistant_tool_calls(state: DSState) -> None:
    from voyager.llm.deepseek import Message, ToolCall  # lazy

    state.client = _build_client(state)
    messages = [
        Message(role="user", content="Post a review comment on PR #42."),
        Message(
            role="assistant",
            reasoning_content="I should call post_pr_comment.",
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_abc123",
                    name="post_pr_comment",
                    arguments={"pr_number": 42, "body": "LGTM"},
                ),
            ],
        ),
        Message(
            role="tool",
            tool_call_id="call_abc123",
            content="Comment posted successfully.",
        ),
    ]
    try:
        state.result = _run(state.client.complete(messages, thinking=True))
    except Exception as exc:
        state.raised = exc


# ---------------------------------------------------------------------------
# Then — request assertions
# ---------------------------------------------------------------------------


def _last_request(state: DSState) -> httpx.Request:
    assert state.captured_requests, "No HTTP requests were captured"
    return state.captured_requests[-1]


def _last_request_body(state: DSState) -> dict[str, Any]:
    req = _last_request(state)
    return json.loads(req.content)


@then(parsers.parse('the request Authorization header is "{expected}"'))
def request_auth_header(state: DSState, expected: str) -> None:
    req = _last_request(state)
    auth = req.headers.get("authorization", "")
    assert auth == expected, f"Authorization header {auth!r} != {expected!r}"


@then(parsers.parse('the request was sent to a URL containing "{fragment}"'))
def request_url_contains(state: DSState, fragment: str) -> None:
    req = _last_request(state)
    assert fragment in str(req.url), f"{fragment!r} not in {req.url!r}"


@then(parsers.parse('the request body model is "{model}"'))
def request_body_model(state: DSState, model: str) -> None:
    body = _last_request_body(state)
    assert body.get("model") == model, f"model={body.get('model')!r} != {model!r}"


@then(parsers.parse('the request extra_body thinking type is "{thinking_type}"'))
def request_extra_body_thinking(state: DSState, thinking_type: str) -> None:
    body = _last_request_body(state)
    actual = body.get("thinking", {}).get("type")
    assert actual == thinking_type, f"thinking.type={actual!r} != {thinking_type!r}"


@then(parsers.parse('the request extra_body reasoning_effort is "{effort}"'))
def request_extra_body_effort(state: DSState, effort: str) -> None:
    body = _last_request_body(state)
    actual = body.get("reasoning_effort")
    assert actual == effort, f"reasoning_effort={actual!r} != {effort!r}"


@then("the request messages include an assistant message with reasoning_content")
def request_has_assistant_reasoning(state: DSState) -> None:
    body = _last_request_body(state)
    messages = body.get("messages", [])
    assistant_msgs = [m for m in messages if m.get("role") == "assistant"]
    assert assistant_msgs, "No assistant messages in request"
    has_reasoning = any(m.get("reasoning_content") not in (None, "") for m in assistant_msgs)
    assert has_reasoning, (
        f"No assistant message with reasoning_content found. Messages: {assistant_msgs}"
    )


@then("the request messages include an assistant message with tool_calls")
def request_has_assistant_tool_calls(state: DSState) -> None:
    body = _last_request_body(state)
    messages = body.get("messages", [])
    assistant_msgs = [m for m in messages if m.get("role") == "assistant"]
    assert assistant_msgs, "No assistant messages in request"
    with_tool_calls = [m for m in assistant_msgs if m.get("tool_calls")]
    assert with_tool_calls, (
        f"No assistant message with tool_calls found. Messages: {assistant_msgs}"
    )


@then(parsers.parse('the first request assistant tool_call has id "{tc_id}" name "{tc_name}"'))
def request_first_assistant_tool_call(state: DSState, tc_id: str, tc_name: str) -> None:
    body = _last_request_body(state)
    messages = body.get("messages", [])
    assistant_msgs = [m for m in messages if m.get("role") == "assistant" and m.get("tool_calls")]
    assert assistant_msgs, "No assistant message with tool_calls"
    first = assistant_msgs[0]["tool_calls"][0]
    assert first.get("id") == tc_id, f"id={first.get('id')!r} != {tc_id!r}"
    func = first.get("function", {})
    assert func.get("name") == tc_name, f"function.name={func.get('name')!r} != {tc_name!r}"


@then(
    parsers.parse('the first request assistant tool_call arguments JSON includes "{key}" {value:d}')
)
def request_first_assistant_tool_call_args(state: DSState, key: str, value: int) -> None:
    body = _last_request_body(state)
    messages = body.get("messages", [])
    assistant_msgs = [m for m in messages if m.get("role") == "assistant" and m.get("tool_calls")]
    assert assistant_msgs, "No assistant message with tool_calls"
    first = assistant_msgs[0]["tool_calls"][0]
    args_raw = first.get("function", {}).get("arguments", "")
    args = json.loads(args_raw)
    assert args.get(key) == value, (
        f"tool_call.arguments[{key!r}] = {args.get(key)!r}, expected {value}"
    )


@then(parsers.parse('the request messages include a tool role message with tool_call_id "{tc_id}"'))
def request_has_tool_message(state: DSState, tc_id: str) -> None:
    body = _last_request_body(state)
    messages = body.get("messages", [])
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert tool_msgs, "No tool role messages in request"
    matching = [m for m in tool_msgs if m.get("tool_call_id") == tc_id]
    assert matching, (
        f"No tool message with tool_call_id={tc_id!r} found. Tool messages: {tool_msgs}"
    )


@then("the request body includes tools")
def request_body_has_tools(state: DSState) -> None:
    body = _last_request_body(state)
    tools = body.get("tools")
    assert tools, "No tools found in request body"


# ---------------------------------------------------------------------------
# Then — AssistantTurn assertions
# ---------------------------------------------------------------------------


@then("the AssistantTurn has a non-empty reasoning_content")
def turn_has_reasoning(state: DSState) -> None:
    assert state.result is not None, "complete() raised an exception, no result"
    rc = state.result.reasoning_content
    assert rc is not None, f"reasoning_content is None: {rc!r}"
    assert rc != "", f"reasoning_content is empty: {rc!r}"


@then("the AssistantTurn reasoning_content is None")
def turn_reasoning_is_none(state: DSState) -> None:
    assert state.result is not None, "complete() raised an exception, no result"
    assert state.result.reasoning_content is None, (
        f"Expected reasoning_content=None, got {state.result.reasoning_content!r}"
    )


@then("the AssistantTurn has a non-empty content")
def turn_has_content(state: DSState) -> None:
    assert state.result is not None, "complete() raised an exception, no result"
    c = state.result.content
    assert c is not None, f"content is None: {c!r}"
    assert c != "", f"content is empty: {c!r}"


@then("the AssistantTurn has no tool_calls")
def turn_no_tool_calls(state: DSState) -> None:
    assert state.result is not None, "complete() raised an exception, no result"
    tc = state.result.tool_calls
    assert not tc, f"Expected no tool_calls, got {tc!r}"


@then("the AssistantTurn has tool_calls")
def turn_has_tool_calls(state: DSState) -> None:
    assert state.result is not None, "complete() raised an exception, no result"
    tc = state.result.tool_calls
    assert tc, f"Expected tool_calls to be non-empty, got {tc!r}"


@then(parsers.parse('the first tool_call name is "{name}"'))
def first_tool_call_name(state: DSState, name: str) -> None:
    tc = state.result.tool_calls[0]
    assert tc.name == name, f"tool_call.name={tc.name!r} != {name!r}"


@then(parsers.parse('the first tool_call id is "{tc_id}"'))
def first_tool_call_id(state: DSState, tc_id: str) -> None:
    tc = state.result.tool_calls[0]
    assert tc.id == tc_id, f"tool_call.id={tc.id!r} != {tc_id!r}"


@then("the first tool_call arguments are parsed as a dict")
def first_tool_call_args_are_dict(state: DSState) -> None:
    tc = state.result.tool_calls[0]
    assert isinstance(tc.arguments, dict), (
        f"tool_call.arguments should be dict, got {type(tc.arguments)}"
    )


# ---------------------------------------------------------------------------
# Then — error assertions
# ---------------------------------------------------------------------------


@then(parsers.parse('an error is raised mentioning "{fragment}"'))
def error_raised_mentioning(state: DSState, fragment: str) -> None:
    assert state.raised is not None, "Expected an exception but none was raised"
    assert fragment in str(state.raised), f"{fragment!r} not found in exception: {state.raised!r}"


@then("a timeout error is raised")
def timeout_error_raised(state: DSState) -> None:
    assert state.raised is not None, "Expected a timeout exception but none was raised"
    assert isinstance(state.raised, (httpx.TimeoutException, TimeoutError)), (
        f"Expected a timeout error, got {type(state.raised)}: {state.raised!r}"
    )


@then("a JSON decode error is raised")
def json_decode_error_raised(state: DSState) -> None:
    assert state.raised is not None, "Expected a JSON decode exception but none was raised"
    assert isinstance(state.raised, (json.JSONDecodeError, ValueError)), (
        f"Expected JSON decode error, got {type(state.raised)}: {state.raised!r}"
    )
