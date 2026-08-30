"""Issue #252: installation_token must attempt discovery before the app-level default.

A multi-org GitHub App configured with a default installation_id must not
silently mint tokens from the default installation for an unmapped repository:
every subsequent API call 404s ("Resource not accessible") with no hint.
Resolution order for an explicit repository: configured per-repo/owner map →
discovery → app-level default (with a loud warning).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from voyager.core.config import AppConfig
from voyager.core.github_app import GitHubAppClient

_EXPIRES_SOON = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")


def _app(*, installation_id: str = "111", installations: dict[str, str] | None = None) -> AppConfig:
    return AppConfig(
        slug="iterwheel-clearance",
        app_id="123456",
        private_key_path=Path("/nonexistent-key.pem"),
        installation_id=installation_id,
        installations=installations or {},
    )


def _client(app: AppConfig, transport: httpx.MockTransport) -> GitHubAppClient:
    client = GitHubAppClient({"iterwheel-clearance": app})
    async_client = httpx.AsyncClient(transport=transport)
    client._client = async_client
    # Avoid touching the real private key / JWT machinery.
    client._app_jwt = lambda _app: "fake-jwt"  # type: ignore[method-assign]
    return client


def _transport(
    discovery_status: int, discovery_id: str, requests: list[str]
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/installation"):
            if discovery_status == 404:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"id": int(discovery_id)})
        if "/access_tokens" in request.url.path:
            return httpx.Response(200, json={"token": "ghs_token", "expires_at": _EXPIRES_SOON})
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    return httpx.MockTransport(handler)


async def test_unmapped_repo_discovers_before_default():
    """Repo absent from the installations map + default configured → discovery
    is invoked first and the discovered installation wins."""
    requests: list[str] = []
    client = _client(
        _app(installation_id="111"),
        _transport(discovery_status=200, discovery_id="9999", requests=requests),
    )

    token = await client.installation_token("iterwheel-clearance", repository="other-org/repo")

    assert token == "ghs_token"
    # Discovery ran before any token mint…
    assert requests[0] == "GET /repos/other-org/repo/installation"
    # …and the mint used the DISCOVERED installation, not the default 111.
    assert requests[1] == "POST /app/installations/9999/access_tokens"


async def test_unmapped_repo_falls_back_to_default_with_warning(caplog):
    """Discovery 404 + default configured → default is used, loudly."""
    requests: list[str] = []
    client = _client(
        _app(installation_id="111"),
        _transport(discovery_status=404, discovery_id="", requests=requests),
    )

    with caplog.at_level(logging.WARNING, logger="voyager.core.github_app"):
        token = await client.installation_token("iterwheel-clearance", repository="other-org/repo")

    assert token == "ghs_token"
    assert requests[1] == "POST /app/installations/111/access_tokens"
    assert any("falling back to the app-level default" in r.message for r in caplog.records)


async def test_mapped_repo_skips_discovery():
    """A repo present in the installations map never hits discovery or the default."""
    requests: list[str] = []
    client = _client(
        _app(installation_id="111", installations={"mapped-org/repo": "222"}),
        _transport(discovery_status=200, discovery_id="9999", requests=requests),
    )

    token = await client.installation_token("iterwheel-clearance", repository="mapped-org/repo")

    assert token == "ghs_token"
    assert requests == ["POST /app/installations/222/access_tokens"]


async def test_no_default_and_undiscoverable_still_raises():
    """No default configured + discovery 404 → the existing RuntimeError contract."""
    requests: list[str] = []
    client = _client(
        _app(installation_id=""),
        _transport(discovery_status=404, discovery_id="", requests=requests),
    )

    with pytest.raises(RuntimeError, match="not configured or discoverable"):
        await client.installation_token("iterwheel-clearance", repository="other-org/repo")
