"""Step definitions for TOML config loader BDD scenarios."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

# CRITICAL: do NOT import from voyager.* at module top level — import lazily
# INSIDE step functions to avoid collection-time crashes.

scenarios("../features/config.feature")

FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures" / "config"


# ---------------------------------------------------------------------------
# Per-scenario mutable state
# ---------------------------------------------------------------------------


@pytest.fixture
def state() -> dict[str, Any]:
    return {
        "config_path": None,
        "env_path": None,
        "config": None,
        "raised": None,
    }


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------


@given(parsers.parse('the TOML config file "{filename}"'), target_fixture="state")
def given_toml_file(filename: str) -> dict[str, Any]:
    path = FIXTURES_DIR / filename
    return {"config_path": path, "env_path": None, "config": None, "raised": None}


@given(
    parsers.parse('the TOML config file "{filename}" is set via VOYAGER_CONFIG_PATH'),
    target_fixture="state",
)
def given_toml_via_env(filename: str) -> dict[str, Any]:
    path = FIXTURES_DIR / filename
    return {"config_path": None, "env_path": str(path), "config": None, "raised": None}


@given(parsers.parse('a nonexistent config path "{path}"'), target_fixture="state")
def given_nonexistent_path(path: str) -> dict[str, Any]:
    return {"config_path": Path(path), "env_path": None, "config": None, "raised": None}


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when("the config is loaded", target_fixture="state")
def when_load_config(state: dict[str, Any]) -> dict[str, Any]:
    from voyager.core.config import load_config  # lazy

    if state.get("env_path"):
        old = os.environ.get("VOYAGER_CONFIG_PATH")
        os.environ["VOYAGER_CONFIG_PATH"] = state["env_path"]
        try:
            state["config"] = load_config()
        finally:
            if old is None:
                os.environ.pop("VOYAGER_CONFIG_PATH", None)
            else:
                os.environ["VOYAGER_CONFIG_PATH"] = old
    else:
        state["config"] = load_config(state["config_path"])
    return state


@when("the config load is attempted", target_fixture="state")
def when_load_config_attempt(state: dict[str, Any]) -> dict[str, Any]:
    from voyager.core.config import load_config  # lazy

    try:
        state["config"] = load_config(state["config_path"])
    except (ValueError, FileNotFoundError) as exc:
        state["raised"] = exc
    return state


@when("the config is loaded without an explicit path", target_fixture="state")
def when_load_config_no_path(state: dict[str, Any]) -> dict[str, Any]:
    from voyager.core.config import load_config  # lazy

    old = os.environ.get("VOYAGER_CONFIG_PATH")
    if state.get("env_path"):
        os.environ["VOYAGER_CONFIG_PATH"] = state["env_path"]
    try:
        state["config"] = load_config()
    finally:
        if old is None:
            os.environ.pop("VOYAGER_CONFIG_PATH", None)
        else:
            os.environ["VOYAGER_CONFIG_PATH"] = old
    return state


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------


@then(parsers.parse("the apps dict has {count:d} entries"))
def then_apps_count(state: dict[str, Any], count: int) -> None:
    cfg = state["config"]
    assert cfg is not None, "Config was not loaded"
    assert len(cfg.apps) == count, f"Expected {count} apps, got {len(cfg.apps)}: {list(cfg.apps)}"


@then(parsers.parse('the apps dict contains slug "{slug}"'))
def then_apps_has_slug(state: dict[str, Any], slug: str) -> None:
    cfg = state["config"]
    assert slug in cfg.apps, f"Slug {slug!r} not in apps: {list(cfg.apps)}"


@then(parsers.parse('a ValueError is raised mentioning "{text}"'))
def then_value_error_raised(state: dict[str, Any], text: str) -> None:
    exc = state["raised"]
    assert isinstance(exc, ValueError), f"Expected ValueError, got {type(exc).__name__}: {exc}"
    assert text in str(exc), f"Expected {text!r} in error message: {exc}"


@then("a FileNotFoundError is raised")
def then_file_not_found(state: dict[str, Any]) -> None:
    exc = state["raised"]
    assert isinstance(exc, FileNotFoundError), (
        f"Expected FileNotFoundError, got {type(exc).__name__}: {exc}"
    )


@then(parsers.parse('the "{slug}" app private_key_path does not start with "~"'))
def then_tilde_expanded(state: dict[str, Any], slug: str) -> None:
    cfg = state["config"]
    app = cfg.apps[slug]
    assert not str(app.private_key_path).startswith("~"), (
        f"private_key_path still has tilde: {app.private_key_path}"
    )


@then(parsers.parse('the "{slug}" app webhook_secret_env is "{expected}"'))
def then_webhook_secret_env(state: dict[str, Any], slug: str, expected: str) -> None:
    cfg = state["config"]
    app = cfg.apps[slug]
    assert app.webhook_secret_env == expected, (
        f"webhook_secret_env = {app.webhook_secret_env!r}, expected {expected!r}"
    )


@then(parsers.parse('the "{slug}" app installations has key "{key}" with value "{value}"'))
def then_installations_key(state: dict[str, Any], slug: str, key: str, value: str) -> None:
    cfg = state["config"]
    app = cfg.apps[slug]
    assert key in app.installations, f"Key {key!r} not in installations: {list(app.installations)}"
    assert app.installations[key] == value, (
        f"installations[{key!r}] = {app.installations[key]!r}, expected {value!r}"
    )
