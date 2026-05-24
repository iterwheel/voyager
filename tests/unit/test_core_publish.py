"""Unit tests for voyager/core/publish.py — Assembly App token publish path.

Covers token handling, askpass cleanup, force-with-lease behavior,
PR create/update, codex trigger, and error paths — all without
requiring a real token or GitHub API call.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from voyager.core.publish import (
    CODEX_REVIEW_TRIGGER_BODY,
    _git_auth_env,
    _sanitize,
    _write_git_askpass,
    assembly_app_publish,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_subprocess(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    """Return a mock asyncio subprocess with a controllable exit code."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
    proc.kill = MagicMock()
    return proc


def _mock_client(**attrs: object) -> MagicMock:
    """Return a mock ``GitHubAppClient`` with async methods defaulting to OK."""
    client = MagicMock()
    client.installation_token = AsyncMock(return_value="ghs_test_token_abc123")
    client.find_pull_request_by_head = AsyncMock(return_value=None)
    client.create_pull_request = AsyncMock(
        return_value={"number": 42, "html_url": "https://github.test/pull/42"}
    )
    client.update_pull_request = AsyncMock(return_value={"html_url": "https://github.test/pull/42"})
    client.create_issue_comment = AsyncMock(return_value={"id": 789})

    for key, val in attrs.items():
        setattr(client, key, val if isinstance(val, AsyncMock) else AsyncMock(return_value=val))
    return client


def _temporary_remote_name(mock_exec: MagicMock) -> str:
    for call_args, _ in mock_exec.call_args_list:
        if len(call_args) > 4 and call_args[1] == "remote" and call_args[2] == "add":
            remote_name = str(call_args[3])
            assert remote_name.startswith("assembly-publish-")
            return remote_name
    raise AssertionError("No git remote add call found")


# ---------------------------------------------------------------------------
# token / askpass helpers
# ---------------------------------------------------------------------------


class TestSanitize:
    def test_redacts_token(self) -> None:
        assert _sanitize("hello ghs_token_abc world", "ghs_token_abc") == "hello [redacted] world"

    def test_redacts_any_github_token_pattern(self) -> None:
        assert _sanitize("token=ghp_abc123", "") == "token=[redacted]"

    def test_no_token_noop(self) -> None:
        assert _sanitize("hello world", "") == "hello world"

    def test_token_not_in_string_noop(self) -> None:
        assert _sanitize("hello world", "ghs_secret") == "hello world"


