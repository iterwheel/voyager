"""Unit tests for the Typer ``vyg`` CLI (CHG-1820 Surface 10).

Uses ``typer.testing.CliRunner`` (never subprocess) with a ``monkeypatch``
guard on the uvicorn serve test so the runner never blocks.
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from typing import Any

import click
import pytest
from typer.testing import CliRunner

from voyager.cli import _read_pat_token, _store_refresh_token, _store_refresh_token_argv, app
from voyager.core.countdown_diagnostic import (
    DEDICATED_PAT_FALLBACK_PUBLIC_ACTOR,
    DEDICATED_PAT_FALLBACK_SLUG,
    ReviewThreadCapability,
    ReviewThreadCapabilityReport,
    ReviewThreadResolveCanaryReport,
    ReviewThreadResolveOperation,
)
from voyager.core.github_app import GitHubGraphQLError
from voyager.core.github_app_user_auth import DeviceCodeResponse, UserAccessTokenResponse

# Force a wide terminal in tests so Typer/Rich does not wrap `--host`
# across lines (CI defaults to ~80 cols and the help table breaks the
# flag names mid-token, e.g. `--ho\nst`, causing literal-substring
# assertions to fail on Linux runners while passing on a 200-col local
# terminal). `terminal_width` is the documented Click/Typer escape hatch.
runner = CliRunner(env={"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"})


def test_vyg_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "bridge" in result.stdout
    assert "countdown" in result.stdout
    assert "version" in result.stdout


def test_vyg_version_prints_version_and_build_commit_lines() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "version:" in result.stdout
    assert "build_commit:" in result.stdout


def test_vyg_bridge_help_shows_serve() -> None:
    result = runner.invoke(app, ["bridge", "--help"])
    assert result.exit_code == 0
    assert "serve" in result.stdout
    assert "check-drift" in result.stdout


def test_vyg_countdown_help_shows_review_thread_diagnostic() -> None:
    result = runner.invoke(app, ["countdown", "--help"])
    assert result.exit_code == 0
    assert "review-thread-diagnostic" in result.stdout
    assert "user-review-thread-diagnostic" in result.stdout
    assert "user-device-code" in result.stdout
    assert "user-refresh-check" in result.stdout


def test_read_pat_token_suppresses_child_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    command = f'{sys.executable} -c "import sys; print(\\"secret-pat\\"); print(\\"leak\\", file=sys.stderr)"'

    token = _read_pat_token(command)

    captured = capsys.readouterr()
    assert token == "secret-pat"
    assert captured.out == ""
    assert captured.err == ""


def test_vyg_countdown_review_thread_diagnostic_pat_command_avoids_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "VOYAGER_COUNTDOWN_DEDICATED_PAT_EXPECTED_LOGIN",
        "raw-machine-user-login",
    )

    async def fake_query_review_thread_capabilities(
        client: Any,
        *,
        app_slug: str,
        repository: str,
        pr: int,
        thread_ids: list[str],
    ) -> ReviewThreadCapabilityReport:
        del client
        return ReviewThreadCapabilityReport(
            app_slug=app_slug,
            actor_login="raw-machine-user-login",
            repository=repository,
            pr=pr,
            threads=(
                ReviewThreadCapability(
                    thread_id=thread_ids[0],
                    type_name="PullRequestReviewThread",
                    repository=repository,
                    pr=pr,
                    is_resolved=False,
                    is_outdated=False,
                    viewer_can_resolve=True,
                    viewer_can_reply=True,
                ),
            ),
        )

    monkeypatch.setattr(
        "voyager.core.countdown_diagnostic.query_review_thread_capabilities",
        fake_query_review_thread_capabilities,
    )
    monkeypatch.setattr(
        "voyager.core.config.load_config",
        lambda config=None: (_ for _ in ()).throw(AssertionError("config should not load")),
    )

    token_command = f'{sys.executable} -c "print(\\"secret-pat\\")"'
    result = runner.invoke(
        app,
        [
            "countdown",
            "review-thread-diagnostic",
            "--repo",
            "iterwheel/voyager-sandbox",
            "--pr",
            "42",
            "--thread-id",
            "PRRT_private",
            "--pat-token-command",
            token_command,
        ],
    )

    assert result.exit_code == 0
    assert f"actor: {DEDICATED_PAT_FALLBACK_PUBLIC_ACTOR}" in result.stdout
    assert "viewerCanResolve=True" in result.stdout
    assert "raw-machine-user-login" not in result.stdout
    assert "secret-pat" not in result.stdout


def test_vyg_countdown_review_thread_diagnostic_pat_query_requires_expected_login_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VOYAGER_COUNTDOWN_DEDICATED_PAT_EXPECTED_LOGIN", raising=False)
    monkeypatch.setattr(
        "voyager.core.config.load_config",
        lambda config=None: (_ for _ in ()).throw(AssertionError("config should not load")),
    )
    monkeypatch.setattr(
        "voyager.cli._read_pat_token",
        lambda command: (_ for _ in ()).throw(AssertionError("token should not load")),
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "review-thread-diagnostic",
            "--repo",
            "iterwheel/voyager-sandbox",
            "--pr",
            "42",
            "--thread-id",
            "PRRT_private",
            "--pat-token-command",
            "fake-token-command",
        ],
    )

    assert result.exit_code == 1
    assert "VOYAGER_COUNTDOWN_DEDICATED_PAT_EXPECTED_LOGIN is not set" in result.stderr


def test_vyg_countdown_review_thread_diagnostic_pat_query_blocks_wrong_pat_viewer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "VOYAGER_COUNTDOWN_DEDICATED_PAT_EXPECTED_LOGIN",
        "raw-machine-user-login",
    )

    async def fake_query_review_thread_capabilities(
        client: Any,
        *,
        app_slug: str,
        repository: str,
        pr: int,
        thread_ids: list[str],
    ) -> ReviewThreadCapabilityReport:
        del client
        return ReviewThreadCapabilityReport(
            app_slug=app_slug,
            actor_login="wrong-machine-user-login",
            repository=repository,
            pr=pr,
            threads=(
                ReviewThreadCapability(
                    thread_id=thread_ids[0],
                    type_name="PullRequestReviewThread",
                    repository=repository,
                    pr=pr,
                    is_resolved=False,
                    is_outdated=False,
                    viewer_can_resolve=True,
                    viewer_can_reply=True,
                ),
            ),
        )

    monkeypatch.setattr(
        "voyager.core.countdown_diagnostic.query_review_thread_capabilities",
        fake_query_review_thread_capabilities,
    )
    monkeypatch.setattr("voyager.cli._read_pat_token", lambda command: "secret-pat")
    monkeypatch.setattr(
        "voyager.core.config.load_config",
        lambda config=None: (_ for _ in ()).throw(AssertionError("config should not load")),
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "review-thread-diagnostic",
            "--repo",
            "iterwheel/voyager-sandbox",
            "--pr",
            "42",
            "--thread-id",
            "PRRT_private",
            "--pat-token-command",
            "fake-token-command",
        ],
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, click.ClickException)
    assert "refusing PAT fallback query" in str(result.exception)
    assert "wrong-machine-user-login" not in result.stdout
    assert "wrong-machine-user-login" not in result.stderr
    assert "secret-pat" not in result.stdout
    assert "secret-pat" not in result.stderr


def test_vyg_countdown_review_thread_diagnostic_empty_pat_command_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "VOYAGER_COUNTDOWN_DEDICATED_PAT_EXPECTED_LOGIN",
        "raw-machine-user-login",
    )
    monkeypatch.setattr(
        "voyager.core.config.load_config",
        lambda config=None: (_ for _ in ()).throw(AssertionError("config should not load")),
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "review-thread-diagnostic",
            "--repo",
            "iterwheel/voyager-sandbox",
            "--pr",
            "42",
            "--thread-id",
            "PRRT_private",
            "--pat-token-command",
            "",
        ],
    )

    assert result.exit_code == 1
    assert "--pat-token-command must not be empty" in result.stderr


def test_vyg_countdown_review_thread_diagnostic_pat_query_failure_uses_safe_error_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "VOYAGER_COUNTDOWN_DEDICATED_PAT_EXPECTED_LOGIN",
        "raw-machine-user-login",
    )

    async def fake_query_review_thread_capabilities(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("GitHub GraphQL dedicated PAT request failed: HTTP 401")

    monkeypatch.setattr(
        "voyager.core.countdown_diagnostic.query_review_thread_capabilities",
        fake_query_review_thread_capabilities,
    )
    monkeypatch.setattr("voyager.cli._read_pat_token", lambda command: "secret-pat")
    monkeypatch.setattr(
        "voyager.core.config.load_config",
        lambda config=None: (_ for _ in ()).throw(AssertionError("config should not load")),
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "review-thread-diagnostic",
            "--repo",
            "iterwheel/voyager-sandbox",
            "--pr",
            "42",
            "--thread-id",
            "PRRT_private",
            "--pat-token-command",
            "fake-token-command",
        ],
    )

    assert result.exit_code == 1
    assert "ERROR: GitHub GraphQL dedicated PAT request failed: HTTP 401" in result.stderr
    assert "Traceback" not in result.stderr
    assert "secret-pat" not in result.stderr
    assert result.stdout == ""


def test_vyg_countdown_review_thread_diagnostic_pat_graphql_errors_use_safe_error_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "VOYAGER_COUNTDOWN_DEDICATED_PAT_EXPECTED_LOGIN",
        "raw-machine-user-login",
    )

    async def fake_query_review_thread_capabilities(*args: Any, **kwargs: Any) -> None:
        raise GitHubGraphQLError([{"type": "FORBIDDEN", "message": "raw thread id and secret-pat"}])

    monkeypatch.setattr(
        "voyager.core.countdown_diagnostic.query_review_thread_capabilities",
        fake_query_review_thread_capabilities,
    )
    monkeypatch.setattr("voyager.cli._read_pat_token", lambda command: "secret-pat")
    monkeypatch.setattr(
        "voyager.core.config.load_config",
        lambda config=None: (_ for _ in ()).throw(AssertionError("config should not load")),
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "review-thread-diagnostic",
            "--repo",
            "iterwheel/voyager-sandbox",
            "--pr",
            "42",
            "--thread-id",
            "PRRT_private",
            "--pat-token-command",
            "fake-token-command",
        ],
    )

    assert result.exit_code == 1
    assert "ERROR: GitHub GraphQL returned 1 error(s); first type: FORBIDDEN" in (result.stderr)
    assert "Traceback" not in result.stderr
    assert "secret-pat" not in result.stderr
    assert "raw thread id" not in result.stderr
    assert result.stdout == ""


def test_vyg_countdown_review_thread_diagnostic_pat_resolve_requires_app_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_calls: list[str] = []
    events: list[str] = []
    monkeypatch.setenv(
        "VOYAGER_COUNTDOWN_DEDICATED_PAT_EXPECTED_LOGIN",
        "raw-machine-user-login",
    )

    async def fake_query_review_thread_capabilities(
        client: Any,
        *,
        app_slug: str,
        repository: str,
        pr: int,
        thread_ids: list[str],
    ) -> ReviewThreadCapabilityReport:
        del client
        baseline_calls.append(app_slug)
        if app_slug == "iterwheel-countdown":
            events.append("app-baseline")
            actor_login = "iterwheel-countdown[bot]"
            viewer_can_resolve = False
        else:
            events.append("pat-actor")
            actor_login = "raw-machine-user-login"
            viewer_can_resolve = True
        return ReviewThreadCapabilityReport(
            app_slug=app_slug,
            actor_login=actor_login,
            repository=repository,
            pr=pr,
            threads=(
                ReviewThreadCapability(
                    thread_id=thread_ids[0],
                    type_name="PullRequestReviewThread",
                    repository=repository,
                    pr=pr,
                    is_resolved=False,
                    is_outdated=False,
                    viewer_can_resolve=viewer_can_resolve,
                    viewer_can_reply=True,
                ),
            ),
        )

    async def fake_run_review_thread_resolve_canary(
        client: Any,
        *,
        app_slug: str,
        repository: str,
        pr: int,
        thread_ids: list[str],
    ) -> ReviewThreadResolveCanaryReport:
        del client
        assert events == ["app-baseline", "token", "pat-actor"]
        assert app_slug == DEDICATED_PAT_FALLBACK_SLUG
        before = ReviewThreadCapabilityReport(
            app_slug=app_slug,
            actor_login="raw-machine-user-login",
            repository=repository,
            pr=pr,
            threads=(
                ReviewThreadCapability(
                    thread_id=thread_ids[0],
                    type_name="PullRequestReviewThread",
                    repository=repository,
                    pr=pr,
                    is_resolved=False,
                    is_outdated=False,
                    viewer_can_resolve=True,
                    viewer_can_reply=True,
                ),
            ),
        )
        after = ReviewThreadCapabilityReport(
            app_slug=app_slug,
            actor_login="raw-machine-user-login",
            repository=repository,
            pr=pr,
            threads=(
                ReviewThreadCapability(
                    thread_id=thread_ids[0],
                    type_name="PullRequestReviewThread",
                    repository=repository,
                    pr=pr,
                    is_resolved=True,
                    is_outdated=False,
                    viewer_can_resolve=True,
                    viewer_can_reply=True,
                ),
            ),
        )
        return ReviewThreadResolveCanaryReport(
            before=before,
            operations=(
                ReviewThreadResolveOperation(
                    thread_id=thread_ids[0],
                    applied=True,
                    reason=None,
                    resolved_by="raw-machine-user-login",
                ),
            ),
            after=after,
        )

    monkeypatch.setattr(
        "voyager.core.countdown_diagnostic.query_review_thread_capabilities",
        fake_query_review_thread_capabilities,
    )
    monkeypatch.setattr(
        "voyager.core.countdown_diagnostic.run_review_thread_resolve_canary",
        fake_run_review_thread_resolve_canary,
    )
    monkeypatch.setattr(
        "voyager.core.config.load_config",
        lambda config=None: SimpleNamespace(apps={"iterwheel-countdown": object()}),
    )
    monkeypatch.setattr(
        "voyager.cli._read_pat_token",
        lambda command: events.append("token") or "secret-pat",
    )

    token_command = "fake-token-command"
    result = runner.invoke(
        app,
        [
            "countdown",
            "review-thread-diagnostic",
            "--repo",
            "iterwheel/voyager-sandbox",
            "--pr",
            "42",
            "--thread-id",
            "PRRT_private",
            "--pat-token-command",
            token_command,
            "--resolve",
        ],
    )

    assert result.exit_code == 0
    assert baseline_calls == ["iterwheel-countdown", DEDICATED_PAT_FALLBACK_SLUG]
    assert f"actor: {DEDICATED_PAT_FALLBACK_PUBLIC_ACTOR}" in result.stdout
    assert "applied=True" in result.stdout
    assert f"resolvedBy={DEDICATED_PAT_FALLBACK_PUBLIC_ACTOR}" in result.stdout
    assert "raw-machine-user-login" not in result.stdout
    assert "secret-pat" not in result.stdout


def test_vyg_countdown_review_thread_diagnostic_pat_resolve_requires_expected_login_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VOYAGER_COUNTDOWN_DEDICATED_PAT_EXPECTED_LOGIN", raising=False)
    monkeypatch.setattr(
        "voyager.core.config.load_config",
        lambda config=None: (_ for _ in ()).throw(AssertionError("config should not load")),
    )
    monkeypatch.setattr(
        "voyager.cli._read_pat_token",
        lambda command: (_ for _ in ()).throw(AssertionError("token should not load")),
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "review-thread-diagnostic",
            "--repo",
            "iterwheel/voyager-sandbox",
            "--pr",
            "42",
            "--thread-id",
            "PRRT_private",
            "--pat-token-command",
            "fake-token-command",
            "--resolve",
        ],
    )

    assert result.exit_code == 1
    assert "VOYAGER_COUNTDOWN_DEDICATED_PAT_EXPECTED_LOGIN is not set" in result.stderr


def test_vyg_countdown_review_thread_diagnostic_pat_resolve_blocks_wrong_pat_viewer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setenv(
        "VOYAGER_COUNTDOWN_DEDICATED_PAT_EXPECTED_LOGIN",
        "raw-machine-user-login",
    )

    async def fake_query_review_thread_capabilities(
        client: Any,
        *,
        app_slug: str,
        repository: str,
        pr: int,
        thread_ids: list[str],
    ) -> ReviewThreadCapabilityReport:
        del client
        if app_slug == "iterwheel-countdown":
            events.append("app-baseline")
            actor_login = "iterwheel-countdown[bot]"
            viewer_can_resolve = False
        else:
            events.append("pat-actor")
            actor_login = "wrong-machine-user-login"
            viewer_can_resolve = True
        return ReviewThreadCapabilityReport(
            app_slug=app_slug,
            actor_login=actor_login,
            repository=repository,
            pr=pr,
            threads=(
                ReviewThreadCapability(
                    thread_id=thread_ids[0],
                    type_name="PullRequestReviewThread",
                    repository=repository,
                    pr=pr,
                    is_resolved=False,
                    is_outdated=False,
                    viewer_can_resolve=viewer_can_resolve,
                    viewer_can_reply=True,
                ),
            ),
        )

    async def fake_run_review_thread_resolve_canary(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("PAT resolve should not run for the wrong viewer")

    monkeypatch.setattr(
        "voyager.core.countdown_diagnostic.query_review_thread_capabilities",
        fake_query_review_thread_capabilities,
    )
    monkeypatch.setattr(
        "voyager.core.countdown_diagnostic.run_review_thread_resolve_canary",
        fake_run_review_thread_resolve_canary,
    )
    monkeypatch.setattr(
        "voyager.core.config.load_config",
        lambda config=None: SimpleNamespace(apps={"iterwheel-countdown": object()}),
    )
    monkeypatch.setattr("voyager.cli._read_pat_token", lambda command: "secret-pat")

    result = runner.invoke(
        app,
        [
            "countdown",
            "review-thread-diagnostic",
            "--repo",
            "iterwheel/voyager-sandbox",
            "--pr",
            "42",
            "--thread-id",
            "PRRT_private",
            "--pat-token-command",
            "fake-token-command",
            "--resolve",
        ],
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, click.ClickException)
    assert "Dedicated PAT viewer did not match expected login" in str(result.exception)
    assert events == ["app-baseline", "pat-actor"]
    assert "wrong-machine-user-login" not in result.stdout
    assert "wrong-machine-user-login" not in result.stderr
    assert "secret-pat" not in result.stdout
    assert "secret-pat" not in result.stderr


def test_vyg_countdown_review_thread_diagnostic_pat_resolve_blocks_without_app_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "VOYAGER_COUNTDOWN_DEDICATED_PAT_EXPECTED_LOGIN",
        "raw-machine-user-login",
    )

    async def fake_query_review_thread_capabilities(
        client: Any,
        *,
        app_slug: str,
        repository: str,
        pr: int,
        thread_ids: list[str],
    ) -> ReviewThreadCapabilityReport:
        del client
        return ReviewThreadCapabilityReport(
            app_slug=app_slug,
            actor_login="iterwheel-countdown[bot]",
            repository=repository,
            pr=pr,
            threads=(
                ReviewThreadCapability(
                    thread_id=thread_ids[0],
                    type_name="PullRequestReviewThread",
                    repository=repository,
                    pr=pr,
                    is_resolved=False,
                    is_outdated=False,
                    viewer_can_resolve=True,
                    viewer_can_reply=True,
                ),
            ),
        )

    async def fake_run_review_thread_resolve_canary(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("PAT resolve should not run when App baseline can resolve")

    monkeypatch.setattr(
        "voyager.core.countdown_diagnostic.query_review_thread_capabilities",
        fake_query_review_thread_capabilities,
    )
    monkeypatch.setattr(
        "voyager.core.countdown_diagnostic.run_review_thread_resolve_canary",
        fake_run_review_thread_resolve_canary,
    )
    monkeypatch.setattr(
        "voyager.core.config.load_config",
        lambda config=None: SimpleNamespace(apps={"iterwheel-countdown": object()}),
    )
    monkeypatch.setattr(
        "voyager.cli._read_pat_token",
        lambda command: (_ for _ in ()).throw(
            AssertionError("token should not load before App baseline passes")
        ),
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "review-thread-diagnostic",
            "--repo",
            "iterwheel/voyager-sandbox",
            "--pr",
            "42",
            "--thread-id",
            "PRRT_private",
            "--pat-token-command",
            "fake-token-command",
            "--resolve",
        ],
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, click.ClickException)
    assert "Countdown App baseline viewerCanResolve is not false" in str(result.exception)
    assert "secret-pat" not in result.stdout
    assert "secret-pat" not in result.stderr


def test_vyg_countdown_review_thread_diagnostic_pat_resolve_requires_one_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "voyager.core.config.load_config",
        lambda config=None: (_ for _ in ()).throw(AssertionError("config should not load")),
    )
    monkeypatch.setattr(
        "voyager.cli._read_pat_token",
        lambda command: (_ for _ in ()).throw(AssertionError("token should not load")),
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "review-thread-diagnostic",
            "--repo",
            "iterwheel/voyager-sandbox",
            "--pr",
            "42",
            "--thread-id",
            "PRRT_private_1",
            "--thread-id",
            "PRRT_private_2",
            "--pat-token-command",
            "fake-token-command",
            "--resolve",
        ],
    )

    assert result.exit_code == 1
    assert "requires exactly one --thread-id" in result.stderr


def test_vyg_countdown_review_thread_diagnostic_pat_resolve_requires_sandbox_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "voyager.core.config.load_config",
        lambda config=None: (_ for _ in ()).throw(AssertionError("config should not load")),
    )
    monkeypatch.setattr(
        "voyager.cli._read_pat_token",
        lambda command: (_ for _ in ()).throw(AssertionError("token should not load")),
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "review-thread-diagnostic",
            "--repo",
            "iterwheel/voyager",
            "--pr",
            "215",
            "--thread-id",
            "PRRT_private",
            "--pat-token-command",
            "fake-token-command",
            "--resolve",
        ],
    )

    assert result.exit_code == 1
    assert "--pat-token-command --resolve is only allowed for: iterwheel/voyager-sandbox" in (
        result.stderr
    )


def test_vyg_countdown_review_thread_diagnostic_empty_pat_resolve_uses_pat_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "voyager.core.config.load_config",
        lambda config=None: (_ for _ in ()).throw(AssertionError("config should not load")),
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "review-thread-diagnostic",
            "--repo",
            "iterwheel/voyager",
            "--pr",
            "215",
            "--thread-id",
            "PRRT_private",
            "--pat-token-command",
            "",
            "--resolve",
        ],
    )

    assert result.exit_code == 1
    assert "--pat-token-command --resolve is only allowed for: iterwheel/voyager-sandbox" in (
        result.stderr
    )


def test_vyg_countdown_user_refresh_check_requires_env() -> None:
    result = runner.invoke(
        app,
        [
            "countdown",
            "user-refresh-check",
            "--client-id",
            "Iv1.test",
            "--refresh-token-env",
            "VOYAGER_TEST_MISSING_REFRESH_TOKEN",
        ],
    )
    assert result.exit_code == 1
    assert "VOYAGER_TEST_MISSING_REFRESH_TOKEN is not set" in result.stderr


def test_vyg_countdown_user_refresh_check_requires_store_command() -> None:
    result = runner.invoke(
        app,
        [
            "countdown",
            "user-refresh-check",
            "--client-id",
            "Iv1.test",
            "--refresh-token-env",
            "VOYAGER_TEST_REFRESH_TOKEN",
        ],
        env={"VOYAGER_TEST_REFRESH_TOKEN": "old-refresh"},
    )
    assert result.exit_code == 1
    assert "--store-refresh-token-command is required" in result.stderr


def test_store_refresh_token_suppresses_child_output(capsys: pytest.CaptureFixture[str]) -> None:
    command = (
        f'{sys.executable} -c "import sys; data=sys.stdin.read(); '
        'print(data); print(data, file=sys.stderr)"'
    )

    _store_refresh_token(command, "secret-refresh")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_vyg_countdown_user_refresh_check_preflights_store_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refresh_called = False

    async def fake_refresh_user_access_token(
        client_id: str, refresh_token: str
    ) -> UserAccessTokenResponse:
        nonlocal refresh_called
        refresh_called = True
        return UserAccessTokenResponse(
            access_token="secret-access",
            token_type="bearer",
            expires_in=28800,
            refresh_token="secret-refresh",
            refresh_token_expires_in=15897600,
        )

    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.refresh_user_access_token",
        fake_refresh_user_access_token,
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "user-refresh-check",
            "--client-id",
            "Iv1.test",
            "--refresh-token-env",
            "VOYAGER_TEST_REFRESH_TOKEN",
            "--store-refresh-token-command",
            "voyager-missing-secret-store-command",
        ],
        env={"VOYAGER_TEST_REFRESH_TOKEN": "old-refresh"},
    )

    assert result.exit_code == 1
    assert "ERROR: secret-store command executable not found" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""
    assert not refresh_called


def test_vyg_countdown_user_device_code_preflight_uses_safe_error_path() -> None:
    result = runner.invoke(
        app,
        [
            "countdown",
            "user-device-code",
            "--client-id",
            "Iv1.test",
            "--store-refresh-token-command",
            "voyager-missing-secret-store-command",
        ],
    )

    assert result.exit_code == 1
    assert "ERROR: secret-store command executable not found" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def test_vyg_countdown_user_refresh_check_preflight_rejects_malformed_store_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refresh_called = False

    async def fake_refresh_user_access_token(
        client_id: str, refresh_token: str
    ) -> UserAccessTokenResponse:
        nonlocal refresh_called
        refresh_called = True
        return UserAccessTokenResponse(
            access_token="secret-access",
            token_type="bearer",
            expires_in=28800,
            refresh_token="secret-refresh",
            refresh_token_expires_in=15897600,
        )

    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.refresh_user_access_token",
        fake_refresh_user_access_token,
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "user-refresh-check",
            "--client-id",
            "Iv1.test",
            "--refresh-token-env",
            "VOYAGER_TEST_REFRESH_TOKEN",
            "--store-refresh-token-command",
            '"unterminated',
        ],
        env={"VOYAGER_TEST_REFRESH_TOKEN": "old-refresh"},
    )

    assert result.exit_code == 1
    assert "ERROR: invalid --store-refresh-token-command" in result.stderr
    assert "Traceback" not in result.stderr
    assert "old-refresh" not in result.stderr
    assert result.stdout == ""
    assert not refresh_called


def test_store_refresh_token_fails_closed_when_child_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("VOYAGER_REFRESH_TOKEN_RECOVERY_DIR", str(tmp_path))
    command = f'{sys.executable} -c "import sys; sys.exit(7)"'

    with pytest.raises(
        click.ClickException, match="replacement refresh token was not stored"
    ) as exc_info:
        _store_refresh_token(command, "secret-refresh")

    assert "secret-refresh" not in str(exc_info.value)
    assert list(tmp_path.glob("countdown-refresh-token-*.txt")) == []


def test_store_refresh_token_fails_closed_when_child_cannot_exec(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("VOYAGER_REFRESH_TOKEN_RECOVERY_DIR", str(tmp_path / "recovery"))
    broken_store = tmp_path / "broken-store"
    broken_store.write_text("not a valid executable format\n", encoding="utf-8")
    broken_store.chmod(0o700)

    with pytest.raises(
        click.ClickException, match="replacement refresh token was not stored"
    ) as exc_info:
        _store_refresh_token(str(broken_store), "secret-refresh")

    assert "secret-refresh" not in str(exc_info.value)
    assert not (tmp_path / "recovery").exists()


def test_store_refresh_token_fails_closed_when_command_disappears_after_preflight(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("VOYAGER_REFRESH_TOKEN_RECOVERY_DIR", str(tmp_path / "recovery"))
    rotating_store = tmp_path / "rotating-store"
    rotating_store.write_text("#!/bin/sh\ncat >/dev/null\n", encoding="utf-8")
    rotating_store.chmod(0o700)
    command = str(rotating_store)

    assert _store_refresh_token_argv(command) == [command]
    rotating_store.unlink()

    with pytest.raises(
        click.ClickException, match="replacement refresh token was not stored"
    ) as exc_info:
        _store_refresh_token(command, "secret-refresh")

    assert "secret-refresh" not in str(exc_info.value)
    assert not (tmp_path / "recovery").exists()


def test_store_refresh_token_fails_closed_when_child_times_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("VOYAGER_REFRESH_TOKEN_RECOVERY_DIR", str(tmp_path / "recovery"))
    monkeypatch.setattr("voyager.cli._STORE_REFRESH_TOKEN_TIMEOUT_SECONDS", 0.01)
    command = f'{sys.executable} -c "import time; time.sleep(5)"'

    with pytest.raises(
        click.ClickException, match="replacement refresh token was not stored"
    ) as exc_info:
        _store_refresh_token(command, "secret-refresh")

    assert "secret-refresh" not in str(exc_info.value)
    assert not (tmp_path / "recovery").exists()


def test_vyg_countdown_user_refresh_check_store_failure_hides_token_locals(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("VOYAGER_REFRESH_TOKEN_RECOVERY_DIR", str(tmp_path / "recovery"))

    async def fake_refresh_user_access_token(
        client_id: str, refresh_token: str
    ) -> UserAccessTokenResponse:
        assert client_id == "Iv1.test"
        assert refresh_token == "old-refresh"
        return UserAccessTokenResponse(
            access_token="secret-access",
            token_type="bearer",
            expires_in=28800,
            refresh_token="secret-refresh",
            refresh_token_expires_in=15897600,
        )

    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.refresh_user_access_token",
        fake_refresh_user_access_token,
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "user-refresh-check",
            "--client-id",
            "Iv1.test",
            "--refresh-token-env",
            "VOYAGER_TEST_REFRESH_TOKEN",
            "--store-refresh-token-command",
            f'{sys.executable} -c "import sys; sys.exit(7)"',
        ],
        env={"VOYAGER_TEST_REFRESH_TOKEN": "old-refresh"},
    )

    assert result.exit_code == 1
    assert "Secret-store command failed" in result.stderr
    assert "replacement refresh token was not stored" in result.stderr
    assert "replacement refresh token was saved" not in result.stderr
    assert "secret-refresh" not in result.stderr
    assert "secret-access" not in result.stderr
    assert "old-refresh" not in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""
    assert not (tmp_path / "recovery").exists()


def test_vyg_countdown_user_refresh_check_refresh_failure_uses_safe_error_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    token_path = tmp_path / "refresh-token.txt"
    store_command = (
        f'{sys.executable} -c "import pathlib, sys; '
        "pathlib.Path(sys.argv[1]).write_text(sys.stdin.read(), encoding='utf-8')\" "
        f"{token_path}"
    )

    async def fake_refresh_user_access_token(
        client_id: str, refresh_token: str
    ) -> UserAccessTokenResponse:
        assert client_id == "Iv1.test"
        assert refresh_token == "old-refresh"
        raise RuntimeError("GitHub refresh failed: bad_refresh_token")

    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.refresh_user_access_token",
        fake_refresh_user_access_token,
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "user-refresh-check",
            "--client-id",
            "Iv1.test",
            "--refresh-token-env",
            "VOYAGER_TEST_REFRESH_TOKEN",
            "--store-refresh-token-command",
            store_command,
        ],
        env={"VOYAGER_TEST_REFRESH_TOKEN": "old-refresh"},
    )

    assert result.exit_code == 1
    assert "ERROR: GitHub refresh failed: bad_refresh_token" in result.stderr
    assert "Traceback" not in result.stderr
    assert "old-refresh" not in result.stderr
    assert result.stdout == ""
    assert not token_path.exists()


def test_vyg_countdown_user_refresh_check_viewer_failure_uses_safe_error_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    token_path = tmp_path / "refresh-token.txt"
    store_command = (
        f'{sys.executable} -c "import pathlib, sys; '
        "pathlib.Path(sys.argv[1]).write_text(sys.stdin.read(), encoding='utf-8')\" "
        f"{token_path}"
    )

    async def fake_refresh_user_access_token(
        client_id: str, refresh_token: str
    ) -> UserAccessTokenResponse:
        assert client_id == "Iv1.test"
        assert refresh_token == "old-refresh"
        return UserAccessTokenResponse(
            access_token="secret-access",
            token_type="bearer",
            expires_in=28800,
            refresh_token="secret-refresh",
            refresh_token_expires_in=15897600,
        )

    async def fake_query_viewer_login(access_token: str) -> str:
        assert access_token == "secret-access"
        raise RuntimeError("GitHub GraphQL viewer query failed: HTTP 401")

    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.refresh_user_access_token",
        fake_refresh_user_access_token,
    )
    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.query_viewer_login",
        fake_query_viewer_login,
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "user-refresh-check",
            "--client-id",
            "Iv1.test",
            "--refresh-token-env",
            "VOYAGER_TEST_REFRESH_TOKEN",
            "--store-refresh-token-command",
            store_command,
            "--check-viewer",
        ],
        env={"VOYAGER_TEST_REFRESH_TOKEN": "old-refresh"},
    )

    assert result.exit_code == 1
    assert "ERROR: GitHub GraphQL viewer query failed: HTTP 401" in result.stderr
    assert "Traceback" not in result.stderr
    assert "secret-access" not in result.stderr
    assert "secret-refresh" not in result.stderr
    assert "old-refresh" not in result.stderr
    assert result.stdout == ""
    assert token_path.read_text(encoding="utf-8") == "secret-refresh"


def test_vyg_countdown_user_refresh_check_expected_viewer_match_stores_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    token_path = tmp_path / "refresh-token.txt"
    store_command = (
        f'{sys.executable} -c "import pathlib, sys; '
        "pathlib.Path(sys.argv[1]).write_text(sys.stdin.read(), encoding='utf-8')\" "
        f"{token_path}"
    )

    async def fake_refresh_user_access_token(
        client_id: str, refresh_token: str
    ) -> UserAccessTokenResponse:
        assert client_id == "Iv1.test"
        assert refresh_token == "old-refresh"
        return UserAccessTokenResponse(
            access_token="secret-access",
            token_type="bearer",
            expires_in=28800,
            refresh_token="secret-refresh",
            refresh_token_expires_in=15897600,
        )

    async def fake_query_viewer_login(access_token: str) -> str:
        assert access_token == "secret-access"
        return "Maintainer"

    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.refresh_user_access_token",
        fake_refresh_user_access_token,
    )
    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.query_viewer_login",
        fake_query_viewer_login,
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "user-refresh-check",
            "--client-id",
            "Iv1.test",
            "--refresh-token-env",
            "VOYAGER_TEST_REFRESH_TOKEN",
            "--store-refresh-token-command",
            store_command,
            "--expected-viewer-login-env",
            "VOYAGER_EXPECTED_VIEWER",
        ],
        env={
            "VOYAGER_TEST_REFRESH_TOKEN": "old-refresh",
            "VOYAGER_EXPECTED_VIEWER": "maintainer",
        },
    )

    assert result.exit_code == 0
    assert "viewer_login_present: True" in result.stdout
    assert "viewer_login_matches_expected: True" in result.stdout
    assert "Maintainer" not in result.stdout
    assert "maintainer" not in result.stdout
    assert "secret-access" not in result.stdout
    assert "secret-refresh" not in result.stdout
    assert token_path.read_text(encoding="utf-8") == "secret-refresh"


def test_vyg_countdown_user_refresh_check_expected_viewer_mismatch_does_not_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    token_path = tmp_path / "refresh-token.txt"
    store_command = (
        f'{sys.executable} -c "import pathlib, sys; '
        "pathlib.Path(sys.argv[1]).write_text(sys.stdin.read(), encoding='utf-8')\" "
        f"{token_path}"
    )

    async def fake_refresh_user_access_token(
        client_id: str, refresh_token: str
    ) -> UserAccessTokenResponse:
        assert client_id == "Iv1.test"
        assert refresh_token == "old-refresh"
        return UserAccessTokenResponse(
            access_token="secret-access",
            token_type="bearer",
            expires_in=28800,
            refresh_token="secret-refresh",
            refresh_token_expires_in=15897600,
        )

    async def fake_query_viewer_login(access_token: str) -> str:
        assert access_token == "secret-access"
        return "other-user"

    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.refresh_user_access_token",
        fake_refresh_user_access_token,
    )
    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.query_viewer_login",
        fake_query_viewer_login,
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "user-refresh-check",
            "--client-id",
            "Iv1.test",
            "--refresh-token-env",
            "VOYAGER_TEST_REFRESH_TOKEN",
            "--store-refresh-token-command",
            store_command,
            "--expected-viewer-login-env",
            "VOYAGER_EXPECTED_VIEWER",
        ],
        env={
            "VOYAGER_TEST_REFRESH_TOKEN": "old-refresh",
            "VOYAGER_EXPECTED_VIEWER": "maintainer",
        },
    )

    assert result.exit_code == 1
    assert "ERROR: GitHub viewer login did not match expected account" in result.stderr
    assert "other-user" not in result.stderr
    assert "maintainer" not in result.stderr
    assert "secret-access" not in result.stderr
    assert "secret-refresh" not in result.stderr
    assert not token_path.exists()


def test_vyg_countdown_user_refresh_check_expected_viewer_requires_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    token_path = tmp_path / "refresh-token.txt"
    refresh_called = False
    store_command = (
        f'{sys.executable} -c "import pathlib, sys; '
        "pathlib.Path(sys.argv[1]).write_text(sys.stdin.read(), encoding='utf-8')\" "
        f"{token_path}"
    )

    async def fake_refresh_user_access_token(
        client_id: str, refresh_token: str
    ) -> UserAccessTokenResponse:
        nonlocal refresh_called
        refresh_called = True
        return UserAccessTokenResponse(
            access_token="secret-access",
            token_type="bearer",
            expires_in=28800,
            refresh_token="secret-refresh",
            refresh_token_expires_in=15897600,
        )

    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.refresh_user_access_token",
        fake_refresh_user_access_token,
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "user-refresh-check",
            "--client-id",
            "Iv1.test",
            "--refresh-token-env",
            "VOYAGER_TEST_REFRESH_TOKEN",
            "--store-refresh-token-command",
            store_command,
            "--expected-viewer-login-env",
            "VOYAGER_EXPECTED_VIEWER",
        ],
        env={"VOYAGER_TEST_REFRESH_TOKEN": "old-refresh"},
    )

    assert result.exit_code == 1
    assert "ERROR: VOYAGER_EXPECTED_VIEWER is not set" in result.stderr
    assert not refresh_called
    assert not token_path.exists()


def test_vyg_countdown_user_review_thread_diagnostic_redacts_sensitive_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    token_path = tmp_path / "refresh-token.txt"
    store_command = (
        f'{sys.executable} -c "import pathlib, sys; '
        "pathlib.Path(sys.argv[1]).write_text(sys.stdin.read(), encoding='utf-8')\" "
        f"{token_path}"
    )

    async def fake_refresh_user_access_token(
        client_id: str, refresh_token: str
    ) -> UserAccessTokenResponse:
        assert client_id == "Iv1.test"
        assert refresh_token == "old-refresh"
        return UserAccessTokenResponse(
            access_token="secret-access",
            token_type="bearer",
            expires_in=28800,
            refresh_token="secret-refresh",
            refresh_token_expires_in=15897600,
        )

    async def fake_query_viewer_login(access_token: str) -> str:
        assert access_token == "secret-access"
        return "Maintainer"

    class FakeUserAccessClient:
        def __init__(self, access_token: str) -> None:
            assert access_token == "secret-access"

        async def aclose(self) -> None:
            return None

    class FakeCapabilityReport:
        def to_public_dict(self) -> dict[str, Any]:
            return {
                "app_slug": "github-app-user",
                "actor_login": "Maintainer",
                "repo": "iterwheel/voyager-sandbox",
                "pr": 69,
                "threads": [
                    {
                        "thread_id": "PRRT_secret",
                        "type": "PullRequestReviewThread",
                        "repo": "iterwheel/voyager-sandbox",
                        "pr": 69,
                        "isResolved": False,
                        "isOutdated": False,
                        "viewerCanResolve": True,
                        "viewerCanReply": True,
                        "error": None,
                    }
                ],
            }

    async def fake_query_review_thread_capabilities(
        client: Any,
        *,
        app_slug: str,
        repository: str,
        pr: int,
        thread_ids: list[str],
    ) -> FakeCapabilityReport:
        assert isinstance(client, FakeUserAccessClient)
        assert app_slug == "github-app-user"
        assert repository == "iterwheel/voyager-sandbox"
        assert pr == 69
        assert thread_ids == ["PRRT_secret"]
        return FakeCapabilityReport()

    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.refresh_user_access_token",
        fake_refresh_user_access_token,
    )
    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.query_viewer_login",
        fake_query_viewer_login,
    )
    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.GitHubUserAccessClient",
        FakeUserAccessClient,
    )
    monkeypatch.setattr(
        "voyager.core.countdown_diagnostic.query_review_thread_capabilities",
        fake_query_review_thread_capabilities,
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "user-review-thread-diagnostic",
            "--client-id",
            "Iv1.test",
            "--repo",
            "iterwheel/voyager-sandbox",
            "--pr",
            "69",
            "--thread-id",
            "PRRT_secret",
            "--refresh-token-env",
            "VOYAGER_TEST_REFRESH_TOKEN",
            "--store-refresh-token-command",
            store_command,
            "--expected-viewer-login-env",
            "VOYAGER_EXPECTED_VIEWER",
            "--json",
        ],
        env={
            "VOYAGER_TEST_REFRESH_TOKEN": "old-refresh",
            "VOYAGER_EXPECTED_VIEWER": "maintainer",
        },
    )

    assert result.exit_code == 0
    assert "secret-access" not in result.stdout
    assert "secret-refresh" not in result.stdout
    assert "Maintainer" not in result.stdout
    assert "maintainer" not in result.stdout
    assert "PRRT_secret" not in result.stdout
    public_result = json.loads(result.stdout)
    assert public_result["viewer_login_present"] is True
    assert public_result["viewer_login_matches_expected"] is True
    assert public_result["replacement_refresh_token_stored"] is True
    assert public_result["diagnostic"]["actor_login_present"] is True
    assert public_result["diagnostic"]["repo_present"] is True
    assert public_result["diagnostic"]["pr_present"] is True
    assert "repo" not in public_result["diagnostic"]
    assert "pr" not in public_result["diagnostic"]
    thread = public_result["diagnostic"]["threads"][0]
    assert thread["thread_id_present"] is True
    assert thread["repo_present"] is True
    assert thread["repo_matches_report"] is True
    assert thread["pr_present"] is True
    assert thread["pr_matches_report"] is True
    assert "repo" not in thread
    assert "pr" not in thread
    assert thread["viewerCanResolve"] is True
    assert token_path.read_text(encoding="utf-8") == "secret-refresh"


def test_vyg_countdown_user_review_thread_diagnostic_viewer_mismatch_does_not_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    token_path = tmp_path / "refresh-token.txt"
    store_command = (
        f'{sys.executable} -c "import pathlib, sys; '
        "pathlib.Path(sys.argv[1]).write_text(sys.stdin.read(), encoding='utf-8')\" "
        f"{token_path}"
    )

    async def fake_refresh_user_access_token(
        client_id: str, refresh_token: str
    ) -> UserAccessTokenResponse:
        assert client_id == "Iv1.test"
        assert refresh_token == "old-refresh"
        return UserAccessTokenResponse(
            access_token="secret-access",
            token_type="bearer",
            expires_in=28800,
            refresh_token="secret-refresh",
            refresh_token_expires_in=15897600,
        )

    async def fake_query_viewer_login(access_token: str) -> str:
        assert access_token == "secret-access"
        return "other-user"

    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.refresh_user_access_token",
        fake_refresh_user_access_token,
    )
    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.query_viewer_login",
        fake_query_viewer_login,
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "user-review-thread-diagnostic",
            "--client-id",
            "Iv1.test",
            "--repo",
            "iterwheel/voyager-sandbox",
            "--pr",
            "69",
            "--thread-id",
            "PRRT_secret",
            "--refresh-token-env",
            "VOYAGER_TEST_REFRESH_TOKEN",
            "--store-refresh-token-command",
            store_command,
            "--expected-viewer-login-env",
            "VOYAGER_EXPECTED_VIEWER",
            "--json",
        ],
        env={
            "VOYAGER_TEST_REFRESH_TOKEN": "old-refresh",
            "VOYAGER_EXPECTED_VIEWER": "maintainer",
        },
    )

    assert result.exit_code == 1
    assert "ERROR: GitHub viewer login did not match expected account" in result.stderr
    assert "other-user" not in result.stderr
    assert "maintainer" not in result.stderr
    assert "secret-access" not in result.stderr
    assert "secret-refresh" not in result.stderr
    assert not token_path.exists()


def test_vyg_countdown_user_review_thread_diagnostic_viewer_query_failure_stores_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    token_path = tmp_path / "refresh-token.txt"
    store_command = (
        f'{sys.executable} -c "import pathlib, sys; '
        "pathlib.Path(sys.argv[1]).write_text(sys.stdin.read(), encoding='utf-8')\" "
        f"{token_path}"
    )

    async def fake_refresh_user_access_token(
        client_id: str, refresh_token: str
    ) -> UserAccessTokenResponse:
        assert client_id == "Iv1.test"
        assert refresh_token == "old-refresh"
        return UserAccessTokenResponse(
            access_token="secret-access",
            token_type="bearer",
            expires_in=28800,
            refresh_token="secret-refresh",
            refresh_token_expires_in=15897600,
        )

    async def fake_query_viewer_login(access_token: str) -> str:
        assert access_token == "secret-access"
        raise RuntimeError("GitHub GraphQL viewer query failed: HTTP request error")

    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.refresh_user_access_token",
        fake_refresh_user_access_token,
    )
    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.query_viewer_login",
        fake_query_viewer_login,
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "user-review-thread-diagnostic",
            "--client-id",
            "Iv1.test",
            "--repo",
            "iterwheel/voyager-sandbox",
            "--pr",
            "69",
            "--thread-id",
            "PRRT_secret",
            "--refresh-token-env",
            "VOYAGER_TEST_REFRESH_TOKEN",
            "--store-refresh-token-command",
            store_command,
            "--expected-viewer-login-env",
            "VOYAGER_EXPECTED_VIEWER",
            "--json",
        ],
        env={
            "VOYAGER_TEST_REFRESH_TOKEN": "old-refresh",
            "VOYAGER_EXPECTED_VIEWER": "maintainer",
        },
    )

    assert result.exit_code == 1
    assert "ERROR: GitHub GraphQL viewer query failed: HTTP request error" in result.stderr
    assert "secret-access" not in result.stderr
    assert "secret-refresh" not in result.stderr
    assert token_path.read_text(encoding="utf-8") == "secret-refresh"


def test_vyg_countdown_user_device_code_json_emits_completion_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    token_path = tmp_path / "refresh-token.txt"
    events: list[str] = []

    async def fake_request_device_code(client_id: str) -> DeviceCodeResponse:
        assert client_id == "Iv1.test"
        return DeviceCodeResponse(
            device_code="secret-device",
            user_code="ABCD-1234",
            verification_uri="https://github.com/login/device",
            expires_in=900,
            interval=1,
        )

    async def fake_exchange_device_code(
        client_id: str, device_code: str, repository_id: int | None = None
    ) -> UserAccessTokenResponse:
        assert client_id == "Iv1.test"
        assert device_code == "secret-device"
        assert repository_id is None
        assert events == ["sleep:1"]
        return UserAccessTokenResponse(
            access_token="secret-access",
            token_type="bearer",
            expires_in=28800,
            refresh_token="secret-refresh",
            refresh_token_expires_in=15897600,
        )

    async def fake_sleep(interval: int) -> None:
        events.append(f"sleep:{interval}")

    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.request_device_code", fake_request_device_code
    )
    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.exchange_device_code", fake_exchange_device_code
    )
    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    store_command = (
        f'{sys.executable} -c "import pathlib, sys; '
        "pathlib.Path(sys.argv[1]).write_text(sys.stdin.read(), encoding='utf-8')\" "
        f"{token_path}"
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "user-device-code",
            "--client-id",
            "Iv1.test",
            "--store-refresh-token-command",
            store_command,
            "--json",
        ],
    )

    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert lines == [
        {
            "event": "device_code",
            "expires_in": 900,
            "interval": 1,
            "user_code": "ABCD-1234",
            "verification_uri": "https://github.com/login/device",
        },
        {
            "event": "authorization_complete",
            "expires_in": 28800,
            "refresh_token_expires_in": 15897600,
            "refresh_token_present": True,
            "refresh_token_stored": True,
            "scope": None,
            "token_type": "bearer",
        },
    ]
    assert "secret" not in result.stdout
    assert token_path.read_text(encoding="utf-8") == "secret-refresh"


def test_vyg_countdown_user_device_code_expected_viewer_match_stores_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    token_path = tmp_path / "refresh-token.txt"

    async def fake_request_device_code(client_id: str) -> DeviceCodeResponse:
        assert client_id == "Iv1.test"
        return DeviceCodeResponse(
            device_code="secret-device",
            user_code="ABCD-1234",
            verification_uri="https://github.com/login/device",
            expires_in=900,
            interval=1,
        )

    async def fake_exchange_device_code(
        client_id: str, device_code: str, repository_id: int | None = None
    ) -> UserAccessTokenResponse:
        assert client_id == "Iv1.test"
        assert device_code == "secret-device"
        assert repository_id == 12345
        return UserAccessTokenResponse(
            access_token="secret-access",
            token_type="bearer",
            expires_in=28800,
            refresh_token="secret-refresh",
            refresh_token_expires_in=15897600,
        )

    async def fake_query_viewer_login(access_token: str) -> str:
        assert access_token == "secret-access"
        return "Maintainer"

    async def fake_sleep(interval: int) -> None:
        assert interval == 1

    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.request_device_code", fake_request_device_code
    )
    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.exchange_device_code", fake_exchange_device_code
    )
    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.query_viewer_login",
        fake_query_viewer_login,
    )
    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    store_command = (
        f'{sys.executable} -c "import pathlib, sys; '
        "pathlib.Path(sys.argv[1]).write_text(sys.stdin.read(), encoding='utf-8')\" "
        f"{token_path}"
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "user-device-code",
            "--client-id",
            "Iv1.test",
            "--store-refresh-token-command",
            store_command,
            "--expected-viewer-login-env",
            "VOYAGER_EXPECTED_VIEWER",
            "--repository-id",
            "12345",
        ],
        env={"VOYAGER_EXPECTED_VIEWER": "maintainer"},
    )

    assert result.exit_code == 0
    assert "viewer_login_present: True" in result.stdout
    assert "viewer_login_matches_expected: True" in result.stdout
    assert "Maintainer" not in result.stdout
    assert "maintainer" not in result.stdout
    assert "secret-access" not in result.stdout
    assert "secret-refresh" not in result.stdout
    assert token_path.read_text(encoding="utf-8") == "secret-refresh"


def test_vyg_countdown_user_device_code_expected_viewer_mismatch_does_not_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    token_path = tmp_path / "refresh-token.txt"

    async def fake_request_device_code(client_id: str) -> DeviceCodeResponse:
        assert client_id == "Iv1.test"
        return DeviceCodeResponse(
            device_code="secret-device",
            user_code="ABCD-1234",
            verification_uri="https://github.com/login/device",
            expires_in=900,
            interval=1,
        )

    async def fake_exchange_device_code(
        client_id: str, device_code: str, repository_id: int | None = None
    ) -> UserAccessTokenResponse:
        assert client_id == "Iv1.test"
        assert device_code == "secret-device"
        assert repository_id is None
        return UserAccessTokenResponse(
            access_token="secret-access",
            token_type="bearer",
            expires_in=28800,
            refresh_token="secret-refresh",
            refresh_token_expires_in=15897600,
        )

    async def fake_query_viewer_login(access_token: str) -> str:
        assert access_token == "secret-access"
        return "other-user"

    async def fake_sleep(interval: int) -> None:
        assert interval == 1

    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.request_device_code", fake_request_device_code
    )
    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.exchange_device_code", fake_exchange_device_code
    )
    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.query_viewer_login",
        fake_query_viewer_login,
    )
    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    store_command = (
        f'{sys.executable} -c "import pathlib, sys; '
        "pathlib.Path(sys.argv[1]).write_text(sys.stdin.read(), encoding='utf-8')\" "
        f"{token_path}"
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "user-device-code",
            "--client-id",
            "Iv1.test",
            "--store-refresh-token-command",
            store_command,
            "--expected-viewer-login-env",
            "VOYAGER_EXPECTED_VIEWER",
        ],
        env={"VOYAGER_EXPECTED_VIEWER": "maintainer"},
    )

    assert result.exit_code == 1
    assert "ERROR: GitHub viewer login did not match expected account" in result.stderr
    assert "other-user" not in result.stderr
    assert "maintainer" not in result.stderr
    assert "secret-access" not in result.stderr
    assert "secret-refresh" not in result.stderr
    assert not token_path.exists()


def test_vyg_countdown_user_device_code_request_failure_uses_safe_error_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    token_path = tmp_path / "refresh-token.txt"
    store_command = (
        f'{sys.executable} -c "import pathlib, sys; '
        "pathlib.Path(sys.argv[1]).write_text(sys.stdin.read(), encoding='utf-8')\" "
        f"{token_path}"
    )

    async def fake_request_device_code(client_id: str) -> DeviceCodeResponse:
        assert client_id == "Iv1.test"
        raise RuntimeError("GitHub device authorization failed: device_flow_disabled")

    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.request_device_code", fake_request_device_code
    )

    result = runner.invoke(
        app,
        [
            "countdown",
            "user-device-code",
            "--client-id",
            "Iv1.test",
            "--store-refresh-token-command",
            store_command,
        ],
    )

    assert result.exit_code == 1
    assert "ERROR: GitHub device authorization failed: device_flow_disabled" in result.stderr
    assert "Traceback" not in result.stderr
    assert "Iv1.test" not in result.stderr
    assert result.stdout == ""
    assert not token_path.exists()


def test_vyg_countdown_user_device_code_exchange_failure_uses_safe_error_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    token_path = tmp_path / "refresh-token.txt"
    store_command = (
        f'{sys.executable} -c "import pathlib, sys; '
        "pathlib.Path(sys.argv[1]).write_text(sys.stdin.read(), encoding='utf-8')\" "
        f"{token_path}"
    )

    async def fake_request_device_code(client_id: str) -> DeviceCodeResponse:
        assert client_id == "Iv1.test"
        return DeviceCodeResponse(
            device_code="secret-device",
            user_code="ABCD-1234",
            verification_uri="https://github.com/login/device",
            expires_in=900,
            interval=1,
        )

    async def fake_exchange_device_code(
        client_id: str, device_code: str, repository_id: int | None = None
    ) -> UserAccessTokenResponse:
        assert client_id == "Iv1.test"
        assert device_code == "secret-device"
        assert repository_id is None
        raise RuntimeError("GitHub device authorization not complete: HTTP 429")

    async def fake_sleep(interval: int) -> None:
        assert interval == 1

    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.request_device_code", fake_request_device_code
    )
    monkeypatch.setattr(
        "voyager.core.github_app_user_auth.exchange_device_code", fake_exchange_device_code
    )
    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    result = runner.invoke(
        app,
        [
            "countdown",
            "user-device-code",
            "--client-id",
            "Iv1.test",
            "--store-refresh-token-command",
            store_command,
        ],
    )

    assert result.exit_code == 1
    assert "ERROR: GitHub device authorization not complete: HTTP 429" in result.stderr
    assert "Traceback" not in result.stderr
    assert "secret-device" not in result.stderr
    assert "secret-device" not in result.stdout
    assert "device_code: [redacted]" in result.stdout
    assert not token_path.exists()


def test_vyg_bridge_serve_help_lists_flags() -> None:
    result = runner.invoke(app, ["bridge", "serve", "--help"])
    assert result.exit_code == 0
    assert "--host" in result.stdout
    assert "--port" in result.stdout
    assert "--log-level" in result.stdout


def test_vyg_bridge_serve_invokes_uvicorn_with_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(*args: Any, **kwargs: Any) -> None:
        calls.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr("voyager.cli.uvicorn.run", fake_run)
    result = runner.invoke(app, ["bridge", "serve"])
    assert result.exit_code == 0
    assert len(calls) == 1
    kwargs = calls[0]["kwargs"]
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8787
    assert kwargs["log_level"] == "info"
