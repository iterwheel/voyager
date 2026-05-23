"""Issue #62: Unit tests for fork PR head-repo accessibility.

Covers ``GitHubAppClient.check_head_repo_accessible`` caching behaviour and the
``UnsupportedContext`` writeback failure formatting used by Stage 1.5 when a
fork PR's head repository is not accessible to the Clearance app.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from voyager.core.writeback import format_writeback_failure_warning

# ---------------------------------------------------------------------------
# format_writeback_failure_warning — UnsupportedContext
# ---------------------------------------------------------------------------


def test_format_warning_unsupported_context_fork():
    """UnsupportedContext failures surface the fork-repo install message."""
    failure = {
        "operation": "resolveReviewThread",
        "error_class": "UnsupportedContext",
        "status": None,
        "repo": "iterwheel/voyager",
        "pr": 51,
        "issue": None,
        "thread_id": "PRRT_kwDOSX_Dgs6CqiOL",
        "suggested_action": (
            "Unsupported context: PR #51 is from fork ryosaeba1985/voyager. "
            "Install iterwheel-clearance on ryosaeba1985/voyager to enable "
            "auto-resolve, or resolve thread PRRT_kwDOSX_Dgs6CqiOL manually."
        ),
    }
    line = format_writeback_failure_warning(failure)
    assert "⚠️ Automation writeback: resolveReviewThread failed (UnsupportedContext)" in line
    assert "iterwheel/voyager#51 thread PRRT_kwDOSX_Dgs6CqiOL" in line
    assert "fork" in line.lower()
    assert "install" in line.lower()


def test_format_warning_unsupported_context_no_thread_id():
    """UnsupportedContext without thread_id still renders the install message."""
    failure = {
        "operation": "resolveReviewThread",
        "error_class": "UnsupportedContext",
        "status": None,
        "repo": "iterwheel/voyager",
        "pr": 60,
        "issue": None,
        "thread_id": None,
        "suggested_action": (
            "Unsupported context: PR #60 is from fork ryosaeba1985/voyager. "
            "Install iterwheel-clearance on ryosaeba1985/voyager."
        ),
    }
    line = format_writeback_failure_warning(failure)
    assert "fork" in line.lower()
    assert "install" in line.lower()


# ---------------------------------------------------------------------------
# GitHubAppClient.check_head_repo_accessible — caching
# ---------------------------------------------------------------------------


class _FakeApp:
    """Minimal app config double for discover_installation_id tests."""

    slug = "iterwheel-clearance"


async def test_check_head_repo_accessible_does_not_cache_negative():
    """Negative results are NOT cached — each call re-discovers so an operator
    installing the app on the fork after a previous check takes effect without
    a process restart."""
    from voyager.core.github_app import GitHubAppClient

    client = GitHubAppClient({"iterwheel-clearance": _FakeApp()})
    head_repo = "ryosaeba1985/voyager"

    with patch.object(client, "_discover_installation_id", new_callable=AsyncMock) as mock_discover:
        mock_discover.return_value = None  # not installed

        # First call — should call _discover_installation_id
        result1 = await client.check_head_repo_accessible("iterwheel-clearance", head_repo)
        assert result1 is False
        assert mock_discover.call_count == 1

        # Second call — negative results are not cached, calls again
        result2 = await client.check_head_repo_accessible("iterwheel-clearance", head_repo)
        assert result2 is False
        assert mock_discover.call_count == 2


async def test_check_head_repo_accessible_positive_returns_true():
    """When _discover_installation_id returns an ID, the method returns True."""
    from voyager.core.github_app import GitHubAppClient

    client = GitHubAppClient({"iterwheel-clearance": _FakeApp()})
    head_repo = "iterwheel/voyager"

    with patch.object(client, "_discover_installation_id", new_callable=AsyncMock) as mock_discover:
        mock_discover.return_value = "12345"  # installed

        result = await client.check_head_repo_accessible("iterwheel-clearance", head_repo)
        assert result is True
        assert mock_discover.call_count == 1


async def test_check_head_repo_accessible_positive_not_cached_negative():
    """Positive results are NOT added to the negative cache — a subsequent call
    with a genuinely inaccessible head repo still calls _discover_installation_id."""
    from voyager.core.github_app import GitHubAppClient

    client = GitHubAppClient({"iterwheel-clearance": _FakeApp()})

    with patch.object(client, "_discover_installation_id", new_callable=AsyncMock) as mock_discover:
        # First: accessible repo
        mock_discover.return_value = "12345"
        result1 = await client.check_head_repo_accessible(
            "iterwheel-clearance", "iterwheel/voyager"
        )
        assert result1 is True
        assert mock_discover.call_count == 1

        # Second: inaccessible repo — should still call _discover_installation_id
        mock_discover.return_value = None
        result2 = await client.check_head_repo_accessible(
            "iterwheel-clearance", "ryosaeba1985/voyager"
        )
        assert result2 is False
        assert mock_discover.call_count == 2  # called again for different repo


async def test_check_head_repo_accessible_different_apps_independent():
    """Each app slug re-discovers independently — no shared negative cache."""
    from voyager.core.github_app import GitHubAppClient

    client = GitHubAppClient(
        {
            "iterwheel-clearance": _FakeApp(),
            "iterwheel-blueprint": _FakeApp(),
        }
    )
    head_repo = "ryosaeba1985/voyager"

    with patch.object(client, "_discover_installation_id", new_callable=AsyncMock) as mock_discover:
        mock_discover.return_value = None

        # Clearance app check
        await client.check_head_repo_accessible("iterwheel-clearance", head_repo)
        assert mock_discover.call_count == 1

        # Blueprint app check — different app, so should call again
        await client.check_head_repo_accessible("iterwheel-blueprint", head_repo)
        assert mock_discover.call_count == 2
