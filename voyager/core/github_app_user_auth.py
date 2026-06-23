from __future__ import annotations

import base64
import binascii
import re
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
        try:
            response = await http.post(
                f"{GITHUB_LOGIN}/device/code",
                headers={"Accept": "application/json"},
                data={"client_id": client_id},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            raise RuntimeError(f"GitHub device authorization failed: HTTP {status_code}") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("GitHub device authorization failed: HTTP request error") from exc
        data = _response_json_object(
            response,
            "GitHub device authorization failed: malformed response",
        )
        if data.get("error"):
            error = data.get("error")
            raise RuntimeError(f"GitHub device authorization failed: {error}")
        try:
            return DeviceCodeResponse(
                device_code=str(data["device_code"]),
                user_code=str(data["user_code"]),
                verification_uri=str(data["verification_uri"]),
                expires_in=int(data["expires_in"]),
                interval=int(data.get("interval", 5)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("GitHub device authorization failed: malformed response") from exc
    finally:
        if owns_client:
            await http.aclose()


async def exchange_device_code(
    client_id: str,
    device_code: str,
    repository_id: int | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> UserAccessTokenResponse:
    """Exchange a GitHub device code for a user access token response."""
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=15)
    try:
        request_data: dict[str, str | int] = {
            "client_id": client_id,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
        if repository_id is not None:
            request_data["repository_id"] = repository_id
        try:
            response = await http.post(
                f"{GITHUB_LOGIN}/oauth/access_token",
                headers={"Accept": "application/json"},
                data=request_data,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            raise RuntimeError(
                f"GitHub device authorization not complete: HTTP {status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(
                "GitHub device authorization not complete: HTTP request error"
            ) from exc
        data = _response_json_object(
            response,
            "GitHub device authorization not complete: malformed response",
        )
        if data.get("error"):
            error = data.get("error")
            raise RuntimeError(f"GitHub device authorization not complete: {error}")
        return _parse_user_access_token_response(
            data,
            "GitHub device authorization not complete: malformed response",
        )
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
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                _refresh_error_message(
                    exc.response,
                    refresh_token_present=bool(refresh_token),
                )
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("GitHub refresh failed: HTTP request error") from exc
        data = _response_json_object(response, "GitHub refresh failed: malformed response")
        if data.get("error"):
            error = data.get("error")
            raise RuntimeError(
                _refresh_error_message(
                    response,
                    refresh_token_present=bool(refresh_token),
                    oauth_error=str(error),
                )
            )
        return _parse_user_access_token_response(
            data,
            "GitHub refresh failed: malformed response",
        )
    finally:
        if owns_client:
            await http.aclose()


def _refresh_error_message(
    response: httpx.Response,
    *,
    refresh_token_present: bool,
    oauth_error: str | None = None,
) -> str:
    content_type = _safe_diagnostic_value(response.headers.get("content-type") or "missing")
    request_id = response.headers.get("x-github-request-id")
    diagnostics = [
        f"HTTP {response.status_code}",
        f"content_type={content_type}",
        (
            f"x_github_request_id={_safe_diagnostic_value(request_id)}"
            if request_id
            else "x_github_request_id_present=false"
        ),
        "client_secret_present=false",
        "repository_id_present=false",
        f"refresh_token_present={str(refresh_token_present).lower()}",
    ]
    if oauth_error:
        diagnostics.append(f"oauth_error={_safe_diagnostic_value(oauth_error)}")
    return "GitHub refresh failed: " + "; ".join(diagnostics)


def _safe_diagnostic_value(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-+/" else "_" for ch in value.strip())
    return cleaned[:120] or "missing"


async def query_viewer_login(
    access_token: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Return the GitHub viewer login for a user access token."""
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=15)
    try:
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
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            raise RuntimeError(f"GitHub GraphQL viewer query failed: HTTP {status_code}") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("GitHub GraphQL viewer query failed: HTTP request error") from exc
        data = _response_json_object(
            response,
            "GitHub GraphQL viewer query failed: malformed response",
        )
        if data.get("errors"):
            raise RuntimeError("GitHub GraphQL viewer query returned errors")
        return str(((data.get("data") or {}).get("viewer") or {}).get("login") or "")
    finally:
        if owns_client:
            await http.aclose()


class GitHubUserAccessClient:
    """Minimal GraphQL client for GitHub App user access tokens.

    The access token is intentionally kept in memory only. Public callers should
    redact viewer identity and thread IDs when presenting canary evidence.
    """

    def __init__(
        self,
        access_token: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._access_token = access_token
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=15)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def graphql(
        self,
        app_slug: str,
        repository: str,
        *,
        query: str,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        """Run a GitHub GraphQL request using a user access token."""
        del app_slug, repository
        try:
            response = await self._client.post(
                f"{GITHUB_API}/graphql",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self._access_token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={"query": query, "variables": variables},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            raise RuntimeError(
                f"GitHub GraphQL user-token request failed: HTTP {status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(
                "GitHub GraphQL user-token request failed: HTTP request error"
            ) from exc

        data = _response_json_object(
            response,
            "GitHub GraphQL user-token request failed: malformed response",
        )
        if data.get("errors"):
            raise RuntimeError(_graphql_error_message(data.get("errors")))
        result = data.get("data")
        if not isinstance(result, dict):
            raise RuntimeError("GitHub GraphQL user-token request failed: malformed response")
        return result

    async def resolve_review_thread(
        self,
        app_slug: str,
        repository: str,
        thread_id: str,
    ) -> dict[str, Any]:
        """Resolve a GitHub review thread using a user access token."""
        query = """
        mutation ResolveReviewThread($threadId: ID!) {
          resolveReviewThread(input: {threadId: $threadId}) {
            thread {
              id
              isResolved
              isOutdated
              resolvedBy {
                login
              }
            }
          }
        }
        """
        data = await self.graphql(
            app_slug,
            repository,
            query=query,
            variables={"threadId": thread_id},
        )
        return (((data or {}).get("resolveReviewThread") or {}).get("thread")) or {}


def _graphql_error_message(errors: Any) -> str:
    if not isinstance(errors, list) or not errors:
        return "GitHub GraphQL user-token request returned errors"
    first = errors[0] if isinstance(errors[0], dict) else {}
    first_type = _safe_diagnostic_value(str(first.get("type") or "unknown"))
    first_message = _safe_graphql_error_message(str(first.get("message") or "missing"))
    return (
        "GitHub GraphQL user-token request returned errors: "
        f"first_type={first_type}; first_message={first_message}"
    )


def _safe_graphql_error_message(value: str) -> str:
    redacted = re.sub(r"PRRT_[A-Za-z0-9._+/=-]+", "PRRT_redacted", value)
    redacted = re.sub(
        r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{20,}={0,2}(?![A-Za-z0-9+/=])",
        _redact_base64_node_id,
        redacted,
    )
    return _safe_diagnostic_value(redacted)


def _redact_base64_node_id(match: re.Match[str]) -> str:
    value = match.group(0)
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(value + padding, validate=True)
    except (binascii.Error, ValueError):
        return value
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError:
        return value
    if ":" not in text or any(ord(ch) < 32 or ord(ch) > 126 for ch in text):
        return value
    return "NODEID_redacted"


def _response_json_object(response: httpx.Response, error_message: str) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(error_message) from exc
    if not isinstance(data, dict):
        raise RuntimeError(error_message)
    return data


def _parse_user_access_token_response(
    data: dict[str, Any],
    error_message: str,
) -> UserAccessTokenResponse:
    try:
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
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(error_message) from exc
