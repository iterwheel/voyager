from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

GITHUB_LOGIN = "https://github.com/login"
GITHUB_API = "https://api.github.com"


@dataclass(frozen=True)
class DeviceCodeResponse:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "user_code": self.user_code,
            "verification_uri": self.verification_uri,
            "expires_in": self.expires_in,
            "interval": self.interval,
        }


@dataclass(frozen=True)
class UserAccessTokenResponse:
    access_token: str
    token_type: str
    expires_in: int | None
    refresh_token: str | None
    refresh_token_expires_in: int | None
    scope: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "token_type": self.token_type,
            "expires_in": self.expires_in,
            "refresh_token_present": bool(self.refresh_token),
            "refresh_token_expires_in": self.refresh_token_expires_in,
            "scope": self.scope,
        }


async def request_device_code(
    client_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> DeviceCodeResponse:
    """Start GitHub's device authorization flow for a GitHub App client ID.

    The returned ``device_code`` is intentionally not included in public output;
    callers should keep it in memory and exchange it only with GitHub.
    """
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=15)
    try:
        response = await http.post(
            f"{GITHUB_LOGIN}/device/code",
            headers={"Accept": "application/json"},
            data={"client_id": client_id},
        )
        response.raise_for_status()
        data = response.json()
        return DeviceCodeResponse(
            device_code=str(data["device_code"]),
            user_code=str(data["user_code"]),
            verification_uri=str(data["verification_uri"]),
            expires_in=int(data["expires_in"]),
            interval=int(data.get("interval", 5)),
        )
    finally:
        if owns_client:
            await http.aclose()


async def exchange_device_code(
    client_id: str,
    device_code: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> UserAccessTokenResponse:
    """Exchange a GitHub device code for a user access token response."""
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=15)
    try:
        response = await http.post(
            f"{GITHUB_LOGIN}/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        response.raise_for_status()
        data = response.json()
        if data.get("error"):
            error = data.get("error")
            raise RuntimeError(f"GitHub device authorization not complete: {error}")
        return _parse_user_access_token_response(data)
    finally:
        if owns_client:
            await http.aclose()


async def refresh_user_access_token(
    client_id: str,
    refresh_token: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> UserAccessTokenResponse:
    """Refresh a GitHub App user access token without logging token values."""
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=15)
    try:
        response = await http.post(
            f"{GITHUB_LOGIN}/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        response.raise_for_status()
        data = response.json()
        if data.get("error"):
            error = data.get("error")
            raise RuntimeError(f"GitHub refresh failed: {error}")
        return _parse_user_access_token_response(data)
    finally:
        if owns_client:
            await http.aclose()


async def query_viewer_login(
    access_token: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Return the GitHub viewer login for a user access token."""
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=15)
    try:
        response = await http.post(
            f"{GITHUB_API}/graphql",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {access_token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"query": "query { viewer { login } }"},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("errors"):
            raise RuntimeError("GitHub GraphQL viewer query returned errors")
        return str(((data.get("data") or {}).get("viewer") or {}).get("login") or "")
    finally:
        if owns_client:
            await http.aclose()


def _parse_user_access_token_response(data: dict[str, Any]) -> UserAccessTokenResponse:
    return UserAccessTokenResponse(
        access_token=str(data["access_token"]),
        token_type=str(data.get("token_type") or "bearer"),
        expires_in=int(data["expires_in"]) if data.get("expires_in") is not None else None,
        refresh_token=str(data["refresh_token"]) if data.get("refresh_token") else None,
        refresh_token_expires_in=(
            int(data["refresh_token_expires_in"])
            if data.get("refresh_token_expires_in") is not None
            else None
        ),
        scope=str(data["scope"]) if data.get("scope") is not None else None,
    )
