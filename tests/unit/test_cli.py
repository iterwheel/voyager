"""Unit tests for the Typer ``vyg`` CLI (CHG-1820 Surface 10).

Uses ``typer.testing.CliRunner`` (never subprocess) with a ``monkeypatch``
guard on the uvicorn serve test so the runner never blocks.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import click
import pytest
from typer.testing import CliRunner

from voyager.cli import _store_refresh_token, _store_refresh_token_argv, app
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
    assert "user-device-code" in result.stdout
    assert "user-refresh-check" in result.stdout


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


def test_store_refresh_token_writes_recovery_file_when_child_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("VOYAGER_REFRESH_TOKEN_RECOVERY_DIR", str(tmp_path))
    command = f'{sys.executable} -c "import sys; sys.exit(7)"'

    with pytest.raises(
        click.ClickException, match="replacement refresh token was saved"
    ) as exc_info:
        _store_refresh_token(command, "secret-refresh")

    recovery_paths = list(tmp_path.glob("countdown-refresh-token-*.txt"))
    assert len(recovery_paths) == 1
    recovery_path = recovery_paths[0]
    assert str(recovery_path) in str(exc_info.value)
    assert recovery_path.read_text(encoding="utf-8") == "secret-refresh"
    assert recovery_path.stat().st_mode & 0o777 == 0o600


def test_store_refresh_token_writes_recovery_file_when_child_cannot_exec(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("VOYAGER_REFRESH_TOKEN_RECOVERY_DIR", str(tmp_path / "recovery"))
    broken_store = tmp_path / "broken-store"
    broken_store.write_text("not a valid executable format\n", encoding="utf-8")
    broken_store.chmod(0o700)

    with pytest.raises(
        click.ClickException, match="replacement refresh token was saved"
    ) as exc_info:
        _store_refresh_token(str(broken_store), "secret-refresh")

    recovery_paths = list((tmp_path / "recovery").glob("countdown-refresh-token-*.txt"))
    assert len(recovery_paths) == 1
    recovery_path = recovery_paths[0]
    assert str(recovery_path) in str(exc_info.value)
    assert recovery_path.read_text(encoding="utf-8") == "secret-refresh"
    assert recovery_path.stat().st_mode & 0o777 == 0o600


def test_store_refresh_token_writes_recovery_file_when_command_disappears_after_preflight(
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
        click.ClickException, match="replacement refresh token was saved"
    ) as exc_info:
        _store_refresh_token(command, "secret-refresh")

    recovery_paths = list((tmp_path / "recovery").glob("countdown-refresh-token-*.txt"))
    assert len(recovery_paths) == 1
    recovery_path = recovery_paths[0]
    assert str(recovery_path) in str(exc_info.value)
    assert recovery_path.read_text(encoding="utf-8") == "secret-refresh"
    assert recovery_path.stat().st_mode & 0o777 == 0o600


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
    assert "replacement refresh token was saved" in result.stderr
    assert "secret-refresh" not in result.stderr
    assert "secret-access" not in result.stderr
    assert "old-refresh" not in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""
    recovery_paths = list((tmp_path / "recovery").glob("countdown-refresh-token-*.txt"))
    assert len(recovery_paths) == 1
    assert recovery_paths[0].read_text(encoding="utf-8") == "secret-refresh"
    assert recovery_paths[0].stat().st_mode & 0o777 == 0o600


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
        client_id: str, device_code: str
    ) -> UserAccessTokenResponse:
        assert client_id == "Iv1.test"
        assert device_code == "secret-device"
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
