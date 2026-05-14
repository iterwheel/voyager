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


@given(
    parsers.parse('VOYAGER_CONFIG_PATH is set to nonexistent path "{path}"'),
    target_fixture="state",
)
def given_env_nonexistent(path: str) -> dict[str, Any]:
    return {"config_path": None, "env_path": path, "config": None, "raised": None}


@given("VOYAGER_DEEPSEEK_API_KEY is not set in env", target_fixture="state")
def given_deepseek_env_unset(monkeypatch, state: dict[str, Any]) -> dict[str, Any]:
    monkeypatch.delenv("VOYAGER_DEEPSEEK_API_KEY", raising=False)
    return state


@given(
    parsers.parse('VOYAGER_DEEPSEEK_API_KEY is set in env to "{value}"'),
    target_fixture="state",
)
def given_deepseek_env_set(monkeypatch, state: dict[str, Any], value: str) -> dict[str, Any]:
    monkeypatch.setenv("VOYAGER_DEEPSEEK_API_KEY", value)
    return state


@given(
    "VOYAGER_CONFIG_PATH is set to a tilde path resolving to a valid config",
    target_fixture="state",
)
def given_env_tilde_resolves_to_valid(tmp_path, monkeypatch) -> dict[str, Any]:
    """Codex round 3 P2: tilde in VOYAGER_CONFIG_PATH must be expanded.

    Place a valid TOML config at tmp_path/.voyager/config.toml, then point HOME
    at tmp_path so ``~/.voyager/config.toml`` expands to that file. The env
    override should resolve and load it (instead of raising "file not found"
    on a literal ~).
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    target_dir = tmp_path / ".voyager"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / "config.toml"

    fixtures_dir = Path(__file__).parent.parent.parent / "fixtures" / "config"
    target_file.write_text((fixtures_dir / "valid_two_apps.toml").read_text())

    return {
        "config_path": None,
        "env_path": "~/.voyager/config.toml",
        "config": None,
        "raised": None,
    }


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


@when("the config load is attempted via the env override", target_fixture="state")
def when_load_config_attempt_env(state: dict[str, Any]) -> dict[str, Any]:
    from voyager.core.config import load_config  # lazy

    old = os.environ.get("VOYAGER_CONFIG_PATH")
    if state.get("env_path"):
        os.environ["VOYAGER_CONFIG_PATH"] = state["env_path"]
    try:
        try:
            state["config"] = load_config()
        except (ValueError, FileNotFoundError) as exc:
            state["raised"] = exc
    finally:
        if old is None:
            os.environ.pop("VOYAGER_CONFIG_PATH", None)
        else:
            os.environ["VOYAGER_CONFIG_PATH"] = old
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


@when(
    parsers.parse('the config is loaded then loaded again with "{filename}"'),
    target_fixture="state",
)
def when_load_config_twice(state: dict[str, Any], filename: str) -> dict[str, Any]:
    """Two-shot loader for the 'load twice — second wins, no env mutation' regression."""
    from voyager.core.config import load_config  # lazy

    state["config"] = load_config(state["config_path"])
    state["second_config"] = load_config(FIXTURES_DIR / filename)
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


@then(parsers.parse('the error message mentions "{text}"'))
def then_error_mentions(state: dict[str, Any], text: str) -> None:
    exc = state["raised"]
    assert exc is not None, "No exception was raised"
    assert text in str(exc), f"Expected {text!r} in error message: {exc}"


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


@then(parsers.parse("the profiles dict has {count:d} entries"))
def then_profiles_count(state: dict[str, Any], count: int) -> None:
    cfg = state["config"]
    assert cfg is not None, "Config was not loaded"
    assert len(cfg.profiles) == count, (
        f"Expected {count} profiles, got {len(cfg.profiles)}: {list(cfg.profiles)}"
    )


@then(parsers.parse('the profiles dict contains profile "{name}"'))
def then_profiles_has_name(state: dict[str, Any], name: str) -> None:
    cfg = state["config"]
    assert name in cfg.profiles, f"Profile {name!r} not in profiles: {list(cfg.profiles)}"


@then(parsers.parse('profile "{name}" has model "{model}"'))
def then_profile_model(state: dict[str, Any], name: str, model: str) -> None:
    cfg = state["config"]
    assert cfg.profiles[name].model == model, (
        f"profile {name!r} model = {cfg.profiles[name].model!r}, expected {model!r}"
    )


@then(parsers.parse('profile "{name}" has thinking true'))
def then_profile_thinking_true(state: dict[str, Any], name: str) -> None:
    cfg = state["config"]
    assert cfg.profiles[name].thinking is True, (
        f"profile {name!r} thinking = {cfg.profiles[name].thinking!r}, expected True"
    )


@then(parsers.parse('profile "{name}" has thinking false'))
def then_profile_thinking_false(state: dict[str, Any], name: str) -> None:
    cfg = state["config"]
    assert cfg.profiles[name].thinking is False, (
        f"profile {name!r} thinking = {cfg.profiles[name].thinking!r}, expected False"
    )


@then(parsers.parse('profile "{name}" has reasoning_effort None'))
def then_profile_reasoning_effort_none(state: dict[str, Any], name: str) -> None:
    cfg = state["config"]
    assert cfg.profiles[name].reasoning_effort is None, (
        f"profile {name!r} reasoning_effort = {cfg.profiles[name].reasoning_effort!r}, expected None"
    )


@then(parsers.parse('profile "{name}" has reasoning_effort "{effort}"'))
def then_profile_reasoning_effort(state: dict[str, Any], name: str, effort: str) -> None:
    cfg = state["config"]
    assert cfg.profiles[name].reasoning_effort == effort, (
        f"profile {name!r} reasoning_effort = {cfg.profiles[name].reasoning_effort!r}, expected {effort!r}"
    )


@then(parsers.parse('profile "{name}" has max_diff_chars {chars:d}'))
def then_profile_max_diff_chars(state: dict[str, Any], name: str, chars: int) -> None:
    cfg = state["config"]
    assert cfg.profiles[name].max_diff_chars == chars, (
        f"profile {name!r} max_diff_chars = {cfg.profiles[name].max_diff_chars!r}, expected {chars!r}"
    )


@then(parsers.parse('profile "{name}" has min_confidence {conf:f}'))
def then_profile_min_confidence(state: dict[str, Any], name: str, conf: float) -> None:
    cfg = state["config"]
    actual = cfg.profiles[name].min_confidence
    assert abs(actual - conf) < 1e-9, (
        f"profile {name!r} min_confidence = {actual!r}, expected {conf!r}"
    )


@then(parsers.parse('the default_profile is "{name}"'))
def then_default_profile(state: dict[str, Any], name: str) -> None:
    cfg = state["config"]
    assert cfg.default_profile == name, (
        f"default_profile = {cfg.default_profile!r}, expected {name!r}"
    )


@then("the default_profile is None")
def then_default_profile_none(state: dict[str, Any]) -> None:
    cfg = state["config"]
    assert cfg.default_profile is None, f"default_profile = {cfg.default_profile!r}, expected None"


@then(parsers.parse('the config.deepseek_api_key is "{value}"'))
def then_deepseek_api_key(state: dict[str, Any], value: str) -> None:
    cfg = state["config"]
    assert cfg.deepseek_api_key == value, (
        f"config.deepseek_api_key = {cfg.deepseek_api_key!r}, expected {value!r}"
    )


@then("the config.deepseek_api_key is None")
def then_deepseek_api_key_none(state: dict[str, Any]) -> None:
    cfg = state["config"]
    assert cfg.deepseek_api_key is None, (
        f"config.deepseek_api_key = {cfg.deepseek_api_key!r}, expected None"
    )


@then(parsers.parse('VOYAGER_DEEPSEEK_API_KEY env var equals "{value}"'))
def then_deepseek_env_equals(value: str) -> None:
    actual = os.environ.get("VOYAGER_DEEPSEEK_API_KEY")
    assert actual == value, f"VOYAGER_DEEPSEEK_API_KEY = {actual!r}, expected {value!r}"


@then("VOYAGER_DEEPSEEK_API_KEY env var is unset")
def then_deepseek_env_unset() -> None:
    actual = os.environ.get("VOYAGER_DEEPSEEK_API_KEY")
    assert actual is None, (
        f"VOYAGER_DEEPSEEK_API_KEY = {actual!r}, expected unset — "
        "load_config must not mutate os.environ (trinity round 0 P1)"
    )


@then("the second config.deepseek_api_key is None")
def then_second_deepseek_api_key_none(state: dict[str, Any]) -> None:
    cfg = state.get("second_config")
    assert cfg is not None, "second_config was not loaded — check the When step"
    assert cfg.deepseek_api_key is None, (
        f"second config.deepseek_api_key = {cfg.deepseek_api_key!r}, expected None"
    )