class TestGitAskpass:
    def test_writes_and_chmod(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            askpass = _write_git_askpass(Path(tmp))
            assert askpass.exists()
            assert askpass.name == "git-askpass.sh"
            text = askpass.read_text(encoding="utf-8")
            assert "x-access-token" in text
            assert "ASSEMBLY_GITHUB_TOKEN" in text
            # Verify 0o700 — owner execute bit set
            assert askpass.stat().st_mode & 0o700 == 0o700

    def test_git_auth_env_sets_expected_vars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            askpass = _write_git_askpass(Path(tmp))
            env = _git_auth_env("ghs_secret", askpass)
            assert env.get("GIT_TERMINAL_PROMPT") == "0"
            assert env.get("GIT_ASKPASS") == str(askpass)
            assert env.get("ASSEMBLY_GITHUB_TOKEN") == "ghs_secret"

    def test_git_auth_env_preserves_original_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            askpass = _write_git_askpass(Path(tmp))
            env = _git_auth_env("tok", askpass)
            # Original env vars should still be present
            assert "PATH" in env


# ---------------------------------------------------------------------------
# assembly_app_publish — git push success paths
# ---------------------------------------------------------------------------


class TestPublishPushNewBranch:
    """First push to a new branch — PR creation."""

    @patch("voyager.core.publish.asyncio.create_subprocess_exec")
    async def test_new_branch_creates_pr(self, mock_exec: MagicMock) -> None:
        mock_exec.return_value = _mock_subprocess(returncode=0)
        client = _mock_client()

        result = await assembly_app_publish(
            repository="iterwheel/voyager",
            branch="102-my-feature",
            base="main",
            pr_title="My feature (Closes #102)",
            pr_body="Implements #102.\n\nCloses #102.",
            client=client,
            cwd="/tmp",
        )

        assert result.pushed is True
        assert result.pr_number == 42
        assert result.pr_action == "opened"
        assert result.pr_url == "https://github.test/pull/42"
        assert result.codex_comment_id == 789
        assert result.error is None

        # Verify the correct git push command was constructed
        push_call_args = None
        for call_args, _ in mock_exec.call_args_list:
            if len(call_args) > 1 and call_args[1] == "push":
                push_call_args = call_args
                break
        assert push_call_args is not None, "No git push call found"
        assert "--force-with-lease" in push_call_args
        assert "--no-verify" in push_call_args
        assert push_call_args[-1] == "HEAD:refs/heads/102-my-feature"
        remote_name = _temporary_remote_name(mock_exec)
        assert remote_name in push_call_args

        # Verify the git fetch call is present before push
        fetch_call_args = None
        for call_args, _ in mock_exec.call_args_list:
            if len(call_args) > 1 and call_args[1] == "fetch":
                fetch_call_args = call_args
                break
        assert fetch_call_args is not None, "No git fetch call found"
        assert "--no-tags" in fetch_call_args
        assert remote_name in fetch_call_args
        assert "refs/heads/102-my-feature" in " ".join(fetch_call_args)
        assert f"refs/remotes/{remote_name}/102-my-feature" in " ".join(fetch_call_args)
        fetch_idx = next(
            i
            for i, (call_args, _) in enumerate(mock_exec.call_args_list)
            if len(call_args) > 1 and call_args[1] == "fetch"
        )
        push_idx = next(
            i
            for i, (call_args, _) in enumerate(mock_exec.call_args_list)
            if len(call_args) > 1 and call_args[1] == "push"
        )
        assert fetch_idx < push_idx

        # Verify PR was created with correct data
        client.create_pull_request.assert_awaited_once()
        _, kwargs = client.create_pull_request.await_args
        assert kwargs["title"] == "My feature (Closes #102)"
        assert kwargs["head"] == "102-my-feature"
        assert kwargs["base"] == "main"

        # Verify @codex review was posted
        client.create_issue_comment.assert_awaited_once()
        _, kwargs = client.create_issue_comment.await_args
        assert kwargs["body"] == CODEX_REVIEW_TRIGGER_BODY


class TestPublishUpdateExistingPR:
    """Push updating an existing PR (explicit pr_number)."""

    @patch("voyager.core.publish.asyncio.create_subprocess_exec")
    async def test_explicit_pr_number_updates(self, mock_exec: MagicMock) -> None:
        mock_exec.return_value = _mock_subprocess(returncode=0)
        client = _mock_client()

        result = await assembly_app_publish(
            repository="iterwheel/voyager",
            branch="102-my-feature",
            base="main",
            pr_title="My feature (Closes #102)",
            pr_number=42,
            client=client,
            cwd="/tmp",
        )

        assert result.pushed is True
        assert result.pr_number == 42
        assert result.pr_action == "updated"
        assert result.error is None

        # PR was updated, not created
        client.update_pull_request.assert_awaited_once_with(
            "iterwheel-assembly",
            "iterwheel/voyager",
            42,
            body="",
            title="My feature (Closes #102)",
        )
        client.create_pull_request.assert_not_awaited()

        # @codex review still posted
        client.create_issue_comment.assert_awaited_once()


class TestPublishFindExistingPR:
    """Push where an existing PR for the branch is auto-detected."""

    @patch("voyager.core.publish.asyncio.create_subprocess_exec")
    async def test_existing_pr_updated(self, mock_exec: MagicMock) -> None:
        mock_exec.return_value = _mock_subprocess(returncode=0)
        client = _mock_client()
        client.find_pull_request_by_head = AsyncMock(
            return_value={"number": 99, "html_url": "https://github.test/pull/99"}
        )

        result = await assembly_app_publish(
            repository="iterwheel/voyager",
            branch="102-my-feature",
            base="main",
            pr_title="My feature (Closes #102)",
            client=client,
            cwd="/tmp",
        )

        assert result.pushed is True
        assert result.pr_number == 99
        assert result.pr_action == "updated"
        assert result.error is None

        client.find_pull_request_by_head.assert_awaited_once()
        client.update_pull_request.assert_awaited_once()
        client.create_pull_request.assert_not_awaited()


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestPublishTokenFailure:
    async def test_token_mint_failure_returns_error(self) -> None:
        client = _mock_client()
        client.installation_token = AsyncMock(side_effect=RuntimeError("no key"))

        result = await assembly_app_publish(
            repository="iterwheel/voyager",
            branch="102-x",
            base="main",
            pr_title="x",
            client=client,
            cwd="/tmp",
        )

        assert result.pushed is False
        assert result.pr_number is None
        assert result.error is not None
        assert "installation_token" in (result.error or "")

    async def test_empty_token_returns_error(self) -> None:
        client = _mock_client()
        client.installation_token = AsyncMock(return_value="")

        result = await assembly_app_publish(
            repository="iterwheel/voyager",
            branch="102-x",
            base="main",
            pr_title="x",
            client=client,
            cwd="/tmp",
        )

        assert result.pushed is False
        assert result.error is not None
        assert "empty" in (result.error or "").lower()


class TestPublishPushFailure:
    @patch("voyager.core.publish.asyncio.create_subprocess_exec")
    async def test_remote_add_failure_does_not_remove_existing_remote(
        self, mock_exec: MagicMock
    ) -> None:
        def _side_effect(*args: object, **kwargs: object) -> MagicMock:
            if len(args) > 3 and args[1] == "remote" and args[2] == "add":
                return _mock_subprocess(returncode=3, stderr="remote already exists")
            return _mock_subprocess(returncode=0)

        mock_exec.side_effect = _side_effect
        client = _mock_client()

        result = await assembly_app_publish(
            repository="iterwheel/voyager",
            branch="102-x",
            base="main",
            pr_title="x",
            client=client,
            cwd="/tmp",
        )

        assert result.pushed is False
        assert result.error is not None
        assert "Failed to add temporary remote" in result.error
        assert not any(
            len(call_args) > 3 and call_args[1] == "remote" and call_args[2] == "remove"
            for call_args, _ in mock_exec.call_args_list
        )

    @patch("voyager.core.publish.asyncio.create_subprocess_exec")
    async def test_push_failure_returns_error(self, mock_exec: MagicMock) -> None:
        # git remote add must succeed; git push must fail with 128
        def _side_effect(*args: object, **kwargs: object) -> MagicMock:
            if len(args) > 2 and str(args[1]) == "push":
                return _mock_subprocess(returncode=128)
            return _mock_subprocess(returncode=0)

        mock_exec.side_effect = _side_effect
        client = _mock_client()

        result = await assembly_app_publish(
            repository="iterwheel/voyager",
            branch="102-x",
            base="main",
            pr_title="x",
            client=client,
            cwd="/tmp",
        )

        assert result.pushed is False
        assert result.error is not None
        assert "exit 128" in (result.error or "")

        # No GitHub API calls beyond token
        client.create_pull_request.assert_not_awaited()
        client.update_pull_request.assert_not_awaited()
        client.create_issue_comment.assert_not_awaited()


class TestPublishPushRemoteURL:
    """VOY-1822: push argv must use explicit HTTPS remote, not ``origin``."""

    @patch("voyager.core.publish.asyncio.create_subprocess_exec")
    async def test_push_uses_https_remote_not_origin(self, mock_exec: MagicMock) -> None:
        mock_exec.return_value = _mock_subprocess(returncode=0)
        client = _mock_client()

        result = await assembly_app_publish(
            repository="iterwheel/voyager",
            branch="102-my-feature",
            base="main",
            pr_title="My feature (Closes #102)",
            client=client,
            cwd="/tmp",
        )

        assert result.pushed is True
        assert result.error is None

        # The push argv must use the named remote, not the URL directly
        first_push_call = None
        for call_args, _ in mock_exec.call_args_list:
            if len(call_args) > 2 and call_args[1] == "push":
                first_push_call = call_args
                break
        assert first_push_call is not None, "No push call found"
        push_args = first_push_call
        remote_name = _temporary_remote_name(mock_exec)
        assert remote_name in push_args, f"push argv must use the named remote, got: {push_args}"
        assert "https://github.com/iterwheel/voyager.git" not in push_args
        # Verify no literal "origin" appears as a push remote
        assert "origin" not in push_args
        # Verify the git remote add call has the URL
        remote_add_found = False
        for call_args, _ in mock_exec.call_args_list:
            if len(call_args) > 3 and call_args[1] == "remote" and call_args[2] == "add":
                remote_add_found = True
                assert call_args[3] == remote_name
                assert "https://github.com/iterwheel/voyager.git" in call_args
                break
        assert remote_add_found, "No git remote add call found"

        # Verify the git fetch call is present before push
        fetch_found = False
        for call_args, _ in mock_exec.call_args_list:
            if len(call_args) > 1 and call_args[1] == "fetch":
                fetch_found = True
                assert "--no-tags" in call_args
                assert remote_name in call_args
                assert f"refs/heads/102-my-feature:refs/remotes/{remote_name}/102-my-feature" in (
                    call_args
                )
                break
        assert fetch_found, "No git fetch call found"

    @patch("voyager.core.publish.asyncio.create_subprocess_exec")
    async def test_push_uses_different_repo_remote(self, mock_exec: MagicMock) -> None:
        """The remote URL is derived from the *repository* parameter."""
        mock_exec.return_value = _mock_subprocess(returncode=0)
        client = _mock_client()

        result = await assembly_app_publish(
            repository="other-org/other-repo",
            branch="fix",
            base="main",
            pr_title="Fix",
            client=client,
            cwd="/tmp",
        )

        assert result.pushed is True
        # Push uses the named remote; the URL appears in git remote add
        first_push_call = None
        for call_args, _ in mock_exec.call_args_list:
            if len(call_args) > 2 and call_args[1] == "push":
                first_push_call = call_args
                break
        assert first_push_call is not None, "No push call found"
        push_args = first_push_call
        remote_name = _temporary_remote_name(mock_exec)
        assert remote_name in push_args
        # The URL should be in the git remote add call, not the push
        url_found = False
        for call_args, _ in mock_exec.call_args_list:
            if "https://github.com/other-org/other-repo.git" in call_args:
                url_found = True
                break
        assert url_found, "The HTTPS remote URL must appear in one of the git command args"
        assert "iterwheel" not in push_args

        # Verify the git fetch call is present for the different repo
        fetch_found = False
        for call_args, _ in mock_exec.call_args_list:
            if len(call_args) > 1 and call_args[1] == "fetch":
                fetch_found = True
                assert "--no-tags" in call_args
                assert remote_name in call_args
                assert f"refs/heads/fix:refs/remotes/{remote_name}/fix" in call_args
                break
        assert fetch_found, "No git fetch call found"


class TestPublishFetchPreparation:
    @patch("voyager.core.publish.asyncio.create_subprocess_exec")
    async def test_missing_remote_ref_is_tolerated_for_new_branch(
        self, mock_exec: MagicMock
    ) -> None:
        def _side_effect(*args: object, **kwargs: object) -> MagicMock:
            if len(args) > 1 and str(args[1]) == "fetch":
                return _mock_subprocess(
                    returncode=128,
                    stderr="fatal: could not find remote ref 102-new-branch",
                )
            return _mock_subprocess(returncode=0)

        mock_exec.side_effect = _side_effect
        client = _mock_client()

        result = await assembly_app_publish(
            repository="iterwheel/voyager",
            branch="102-new-branch",
            base="main",
            pr_title="x",
            client=client,
            cwd="/tmp",
        )

        assert result.pushed is True
        assert result.error is None
        assert any(
            len(call_args) > 1 and call_args[1] == "push"
            for call_args, _ in mock_exec.call_args_list
        )
        client.create_pull_request.assert_awaited_once()
        client.create_issue_comment.assert_awaited_once()

    @patch("voyager.core.publish.asyncio.create_subprocess_exec")
    async def test_unexpected_fetch_failure_stops_before_push(self, mock_exec: MagicMock) -> None:
        def _side_effect(*args: object, **kwargs: object) -> MagicMock:
            if len(args) > 1 and str(args[1]) == "fetch":
                return _mock_subprocess(
                    returncode=128,
                    stderr="fatal: repository not found",
                )
            return _mock_subprocess(returncode=0)

        mock_exec.side_effect = _side_effect
        client = _mock_client()

        result = await assembly_app_publish(
            repository="iterwheel/voyager",
            branch="102-x",
            base="main",
            pr_title="x",
            client=client,
            cwd="/tmp",
        )

        assert result.pushed is False
        assert result.error is not None
        assert "git fetch failed" in result.error
        assert "repository not found" in result.error
        assert not any(
            len(call_args) > 1 and call_args[1] == "push"
            for call_args, _ in mock_exec.call_args_list
        )
        client.create_pull_request.assert_not_awaited()
        client.update_pull_request.assert_not_awaited()
        client.create_issue_comment.assert_not_awaited()

    @patch("voyager.core.publish.asyncio.create_subprocess_exec")
    async def test_fetch_timeout_stops_before_push(self, mock_exec: MagicMock) -> None:
        timeout_proc = MagicMock()
        timeout_proc.communicate = AsyncMock(side_effect=TimeoutError())
        timeout_proc.kill = MagicMock()

        def _side_effect(*args: object, **kwargs: object) -> MagicMock:
            if len(args) > 1 and str(args[1]) == "fetch":
                return timeout_proc
            return _mock_subprocess(returncode=0)

        mock_exec.side_effect = _side_effect
        client = _mock_client()

        result = await assembly_app_publish(
            repository="iterwheel/voyager",
            branch="102-x",
            base="main",
            pr_title="x",
            client=client,
            cwd="/tmp",
            timeout_seconds=1,
        )

        assert result.pushed is False
        assert result.error is not None
        assert "git fetch timed out" in result.error
        timeout_proc.kill.assert_called_once()
        assert not any(
            len(call_args) > 1 and call_args[1] == "push"
            for call_args, _ in mock_exec.call_args_list
        )
        client.create_pull_request.assert_not_awaited()
        client.update_pull_request.assert_not_awaited()
        client.create_issue_comment.assert_not_awaited()


class TestPublishTimeout:
    """Timeout from git push converts to structured PublishResult."""

    @patch("voyager.core.publish.asyncio.create_subprocess_exec")
    async def test_timeout_returns_structured_failure(self, mock_exec: MagicMock) -> None:
        """A subprocess that times out returns pulled=False with a timeout error."""
        timeout_proc: MagicMock | None = None

        def _side_effect(*args: object, **kwargs: object) -> MagicMock:
            nonlocal timeout_proc
            if len(args) > 2 and str(args[1]) == "push":
                proc = MagicMock()
                proc.communicate = AsyncMock(side_effect=TimeoutError())
                proc.kill = MagicMock()
                timeout_proc = proc
                return proc
            return _mock_subprocess(returncode=0)

        mock_exec.side_effect = _side_effect

        client = _mock_client()

        result = await assembly_app_publish(
            repository="iterwheel/voyager",
            branch="102-x",
            base="main",
            pr_title="x",
            client=client,
            cwd="/tmp",
        )

        assert result.pushed is False
        assert result.pr_number is None
        assert result.error is not None
        assert "timed out" in (result.error or "").lower()
        # Verify kill was called on the timed-out process
        assert timeout_proc is not None
        timeout_proc.kill.assert_called_once()
        # No GitHub API calls beyond token
        client.create_pull_request.assert_not_awaited()
        client.update_pull_request.assert_not_awaited()
        client.create_issue_comment.assert_not_awaited()


class TestPublishPRCreateFailure:
    @patch("voyager.core.publish.asyncio.create_subprocess_exec")
    async def test_pr_create_failure_returns_error(self, mock_exec: MagicMock) -> None:
        mock_exec.return_value = _mock_subprocess(returncode=0)
        client = _mock_client()
        client.create_pull_request = AsyncMock(side_effect=RuntimeError("no PR"))

        result = await assembly_app_publish(
            repository="iterwheel/voyager",
            branch="102-x",
            base="main",
            pr_title="x",
            client=client,
            cwd="/tmp",
        )

        assert result.pushed is True  # push succeeded
        assert result.pr_number is None
        assert result.error is not None
        assert "create_pull_request" in (result.error or "")


class TestPublishCodexFailure:
    """Codex trigger failure is non-fatal — push and PR still succeed."""

    @patch("voyager.core.publish.asyncio.create_subprocess_exec")
    async def test_codex_failure_non_fatal(self, mock_exec: MagicMock) -> None:
        mock_exec.return_value = _mock_subprocess(returncode=0)
        client = _mock_client()
        client.create_issue_comment = AsyncMock(side_effect=RuntimeError("no comment"))

        result = await assembly_app_publish(
            repository="iterwheel/voyager",
            branch="102-x",
            base="main",
            pr_title="x",
            client=client,
            cwd="/tmp",
        )

        assert result.pushed is True
        assert result.pr_number == 42
        assert result.pr_action == "opened"
        # codex_comment_id should be None since it failed
        # (the call to create_issue_comment is still made — we just log the warning)
        assert result.error is None


# ---------------------------------------------------------------------------
# Askpass cleanup
# ---------------------------------------------------------------------------


class TestAskpassCleanup:
    """Verify the temporary askpass file is removed after publish."""

    @patch("voyager.core.publish.asyncio.create_subprocess_exec")
    async def test_askpass_removed_after_push(self, mock_exec: MagicMock) -> None:
        mock_exec.return_value = _mock_subprocess(returncode=0)
        client = _mock_client()

        # Track temp dirs created
        created_dirs: list[Path] = []

        original_mkdtemp = tempfile.mkdtemp

        def tracking_mkdtemp(*args: object, **kwargs: object) -> str:
            path = original_mkdtemp(*args, **kwargs)  # type: ignore[misc]
            created_dirs.append(Path(path))
            return path

        with patch("voyager.core.publish.tempfile.mkdtemp", tracking_mkdtemp):
            result = await assembly_app_publish(
                repository="iterwheel/voyager",
                branch="102-x",
                base="main",
                pr_title="x",
                client=client,
                cwd="/tmp",
            )

        assert result.pushed is True
        assert result.error is None

        # Temp dir should have been cleaned up
        for d in created_dirs:
            assert not d.exists(), f"Temp dir was not cleaned up: {d}"
