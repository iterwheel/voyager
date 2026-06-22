from __future__ import annotations

import httpx
import pytest

from voyager.core.github_app_user_auth import (
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
async def test_query_viewer_login_returns_actor_without_public_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer ghu_access"
        return httpx.Response(200, json={"data": {"viewer": {"login": "maintainer"}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await query_viewer_login("ghu_access", client=client) == "maintainer"
