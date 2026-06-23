from __future__ import annotations

import httpx
import pytest

from voyager.core.github_app_user_auth import (
    GitHubUserAccessClient,
    exchange_device_code,
    query_viewer_login,
    refresh_user_access_token,
    request_device_code,
)


@pytest.mark.asyncio
async def test_request_device_code_public_dict_redacts_device_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/login/device/code"
        return httpx.Response(
            200,
            json={
                "device_code": "secret-device-code",
                "user_code": "ABCD-1234",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await request_device_code("client-id", client=client)

    assert response.device_code == "secret-device-code"
    assert response.to_public_dict() == {
        "user_code": "ABCD-1234",
        "verification_uri": "https://github.com/login/device",
        "expires_in": 900,
        "interval": 5,
    }


@pytest.mark.asyncio
async def test_request_device_code_reports_oauth_errors_before_success_parsing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/login/device/code"
        return httpx.Response(
            200,
            json={
                "error": "device_flow_disabled",
                "error_description": "Device flow is disabled for client-id",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError) as exc_info:
            await request_device_code("client-id", client=client)

    message = str(exc_info.value)
    assert message == "GitHub device authorization failed: device_flow_disabled"
    assert "client-id" not in message
    assert "Device flow is disabled" not in message


@pytest.mark.asyncio
async def test_request_device_code_normalizes_http_status_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/login/device/code"
        return httpx.Response(
            503,
            request=request,
            json={"message": "service unavailable for client-id"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError) as exc_info:
            await request_device_code("client-id", client=client)

    message = str(exc_info.value)
    assert message == "GitHub device authorization failed: HTTP 503"
    assert "client-id" not in message
    assert "service unavailable" not in message


@pytest.mark.asyncio
async def test_request_device_code_normalizes_request_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("could not connect with client-id", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError) as exc_info:
            await request_device_code("client-id", client=client)

    message = str(exc_info.value)
    assert message == "GitHub device authorization failed: HTTP request error"
    assert "client-id" not in message
    assert "could not connect" not in message


@pytest.mark.asyncio
async def test_exchange_device_code_reports_safe_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/login/oauth/access_token"
        return httpx.Response(
            200,
            json={
                "access_token": "ghu_secret_access",
                "token_type": "bearer",
                "expires_in": 28800,
                "refresh_token": "ghr_secret_refresh",
                "refresh_token_expires_in": 15897600,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await exchange_device_code("client-id", "device-code", client=client)

    assert response.access_token == "ghu_secret_access"
    assert response.refresh_token == "ghr_secret_refresh"
    assert response.to_public_dict() == {
        "token_type": "bearer",
        "expires_in": 28800,
        "refresh_token_present": True,
        "refresh_token_expires_in": 15897600,
        "scope": None,
    }


@pytest.mark.asyncio
async def test_exchange_device_code_sends_repository_id_when_provided() -> None:
    seen_body = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_body
        seen_body = request.content
        return httpx.Response(
            200,
            json={
                "access_token": "ghu_secret_access",
                "token_type": "bearer",
                "expires_in": 28800,
                "refresh_token": "ghr_secret_refresh",
                "refresh_token_expires_in": 15897600,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await exchange_device_code(
            "client-id",
            "device-code",
            repository_id=12345,
            client=client,
        )

    assert b"repository_id=12345" in seen_body


@pytest.mark.asyncio
async def test_exchange_device_code_normalizes_http_status_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/login/oauth/access_token"
        return httpx.Response(
            429,
            request=request,
            json={"message": "rate limited", "device_code": "secret-device"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError) as exc_info:
            await exchange_device_code("client-id", "secret-device", client=client)

    message = str(exc_info.value)
    assert message == "GitHub device authorization not complete: HTTP 429"
    assert "secret-device" not in message
    assert "rate limited" not in message


@pytest.mark.asyncio
async def test_exchange_device_code_normalizes_request_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("could not connect with secret-device", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError) as exc_info:
            await exchange_device_code("client-id", "secret-device", client=client)

    message = str(exc_info.value)
    assert message == "GitHub device authorization not complete: HTTP request error"
    assert "secret-device" not in message
    assert "could not connect" not in message


@pytest.mark.asyncio
async def test_exchange_device_code_normalizes_malformed_success_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/login/oauth/access_token"
        return httpx.Response(200, content=b"not-json secret-device")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError) as exc_info:
            await exchange_device_code("client-id", "secret-device", client=client)

    message = str(exc_info.value)
    assert message == "GitHub device authorization not complete: malformed response"
    assert "secret-device" not in message
    assert "not-json" not in message


@pytest.mark.asyncio
async def test_refresh_user_access_token_uses_refresh_grant() -> None:
    seen_body = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_body
        seen_body = request.content
        return httpx.Response(
            200,
            json={
                "access_token": "ghu_new_access",
                "token_type": "bearer",
                "expires_in": 28800,
                "refresh_token": "ghr_new_refresh",
                "refresh_token_expires_in": 15897600,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await refresh_user_access_token("client-id", "old-refresh", client=client)

    assert b"grant_type=refresh_token" in seen_body
    assert b"refresh_token=old-refresh" in seen_body
    assert response.access_token == "ghu_new_access"
    assert response.refresh_token == "ghr_new_refresh"


@pytest.mark.asyncio
async def test_refresh_user_access_token_normalizes_http_status_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/login/oauth/access_token"
        return httpx.Response(
            401,
            request=request,
            headers={
                "content-type": "application/json; charset=utf-8",
                "x-github-request-id": "ABC1:DEF2:345",
            },
            json={"message": "bad refresh token", "token": "old-refresh"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError) as exc_info:
            await refresh_user_access_token("client-id", "old-refresh", client=client)

    message = str(exc_info.value)
    assert message.startswith("GitHub refresh failed: HTTP 401")
    assert "content_type=application/json__charset_utf-8" in message
    assert "x_github_request_id=ABC1_DEF2_345" in message
    assert "client_secret_present=false" in message
    assert "repository_id_present=false" in message
    assert "refresh_token_present=true" in message
    assert "old-refresh" not in message
    assert "bad refresh token" not in message


@pytest.mark.asyncio
async def test_refresh_user_access_token_diagnoses_oauth_json_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/login/oauth/access_token"
        return httpx.Response(
            200,
            request=request,
            headers={
                "content-type": "application/json; charset=utf-8",
                "x-github-request-id": "ABC1:DEF2:346",
            },
            json={
                "error": "incorrect_client_credentials",
                "error_description": "old-refresh",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError) as exc_info:
            await refresh_user_access_token("client-id", "old-refresh", client=client)

    message = str(exc_info.value)
    assert message.startswith("GitHub refresh failed: HTTP 200")
    assert "content_type=application/json__charset_utf-8" in message
    assert "x_github_request_id=ABC1_DEF2_346" in message
    assert "client_secret_present=false" in message
    assert "repository_id_present=false" in message
    assert "refresh_token_present=true" in message
    assert "oauth_error=incorrect_client_credentials" in message
    assert "old-refresh" not in message


@pytest.mark.asyncio
async def test_refresh_user_access_token_normalizes_request_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("could not connect with old-refresh", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError) as exc_info:
            await refresh_user_access_token("client-id", "old-refresh", client=client)

    message = str(exc_info.value)
    assert message == "GitHub refresh failed: HTTP request error"
    assert "old-refresh" not in message
    assert "could not connect" not in message


@pytest.mark.asyncio
async def test_refresh_user_access_token_normalizes_malformed_json_success_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/login/oauth/access_token"
        return httpx.Response(200, content=b"not-json old-refresh")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError) as exc_info:
            await refresh_user_access_token("client-id", "old-refresh", client=client)

    message = str(exc_info.value)
    assert message == "GitHub refresh failed: malformed response"
    assert "old-refresh" not in message
    assert "not-json" not in message


@pytest.mark.asyncio
async def test_refresh_user_access_token_normalizes_missing_token_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/login/oauth/access_token"
        return httpx.Response(
            200,
            json={"token_type": "bearer", "message": "old-refresh"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError) as exc_info:
            await refresh_user_access_token("client-id", "old-refresh", client=client)

    message = str(exc_info.value)
    assert message == "GitHub refresh failed: malformed response"


@pytest.mark.asyncio
async def test_user_access_client_reports_sanitized_graphql_errors() -> None:
    md_legacy_thread_id = "MDExOlB1bGxSZXF1ZXN0UmV2aWV3VGhyZWFkMTIzNDU2"
    non_md_legacy_thread_id = "OTk6UHVsbFJlcXVlc3RSZXZpZXdUaHJlYWQxMjM0NTY="

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/graphql"
        assert request.headers["authorization"] == "Bearer secret-access"
        return httpx.Response(
            200,
            json={
                "data": {"resolveReviewThread": None},
                "errors": [
                    {
                        "type": "FORBIDDEN",
                        "message": (
                            "Resource not accessible by integration PRRT_secret "
                            f"{md_legacy_thread_id} {non_md_legacy_thread_id}"
                        ),
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = GitHubUserAccessClient("secret-access", client=http)
        with pytest.raises(RuntimeError) as exc_info:
            await client.graphql(
                "github-app-user",
                "iterwheel/voyager-sandbox",
                query="query { viewer { login } }",
                variables={},
            )

    message = str(exc_info.value)
    assert "first_type=FORBIDDEN" in message
    assert (
        "first_message=Resource_not_accessible_by_integration_PRRT_redacted_"
        "NODEID_redacted_NODEID_redacted"
    ) in message
    assert "secret-access" not in message
    assert "PRRT_secret" not in message
    assert md_legacy_thread_id not in message
    assert non_md_legacy_thread_id not in message
    assert "old-refresh" not in message


@pytest.mark.asyncio
async def test_query_viewer_login_returns_actor_without_public_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer ghu_access"
        return httpx.Response(200, json={"data": {"viewer": {"login": "maintainer"}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await query_viewer_login("ghu_access", client=client) == "maintainer"


@pytest.mark.asyncio
async def test_query_viewer_login_normalizes_http_status_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/graphql"
        return httpx.Response(
            401,
            request=request,
            json={"message": "bad viewer token", "token": "ghu_access"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError) as exc_info:
            await query_viewer_login("ghu_access", client=client)

    message = str(exc_info.value)
    assert message == "GitHub GraphQL viewer query failed: HTTP 401"
    assert "ghu_access" not in message
    assert "bad viewer token" not in message


@pytest.mark.asyncio
async def test_query_viewer_login_normalizes_request_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("could not connect with ghu_access", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError) as exc_info:
            await query_viewer_login("ghu_access", client=client)

    message = str(exc_info.value)
    assert message == "GitHub GraphQL viewer query failed: HTTP request error"
    assert "ghu_access" not in message
    assert "could not connect" not in message
