"""Step definitions for webhook server BDD scenarios."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

# CRITICAL: do NOT import from voyager.* at module top level — those modules
# don't have implementations yet, so top-level imports would crash pytest
# collection. Import lazily INSIDE step functions instead.

scenarios("../features/server.feature")

FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures" / "webhooks"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_fixture(name: str) -> dict[str, Any]:
    path = FIXTURES_DIR / f"{name}.json"
    return json.loads(path.read_text())


def _sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _make_client():
    from fastapi.testclient import TestClient

    from voyager.server import app  # lazy — module empty until Wave 3C impl

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Per-scenario mutable state
# ---------------------------------------------------------------------------


@pytest.fixture
def state() -> dict[str, Any]:
    return {
        "secret": "",
        "slug": "",
        "no_secrets": False,
        "request_method": "POST",
        "request_path": "/github/webhook",
        "request_body": b"",
        "request_headers": {},
        "response": None,
        "extra_env": {},
        # match_signature unit-test sub-state
        "sig_body": b"",
        "sig_secret": "",
        "sig_slug": "",
        "sig_secrets": {},
        "sig_value": None,
        "sig_result": None,
        "sig_computed": None,
    }


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------


@given(
    parsers.parse('the webhook secret "{secret}" is configured for slug "{slug}"'),
    target_fixture="state",
)
def background_secret(secret: str, slug: str) -> dict[str, Any]:
    return {
        "secret": secret,
        "slug": slug,
        "no_secrets": False,
        "request_method": "POST",
        "request_path": "/github/webhook",
        "request_body": b"",
        "request_headers": {},
        "response": None,
        "extra_env": {},
        "sig_body": b"",
        "sig_secret": "",
        "sig_slug": "",
        "sig_secrets": {},
        "sig_value": None,
        "sig_result": None,
        "sig_computed": None,
    }


@given(parsers.parse('the webhook endpoint is "POST /github/webhook"'))
def background_endpoint(state: dict) -> None:
    state["request_path"] = "/github/webhook"


# ---------------------------------------------------------------------------
# Given — health check requests
# ---------------------------------------------------------------------------


@given(parsers.parse('a GET request is made to "{path}"'), target_fixture="state")
def get_health_request(state: dict, path: str) -> dict[str, Any]:
    # Re-used in When step — record intent, fire in When
    state["request_method"] = "GET"
    state["request_path"] = path
    return state


# ---------------------------------------------------------------------------
# Given — match_signature unit-test setups
# ---------------------------------------------------------------------------


@given(
    parsers.parse('body b"{raw_body}" signed with secret "{secret}" under slug "{slug}"'),
)
def sig_body_signed(state: dict, raw_body: str, secret: str, slug: str) -> None:
    body = raw_body.encode("utf-8")
    state["sig_body"] = body
    state["sig_secret"] = secret
    state["sig_slug"] = slug
    state["sig_secrets"] = {slug: secret}
    state["sig_value"] = _sign(secret, body)


@given(parsers.parse('body b"{raw_body}" signed with secret "{secret}" under slug "{slug}"'))
def sig_body_signed_alt(state: dict, raw_body: str, secret: str, slug: str) -> None:
    sig_body_signed(state, raw_body, secret, slug)


@given("any body and secrets dict with one entry")
def sig_any_body(state: dict) -> None:
    state["sig_body"] = b"anything"
    state["sig_secret"] = "some-secret"
    state["sig_slug"] = "some-slug"
    state["sig_secrets"] = {"some-slug": "some-secret"}
    state["sig_value"] = _sign("some-secret", b"anything")


# ---------------------------------------------------------------------------
# Given — no secrets configured
# ---------------------------------------------------------------------------


@given("no webhook secrets are configured")
def no_secrets_configured(state: dict) -> None:
    state["no_secrets"] = True


@given(parsers.parse('a raw signed payload for event "{event}" with delivery "{delivery}"'))
def raw_signed_payload(state: dict, event: str, delivery: str) -> None:
    body = json.dumps({"action": "opened"}).encode("utf-8")
    secret = state.get("secret", "test-secret-abc")
    sig = _sign(secret, body)
    state["request_body"] = body
    state["request_headers"] = {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": sig,
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Given — signed/unsigned webhook POST setups
# ---------------------------------------------------------------------------


@given(
    parsers.parse(
        'a signed webhook payload "{fixture}" for event "{event}" with delivery "{delivery}"'
    )
)
def signed_payload(state: dict, fixture: str, event: str, delivery: str) -> None:
    payload = _load_fixture(fixture)
    body = json.dumps(payload).encode("utf-8")
    secret = state["secret"]
    sig = _sign(secret, body)
    state["request_body"] = body
    state["request_headers"] = {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": sig,
        "Content-Type": "application/json",
    }


@given(
    parsers.parse(
        'a signed webhook payload "{fixture}" with no event header and delivery "{delivery}"'
    )
)
def signed_payload_no_event(state: dict, fixture: str, delivery: str) -> None:
    payload = _load_fixture(fixture)
    body = json.dumps(payload).encode("utf-8")
    secret = state["secret"]
    sig = _sign(secret, body)
    state["request_body"] = body
    state["request_headers"] = {
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": sig,
        "Content-Type": "application/json",
    }


@given(
    parsers.parse(
        'a signed webhook payload "{fixture}" for event "{event}" with no delivery header'
    )
)
def signed_payload_no_delivery(state: dict, fixture: str, event: str) -> None:
    payload = _load_fixture(fixture)
    body = json.dumps(payload).encode("utf-8")
    secret = state["secret"]
    sig = _sign(secret, body)
    state["request_body"] = body
    state["request_headers"] = {
        "X-GitHub-Event": event,
        "X-Hub-Signature-256": sig,
        "Content-Type": "application/json",
    }


@given(
    parsers.parse(
        'a webhook payload "{fixture}" with a wrong signature for event "{event}" with delivery "{delivery}"'
    )
)
def wrong_signature_payload(state: dict, fixture: str, event: str, delivery: str) -> None:
    payload = _load_fixture(fixture)
    body = json.dumps(payload).encode("utf-8")
    state["request_body"] = body
    state["request_headers"] = {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": "sha256=deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        "Content-Type": "application/json",
    }


@given(
    parsers.parse(
        'a webhook payload "{fixture}" with no signature header for event "{event}" with delivery "{delivery}"'
    )
)
def no_signature_payload(state: dict, fixture: str, event: str, delivery: str) -> None:
    payload = _load_fixture(fixture)
    body = json.dumps(payload).encode("utf-8")
    state["request_body"] = body
    state["request_headers"] = {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "Content-Type": "application/json",
    }


@given(parsers.parse('a signed non-JSON body for event "{event}" with delivery "{delivery}"'))
def signed_non_json_body(state: dict, event: str, delivery: str) -> None:
    body = b"this is not json {"
    secret = state["secret"]
    sig = _sign(secret, body)
    state["request_body"] = body
    state["request_headers"] = {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": sig,
        "Content-Type": "application/json",
    }


@given(parsers.parse('DRY_RUN is "{value}"'))
def dry_run_env(state: dict, value: str) -> None:
    state.setdefault("extra_env", {})["DRY_RUN"] = value


@given(parsers.parse('bridge allowed repositories is "{value}"'))
def bridge_allowed_repositories_env(state: dict, value: str) -> None:
    state.setdefault("extra_env", {})["BRIDGE_ALLOWED_REPOSITORIES"] = value


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when(parsers.parse('a GET request is made to "{path}"'), target_fixture="state")
def when_get(state: dict, path: str) -> dict[str, Any]:
    state["request_method"] = "GET"
    state["request_path"] = path
    client = _make_client()
    state["response"] = client.get(path)
    return state


@when("the webhook is POSTed", target_fixture="state")
def when_post_webhook(state: dict) -> dict[str, Any]:
    env_patch: dict[str, str] = {}
    if state.get("no_secrets"):
        # Remove all secret env vars so configured_webhook_secrets() returns {}
        env_patch["GITHUB_REPOSITORY_WEBHOOK_SECRET"] = ""
    else:
        env_patch[f"GITHUB_WEBHOOK_SECRET_{state['slug'].upper().replace('-', '_')}"] = state[
            "secret"
        ]
        # The server reads GITHUB_REPOSITORY_WEBHOOK_SECRET as a fallback slug;
        # use it so we don't need a real AppConfig.
        env_patch["GITHUB_REPOSITORY_WEBHOOK_SECRET"] = state["secret"]

    # Keep server BDD hermetic even when the operator shell has production
    # rollout allow-list variables exported.
    for key in list(os.environ):
        if key == "BRIDGE_ALLOWED_REPOSITORIES" or key.startswith("BRIDGE_ALLOWED_REPOSITORIES_"):
            env_patch.setdefault(key, "")
    env_patch.update(state.get("extra_env") or {})

    original = {k: os.environ.get(k) for k in env_patch}
    for k, v in env_patch.items():
        if v:
            os.environ[k] = v
        elif k in os.environ:
            del os.environ[k]

    try:
        client = _make_client()
        state["response"] = client.post(
            state["request_path"],
            content=state["request_body"],
            headers=state["request_headers"],
        )
    finally:
        for k, original_val in original.items():
            if original_val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = original_val

    return state


# ---------------------------------------------------------------------------
# When — match_signature unit tests
# ---------------------------------------------------------------------------


@when("match_signature is called with that body, signature, and secrets")
def call_match_sig(state: dict) -> None:
    from voyager.core.security import match_signature  # lazy

    state["sig_result"] = match_signature(
        state["sig_body"], state["sig_value"], state["sig_secrets"]
    )


@when("match_signature is called with a tampered signature")
def call_match_sig_tampered(state: dict) -> None:
    from voyager.core.security import match_signature  # lazy

    tampered = "sha256=" + "ff" * 32
    state["sig_result"] = match_signature(state["sig_body"], tampered, state["sig_secrets"])


@when("match_signature is called with signature None")
def call_match_sig_none(state: dict) -> None:
    from voyager.core.security import match_signature  # lazy

    state["sig_result"] = match_signature(state["sig_body"], None, state["sig_secrets"])


@when("github_signature is computed for that body and secret")
def call_github_signature(state: dict) -> None:
    from voyager.core.security import github_signature  # lazy

    state["sig_computed"] = github_signature(state["sig_secret"], state["sig_body"])


# ---------------------------------------------------------------------------
# Then — HTTP response status
# ---------------------------------------------------------------------------


@then(parsers.parse("the response status is {code:d}"))
def response_status(state: dict, code: int) -> None:
    resp = state["response"]
    assert resp is not None, "No response recorded — was the When step run?"
    assert resp.status_code == code, f"Expected {code}, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# Then — response body key/value assertions
# ---------------------------------------------------------------------------


@then(parsers.parse('the response body contains key "{key}" with value true'))
def response_body_key_true(state: dict, key: str) -> None:
    body = state["response"].json()
    assert body[key] is True, f"body[{key!r}] = {body.get(key)!r}, expected True"


@then(parsers.parse('the response body contains key "{key}" with value "{value}"'))
def response_body_key_str(state: dict, key: str, value: str) -> None:
    body = state["response"].json()
    assert str(body[key]) == value, f"body[{key!r}] = {body.get(key)!r}, expected {value!r}"


@then(parsers.parse('the response body has a "{key}" field'))
def response_body_has_field(state: dict, key: str) -> None:
    body = state["response"].json()
    assert key in body, f"Key {key!r} not found in body: {list(body.keys())}"


# ---------------------------------------------------------------------------
# Then — response body typed field assertions
# ---------------------------------------------------------------------------


@then(parsers.parse('the response body "{key}" field is true'))
def response_body_field_true(state: dict, key: str) -> None:
    body = state["response"].json()
    assert body[key] is True, f"body[{key!r}] = {body.get(key)!r}, expected True"


@then(parsers.parse('the response body "{key}" field is false'))
def response_body_field_false(state: dict, key: str) -> None:
    body = state["response"].json()
    assert body[key] is False, f"body[{key!r}] = {body.get(key)!r}, expected False"


@then(parsers.parse('the response body "{key}" field is a list'))
def response_body_field_is_list(state: dict, key: str) -> None:
    body = state["response"].json()
    assert isinstance(body[key], list), f"body[{key!r}] = {body.get(key)!r}, expected list"


@then(parsers.parse('the response body "{key}" field is an empty list'))
def response_body_field_empty_list(state: dict, key: str) -> None:
    body = state["response"].json()
    val = body[key]
    assert isinstance(val, list), f"body[{key!r}] = {val!r}, expected list"
    assert val == [], f"body[{key!r}] = {val!r}, expected []"


@then(parsers.parse('the response body "{key}" field is the string "{expected}"'))
def response_body_field_is_string(state: dict, key: str, expected: str) -> None:
    body = state["response"].json()
    assert body[key] == expected, f"body[{key!r}] = {body.get(key)!r}, expected {expected!r}"


@then(parsers.parse('the response body "{key}" field has "{subkey}" equal to string "{expected}"'))
def response_body_subfield_string(state: dict, key: str, subkey: str, expected: str) -> None:
    body = state["response"].json()
    obj = body.get(key) or {}
    assert obj.get(subkey) == expected, (
        f"body[{key!r}][{subkey!r}] = {obj.get(subkey)!r}, expected string {expected!r}"
    )


@then(parsers.parse('the response body "{key}" field has "{subkey}" equal to integer {expected:d}'))
def response_body_subfield_int(state: dict, key: str, subkey: str, expected: int) -> None:
    body = state["response"].json()
    obj = body.get(key) or {}
    assert obj.get(subkey) == expected, (
        f"body[{key!r}][{subkey!r}] = {obj.get(subkey)!r}, expected integer {expected}"
    )


@then(parsers.parse('the response body "{key}" field has "{subkey}" greater than {threshold:d}'))
def response_body_subfield_gt(state: dict, key: str, subkey: str, threshold: int) -> None:
    body = state["response"].json()
    obj = body.get(key) or {}
    val = obj.get(subkey)
    assert isinstance(val, int), f"body[{key!r}][{subkey!r}] = {val!r}, expected int"
    assert val > threshold, f"body[{key!r}][{subkey!r}] = {val!r}, expected int > {threshold}"


# ---------------------------------------------------------------------------
# Then — dispatch routing assertions
# ---------------------------------------------------------------------------


@then(parsers.parse('at least one route targets agent "{agent_slug}"'))
def route_targets_agent(state: dict, agent_slug: str) -> None:
    body = state["response"].json()
    routes = body.get("routes", [])
    agents = [r.get("agent") for r in routes]
    assert any(a == agent_slug for a in agents), (
        f"No route targets {agent_slug!r}. Routes: {agents}"
    )


# ---------------------------------------------------------------------------
# Then — match_signature unit-test assertions
# ---------------------------------------------------------------------------


@then(parsers.parse('the returned slug is "{expected}"'))
def sig_returned_slug(state: dict, expected: str) -> None:
    assert state["sig_result"] == expected, (
        f"Expected slug {expected!r}, got {state['sig_result']!r}"
    )


@then("the returned slug is None")
def sig_returned_none(state: dict) -> None:
    assert state["sig_result"] is None, f"Expected None, got {state['sig_result']!r}"


@then(parsers.parse('the signature starts with "{prefix}"'))
def sig_starts_with(state: dict, prefix: str) -> None:
    computed = state["sig_computed"]
    assert computed.startswith(prefix), f"Signature {computed!r} does not start with {prefix!r}"


@then(parsers.parse('the part after "{prefix}" is {length:d} hex characters'))
def sig_hex_length(state: dict, prefix: str, length: int) -> None:
    computed = state["sig_computed"]
    after_prefix = computed[len(prefix) :]
    assert len(after_prefix) == length, (
        f"Expected {length} hex chars after {prefix!r}, got {len(after_prefix)}: {after_prefix!r}"
    )
    assert all(c in "0123456789abcdef" for c in after_prefix), f"Non-hex chars in {after_prefix!r}"
