"""Unit tests for _get_investigator's env-over-TOML api_key precedence.

Round-1 advisory (4/4 reviewers): consumer-side precedence rule was tested
piece-by-piece (load_config field population in config.feature, env isolation
in BDD steps) but the composition at voyager/server.py:_get_investigator was
not directly exercised. This file closes that gap with the 2x2 truth table.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass(frozen=True)
class _StubProfile:
    name: str = "stub"
    model: str = "deepseek-v4-flash"
    thinking: bool = False
    reasoning_effort: str | None = None
    max_diff_chars: int = 20000
    min_confidence: float = 0.78


def _stub_config(api_key: str | None) -> object:
    """Minimal VoyagerConfig stand-in that exposes the fields _get_investigator reads."""

    @dataclass(frozen=True)
    class _Cfg:
        apps: dict = None
        work_dir: Path = Path("/tmp/voyager")
        profiles: dict = None
        default_profile: str | None = "stub"
        deepseek_api_key: str | None = None

    return _Cfg(
        apps={},
        work_dir=Path("/tmp/voyager"),
        profiles={"stub": _StubProfile()},
        default_profile="stub",
        deepseek_api_key=api_key,
    )


@pytest.fixture
def reset_investigator():
    """Reset server._investigator sentinel before/after each test."""
    from voyager import server

    server._investigator = server._SENTINEL
    yield
    server._investigator = server._SENTINEL


@pytest.fixture
def captured_api_key(monkeypatch):
    """Patch build_investigator_from_profile to record the api_key it sees."""
    captured: dict[str, str] = {}

    def _spy(_profile, *, api_key: str):
        captured["api_key"] = api_key
        return object()  # any truthy sentinel — _get_investigator stores this verbatim

    monkeypatch.setattr(
        "voyager.bots.clearance.investigator.build_investigator_from_profile",
        _spy,
    )
    return captured


def _patch_load_config(monkeypatch, cfg: object) -> None:
    monkeypatch.setattr("voyager.core.config.load_config", lambda: cfg)


def test_env_wins_over_toml(monkeypatch, reset_investigator, captured_api_key) -> None:
    """env set + cfg set → env value wins (12-factor)."""
    from voyager import server

    monkeypatch.setenv("VOYAGER_DEEPSEEK_API_KEY", "sk-env-wins")
    _patch_load_config(monkeypatch, _stub_config(api_key="sk-toml-loses"))

    result = server._get_investigator()

    assert result is not None
    assert captured_api_key["api_key"] == "sk-env-wins"


def test_toml_used_when_env_unset(monkeypatch, reset_investigator, captured_api_key) -> None:
    """env unset + cfg set → cfg fallback."""
    from voyager import server

    monkeypatch.delenv("VOYAGER_DEEPSEEK_API_KEY", raising=False)
    _patch_load_config(monkeypatch, _stub_config(api_key="sk-toml-only"))

    result = server._get_investigator()

    assert result is not None
    assert captured_api_key["api_key"] == "sk-toml-only"


def test_env_used_when_toml_unset(monkeypatch, reset_investigator, captured_api_key) -> None:
    """env set + cfg unset → env value used."""
    from voyager import server

    monkeypatch.setenv("VOYAGER_DEEPSEEK_API_KEY", "sk-env-only")
    _patch_load_config(monkeypatch, _stub_config(api_key=None))

    result = server._get_investigator()

    assert result is not None
    assert captured_api_key["api_key"] == "sk-env-only"


def test_returns_none_when_neither_set(monkeypatch, reset_investigator, captured_api_key) -> None:
    """env unset + cfg unset → None (feature off, no crash)."""
    from voyager import server

    monkeypatch.delenv("VOYAGER_DEEPSEEK_API_KEY", raising=False)
    _patch_load_config(monkeypatch, _stub_config(api_key=None))

    result = server._get_investigator()

    assert result is None
    assert "api_key" not in captured_api_key, (
        "build_investigator_from_profile should not be called when no key resolvable"
    )


def test_env_empty_string_disables_investigator(
    monkeypatch, reset_investigator, captured_api_key
) -> None:
    """Explicit env="" disables the investigator; TOML is NOT consulted.

    Codex GitHub-bot review on commit 9b8df45 raised this as P2: an operator
    who runs ``export VOYAGER_DEEPSEEK_API_KEY=""`` intends to clear the
    secret. The previous implementation conflated explicit-empty with unset
    and silently fell back to the TOML key — re-enabling what the operator
    explicitly disabled. This test locks the corrected semantic.
    """
    from voyager import server

    monkeypatch.setenv("VOYAGER_DEEPSEEK_API_KEY", "")
    _patch_load_config(monkeypatch, _stub_config(api_key="sk-toml-should-not-be-used"))

    result = server._get_investigator()

    assert result is None, (
        "explicit env='' must disable the investigator; "
        "TOML key must NOT be consulted (Codex GH review on 9b8df45)"
    )
    assert "api_key" not in captured_api_key, (
        "build_investigator_from_profile should not be called when env is "
        "explicitly empty — operator intent to disable must be honored"
    )
