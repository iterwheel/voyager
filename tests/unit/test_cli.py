"""Unit tests for the Typer ``vyg`` CLI (CHG-1820 Surface 10).

Uses ``typer.testing.CliRunner`` (never subprocess) with a ``monkeypatch``
guard on the uvicorn serve test so the runner never blocks.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from voyager.cli import app

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
