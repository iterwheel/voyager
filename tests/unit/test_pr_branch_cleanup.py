"""Tests for auto-deletion of merged same-repo PR head branches."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from voyager.bots.cleanup import dispatch_pr_branch_cleanup, route_pr_merge_cleanup
from voyager.core.github_app import GitHubAppClient
from voyager.core.writeback import dispatch_route_writeback

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _merge_payload(
    *,
    repo_full: str = "iterwheel/voyager",
    same_repo: bool = True,
    merged: bool = True,
    head_ref: str = "feature/my-branch",
    head_sha: str = "abc123",
    pr_number: int = 42,
) -> dict[str, Any]:
    """Build a ``pull_request`` closed webhook payload."""
    head = {
        "ref": head_ref,
        "sha": head_sha,
        "repo": {"full_name": repo_full if same_repo else "forker/voyager"},
    }
    base = {
        "ref": "main",
        "repo": {"full_name": repo_full},
    }
    return {
        "action": "closed",
        "number": pr_number,
        "repository": {"full_name": repo_full},
        "pull_request": {
            "number": pr_number,
            "title": "My PR",
            "html_url": f"https://github.com/{repo_full}/pull/{pr_number}",
            "merged": merged,
            "base": base,
            "head": head,
        },
    }


def _mock_client(
    *,
    protected: bool = False,
    current_head_sha: str = "abc123",
    branch_head_error: BaseException | None = None,
    delete_error: bool = False,
    delete_status: int | None = None,
    branch_check_error: bool = False,
) -> MagicMock:
    client = MagicMock()
    if branch_check_error:
        client.branch_protected_or_raise = AsyncMock(side_effect=httpx.HTTPError("boom"))
    else:
        client.branch_protected_or_raise = AsyncMock(return_value=protected)
    client.branch_protected = AsyncMock(return_value=protected)
    if branch_head_error is not None:
        client.branch_head_sha_or_raise = AsyncMock(side_effect=branch_head_error)
    else:
        client.branch_head_sha_or_raise = AsyncMock(return_value=current_head_sha)

    if delete_error:
        err = httpx.HTTPStatusError(
            "boom",
            request=MagicMock(),
            response=MagicMock(status_code=delete_status or 422),
        )
        client.delete_branch = AsyncMock(side_effect=err)
    else:
        client.delete_branch = AsyncMock(return_value=None)

    return client


def _route(**overrides: Any) -> dict[str, Any]:
    return route_pr_merge_cleanup("pull_request", _merge_payload())[0]


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


class TestRoutePrMergeCleanup:
    def test_same_repo_merged_pr_yields_route(self) -> None:
        routes = route_pr_merge_cleanup("pull_request", _merge_payload())
        assert len(routes) == 1
        r = routes[0]
        assert r["agent"] == "iterwheel-cleanup"
        assert r["agent_id"] == "github-cleanup-agent"
        assert r["kind"] == "pr_branch_cleanup"
        assert r["event"] == "pull_request"
        assert r["action"] == "closed"
        assert r["validation"]["status"] == "cleanup_ready"
        assert r["validation"]["head_ref"] == "feature/my-branch"
        assert r["validation"]["head_sha"] == "abc123"
        assert r["validation"]["repository"] == "iterwheel/voyager"
        assert r["writeback"]["dynamic"] == "pr_branch_cleanup"
        assert r["writeback"]["head_ref"] == "feature/my-branch"
        assert r["writeback"]["head_sha"] == "abc123"
        assert r["writeback"]["repository"] == "iterwheel/voyager"
        assert r["writeback"]["pr_number"] == 42

    def test_fork_pr_yields_no_route(self) -> None:
        routes = route_pr_merge_cleanup("pull_request", _merge_payload(same_repo=False))
        assert routes == []

    def test_other_repository_yields_no_route(self) -> None:
        routes = route_pr_merge_cleanup(
            "pull_request",
            _merge_payload(repo_full="frankyxhl/trinity"),
        )
        assert routes == []

    def test_unmerged_pr_yields_no_route(self) -> None:
        routes = route_pr_merge_cleanup("pull_request", _merge_payload(merged=False))
        assert routes == []

    def test_wrong_event_yields_no_route(self) -> None:
        routes = route_pr_merge_cleanup("push", _merge_payload())
        assert routes == []

    def test_wrong_action_yields_no_route(self) -> None:
        payload = _merge_payload()
        payload["action"] = "opened"
        routes = route_pr_merge_cleanup("pull_request", payload)
        assert routes == []

    def test_missing_head_ref_yields_no_route(self) -> None:
        payload = _merge_payload()
        payload["pull_request"]["head"] = {"repo": {"full_name": "iterwheel/voyager"}}
        routes = route_pr_merge_cleanup("pull_request", payload)
        assert routes == []

    def test_missing_head_sha_yields_no_route(self) -> None:
        routes = route_pr_merge_cleanup("pull_request", _merge_payload(head_sha=""))
        assert routes == []

    def test_missing_repo_info_yields_no_route(self) -> None:
        payload = _merge_payload()
        payload["pull_request"]["head"]["repo"] = {}
        routes = route_pr_merge_cleanup("pull_request", payload)
        assert routes == []

    def test_zero_pr_number_yields_no_route(self) -> None:
        routes = route_pr_merge_cleanup("pull_request", _merge_payload(pr_number=0))
        assert routes == []


# ---------------------------------------------------------------------------
# Writeback
# ---------------------------------------------------------------------------


class TestDispatchPrBranchCleanup:
    def test_protected_branch_skipped(self, monkeypatch) -> None:
        monkeypatch.setenv("DRY_RUN", "false")
        client = _mock_client(protected=True)
        result = asyncio.run(
            dispatch_pr_branch_cleanup(client, _route(), repository="iterwheel/voyager")
        )
        assert result["applied"] is True
        assert result["skipped"] == "protected"
        client.delete_branch.assert_not_awaited()

    def test_successful_deletion(self, monkeypatch) -> None:
        monkeypatch.setenv("DRY_RUN", "false")
        client = _mock_client(protected=False)
        result = asyncio.run(
            dispatch_pr_branch_cleanup(client, _route(), repository="iterwheel/voyager")
        )
        assert result["applied"] is True
        assert result["deleted"] is True
        client.branch_head_sha_or_raise.assert_awaited_once_with(
            "iterwheel-assembly", "iterwheel/voyager", "feature/my-branch"
        )
        client.delete_branch.assert_awaited_once_with(
            "iterwheel-assembly", "iterwheel/voyager", "feature/my-branch"
        )

    def test_branch_already_gone_is_idempotent(self, monkeypatch) -> None:
        monkeypatch.setenv("DRY_RUN", "false")
        client = _mock_client(protected=False, delete_error=True, delete_status=404)
        result = asyncio.run(
            dispatch_pr_branch_cleanup(client, _route(), repository="iterwheel/voyager")
        )
        assert result["applied"] is True
        assert result["deleted"] is False
        assert result["reason"] == "already_gone"

    def test_delete_http_error_returns_applied_false(self, monkeypatch) -> None:
        monkeypatch.setenv("DRY_RUN", "false")
        client = _mock_client(protected=False, delete_error=True, delete_status=422)
        result = asyncio.run(
            dispatch_pr_branch_cleanup(client, _route(), repository="iterwheel/voyager")
        )
        assert result["applied"] is False

    def test_transport_error_on_delete_returns_applied_false(self, monkeypatch) -> None:
        monkeypatch.setenv("DRY_RUN", "false")
        client = _mock_client(protected=False, delete_error=True, delete_status=None)
        result = asyncio.run(
            dispatch_pr_branch_cleanup(client, _route(), repository="iterwheel/voyager")
        )
        assert result["applied"] is False

    def test_missing_head_ref_returns_applied_false(self) -> None:
        route = _route()
        route["writeback"]["head_ref"] = ""
        result = asyncio.run(
            dispatch_pr_branch_cleanup(MagicMock(), route, repository="iterwheel/voyager")
        )
        assert result["applied"] is False

    def test_missing_head_sha_returns_applied_false(self, monkeypatch) -> None:
        monkeypatch.setenv("DRY_RUN", "false")
        route = _route()
        route["writeback"]["head_sha"] = ""
        client = _mock_client(protected=False)

        result = asyncio.run(
            dispatch_pr_branch_cleanup(client, route, repository="iterwheel/voyager")
        )

        assert result["applied"] is False
        assert result["reason"] == "missing head_sha in writeback payload"
        client.branch_protected_or_raise.assert_not_awaited()
        client.branch_head_sha_or_raise.assert_not_awaited()
        client.delete_branch.assert_not_awaited()

    def test_branch_protected_check_failure_returns_applied_false(self, monkeypatch) -> None:
        monkeypatch.setenv("DRY_RUN", "false")
        client = _mock_client(branch_check_error=True)
        result = asyncio.run(
            dispatch_pr_branch_cleanup(client, _route(), repository="iterwheel/voyager")
        )
        assert result["applied"] is False
        client.delete_branch.assert_not_awaited()

    def test_branch_protected_404_is_idempotent_already_gone(self, monkeypatch) -> None:
        monkeypatch.setenv("DRY_RUN", "false")
        request = httpx.Request("GET", "https://api.github.com/repos/iterwheel/voyager/branches/x")
        response = httpx.Response(404, request=request)
        err = httpx.HTTPStatusError("not found", request=request, response=response)
        client = MagicMock()
        client.branch_protected_or_raise = AsyncMock(side_effect=err)
        client.branch_protected = AsyncMock(return_value=True)
        client.delete_branch = AsyncMock(return_value=None)

        result = asyncio.run(
            dispatch_pr_branch_cleanup(client, _route(), repository="iterwheel/voyager")
        )

        assert result["applied"] is True
        assert result["deleted"] is False
        assert result["reason"] == "already_gone"
        client.delete_branch.assert_not_awaited()

    def test_branch_head_sha_404_is_idempotent_already_gone(self, monkeypatch) -> None:
        monkeypatch.setenv("DRY_RUN", "false")
        request = httpx.Request(
            "GET", "https://api.github.com/repos/iterwheel/voyager/git/ref/heads/x"
        )
        response = httpx.Response(404, request=request)
        err = httpx.HTTPStatusError("not found", request=request, response=response)
        client = _mock_client(protected=False, branch_head_error=err)

        result = asyncio.run(
            dispatch_pr_branch_cleanup(client, _route(), repository="iterwheel/voyager")
        )

        assert result["applied"] is True
        assert result["deleted"] is False
        assert result["reason"] == "already_gone"
        client.delete_branch.assert_not_awaited()

    def test_branch_head_sha_mismatch_skips_delete(self, monkeypatch) -> None:
        monkeypatch.setenv("DRY_RUN", "false")
        client = _mock_client(protected=False, current_head_sha="def456")

        result = asyncio.run(
            dispatch_pr_branch_cleanup(client, _route(), repository="iterwheel/voyager")
        )

        assert result["applied"] is True
        assert result["skipped"] == "head_sha_changed"
        assert result["expected_head_sha"] == "abc123"
        assert result["current_head_sha"] == "def456"
        client.delete_branch.assert_not_awaited()

    def test_branch_head_sha_transport_error_returns_applied_false(self, monkeypatch) -> None:
        monkeypatch.setenv("DRY_RUN", "false")
        client = _mock_client(protected=False, branch_head_error=httpx.HTTPError("boom"))

        result = asyncio.run(
            dispatch_pr_branch_cleanup(client, _route(), repository="iterwheel/voyager")
        )

        assert result["applied"] is False
        assert result["reason"] == "branch head SHA transport error: HTTPError"
        client.delete_branch.assert_not_awaited()

    def test_real_client_fail_safe_branch_check_is_not_used_for_cleanup(self, monkeypatch) -> None:
        monkeypatch.setenv("DRY_RUN", "false")
        request = httpx.Request("GET", "https://api.github.com/repos/iterwheel/voyager/branches/x")
        response = httpx.Response(500, request=request)
        err = httpx.HTTPStatusError("boom", request=request, response=response)
        client = MagicMock()
        client.branch_protected_or_raise = AsyncMock(side_effect=err)
        client.branch_protected = AsyncMock(return_value=True)
        client.delete_branch = AsyncMock(return_value=None)

        result = asyncio.run(
            dispatch_pr_branch_cleanup(client, _route(), repository="iterwheel/voyager")
        )

        assert result["applied"] is False
        assert result["reason"] == "branch_protected check failed: HTTPStatusError"
        client.branch_protected_or_raise.assert_awaited_once_with(
            "iterwheel-assembly", "iterwheel/voyager", "feature/my-branch"
        )
        client.branch_protected.assert_not_awaited()
        client.delete_branch.assert_not_awaited()

    def test_legacy_client_without_strict_branch_check_still_uses_branch_protected(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv("DRY_RUN", "false")
        client = MagicMock()
        client.branch_protected = AsyncMock(return_value=False)
        client.branch_head_sha_or_raise = AsyncMock(return_value="abc123")
        client.delete_branch = AsyncMock(return_value=None)

        result = asyncio.run(
            dispatch_pr_branch_cleanup(client, _route(), repository="iterwheel/voyager")
        )

        assert result["applied"] is True
        assert result["deleted"] is True
        assert "branch_protected_or_raise" not in client.__dict__
        client.branch_protected.assert_awaited_once_with(
            "iterwheel-assembly", "iterwheel/voyager", "feature/my-branch"
        )
        client.delete_branch.assert_awaited_once()

    def test_dry_run_skips_api_calls(self, monkeypatch) -> None:
        monkeypatch.setenv("DRY_RUN", "true")
        client = MagicMock()
        result = asyncio.run(
            dispatch_pr_branch_cleanup(client, _route(), repository="iterwheel/voyager")
        )
        assert result["applied"] is True
        assert result["dry_run"] is True
        client.branch_protected.assert_not_called()
        client.delete_branch.assert_not_called()

    def test_integration_via_dispatch_route_writeback(self, monkeypatch) -> None:
        """Verify the dispatch dispatcher routes pr_branch_cleanup correctly."""
        monkeypatch.setenv("DRY_RUN", "false")
        route = _route()
        client = _mock_client(protected=False)
        result = asyncio.run(
            dispatch_route_writeback(client, route, repository="iterwheel/voyager")
        )
        assert result["applied"] is True
        assert result["deleted"] is True
        client.delete_branch.assert_awaited_once()

    def test_integration_dry_run_via_dispatch(self, monkeypatch) -> None:
        """Verify dry_run is respected via the dispatch path."""
        monkeypatch.setenv("DRY_RUN", "true")
        route = _route()
        client = MagicMock()
        result = asyncio.run(
            dispatch_route_writeback(client, route, repository="iterwheel/voyager")
        )
        assert result["applied"] is True
        assert result["dry_run"] is True
        client.branch_protected.assert_not_called()
        client.delete_branch.assert_not_called()


@pytest.mark.asyncio
async def test_branch_protected_or_raise_propagates_lookup_http_errors(monkeypatch) -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(500))
    async_client = httpx.AsyncClient(transport=transport)
    client = GitHubAppClient({})

    async def fake_installation_token(_app_slug: str, *, repository: str | None = None) -> str:
        return "ghs_INSTALLATION_TOKEN"

    monkeypatch.setattr(client, "installation_token", fake_installation_token)
    monkeypatch.setattr(client, "_async_client", lambda: async_client)

    with pytest.raises(httpx.HTTPStatusError):
        await client.branch_protected_or_raise(
            "iterwheel-assembly", "iterwheel/voyager", "feature/my-branch"
        )

    await async_client.aclose()


@pytest.mark.asyncio
async def test_branch_protected_keeps_fail_safe_true_on_lookup_http_errors(monkeypatch) -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(500))
    async_client = httpx.AsyncClient(transport=transport)
    client = GitHubAppClient({})

    async def fake_installation_token(_app_slug: str, *, repository: str | None = None) -> str:
        return "ghs_INSTALLATION_TOKEN"

    monkeypatch.setattr(client, "installation_token", fake_installation_token)
    monkeypatch.setattr(client, "_async_client", lambda: async_client)

    protected = await client.branch_protected(
        "iterwheel-clearance", "iterwheel/voyager", "feature/my-branch"
    )

    assert protected is True
    await async_client.aclose()
