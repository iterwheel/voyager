from __future__ import annotations

import logging

from voyager.bots.clearance.investigator import (
    _model_policy_tier,
    _profile_policy_warning,
    build_investigator_from_env,
    build_investigator_from_profile,
)
from voyager.core.config import Profile, load_config


def _profile(
    *,
    name: str,
    model: str,
    thinking: bool = True,
    min_confidence: float,
) -> Profile:
    return Profile(
        name=name,
        model=model,
        thinking=thinking,
        reasoning_effort=None,
        max_diff_chars=12000,
        min_confidence=min_confidence,
    )


def test_config_example_preserves_flash_canary_default_with_separate_thresholds() -> None:
    cfg = load_config("config.example.toml")

    assert cfg.default_profile == "flash_no_thinking"
    assert cfg.profiles["flash"].min_confidence == 0.90
    assert cfg.profiles["flash_no_thinking"].min_confidence == 0.90
    assert cfg.profiles["pro"].min_confidence == 0.78
    assert cfg.profiles["pro_max"].min_confidence == 0.85
    assert cfg.profiles["pro_max"].reasoning_effort == "max"


def test_flash_profile_policy_warning_is_actionable() -> None:
    warning = _profile_policy_warning(
        profile_name="flash_no_thinking",
        model="deepseek-v4-flash",
        min_confidence=0.90,
    )

    assert warning is not None
    assert "Flash-tier" in warning
    assert "current canary/advisory" in warning
    assert "[profiles.pro]" in warning
    assert "min_confidence >= 0.90" in warning


def test_flash_profile_below_policy_floor_names_threshold_action() -> None:
    warning = _profile_policy_warning(
        profile_name="flash",
        model="deepseek-v4-flash",
        min_confidence=0.85,
    )

    assert warning is not None
    assert "raise min_confidence to at least 0.90" in warning


def test_pro_profile_policy_warning_when_threshold_below_floor() -> None:
    warning = _profile_policy_warning(
        profile_name="pro_low",
        model="deepseek-v4-pro",
        min_confidence=0.60,
    )

    assert warning is not None
    assert "below the recommended 0.78" in warning
    assert "Raise min_confidence" in warning


def test_pro_profile_at_policy_floor_has_no_warning() -> None:
    assert (
        _profile_policy_warning(
            profile_name="pro",
            model="deepseek-v4-pro",
            min_confidence=0.78,
        )
        is None
    )


def test_unknown_model_policy_warning_requires_documented_tier() -> None:
    warning = _profile_policy_warning(
        profile_name="experimental",
        model="deepseek-v5-preview",
        min_confidence=0.80,
    )

    assert warning is not None
    assert "unrecognized model" in warning
    assert "Known Pro models" in warning
    assert "document the model tier" in warning


def test_model_policy_tier_classifies_known_models() -> None:
    assert _model_policy_tier("deepseek-v4-pro") == "pro"
    assert _model_policy_tier("deepseek-reasoner") == "pro"
    assert _model_policy_tier("deepseek-v4-flash") == "flash"
    assert _model_policy_tier("deepseek-chat") == "unknown"
    assert _model_policy_tier("deepseek-v5-preview") == "unknown"


def test_moving_deepseek_chat_alias_requires_documented_tier() -> None:
    warning = _profile_policy_warning(
        profile_name="legacy_chat",
        model="deepseek-chat",
        min_confidence=0.80,
    )

    assert warning is not None
    assert "unrecognized model" in warning
    assert "document the model tier" in warning


def test_build_investigator_from_profile_logs_flash_policy_warning(caplog) -> None:
    profile = _profile(
        name="flash_no_thinking",
        model="deepseek-v4-flash",
        thinking=False,
        min_confidence=0.90,
    )

    with caplog.at_level(logging.WARNING):
        investigator = build_investigator_from_profile(profile, api_key="test-key")

    assert investigator.min_confidence == 0.90
    assert investigator._thinking is False
    assert any("Flash-tier" in record.message for record in caplog.records)


def test_build_investigator_from_env_logs_unknown_model_policy_warning(monkeypatch, caplog) -> None:
    monkeypatch.setenv("VOYAGER_INVESTIGATOR_ENABLED", "1")
    monkeypatch.setenv("VOYAGER_DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("VOYAGER_INVESTIGATOR_MODEL", "deepseek-v5-preview")
    monkeypatch.setenv("VOYAGER_INVESTIGATOR_MIN_CONFIDENCE", "0.80")

    with caplog.at_level(logging.WARNING):
        investigator = build_investigator_from_env()

    assert investigator is not None
    assert investigator._client._model == "deepseek-v5-preview"
    assert any("unrecognized model" in record.message for record in caplog.records)


def test_build_investigator_from_env_logs_flash_canary_policy_warning(monkeypatch, caplog) -> None:
    monkeypatch.setenv("VOYAGER_INVESTIGATOR_ENABLED", "1")
    monkeypatch.setenv("VOYAGER_DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("VOYAGER_INVESTIGATOR_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("VOYAGER_INVESTIGATOR_MIN_CONFIDENCE", "0.90")

    with caplog.at_level(logging.WARNING):
        investigator = build_investigator_from_env()

    assert investigator is not None
    assert investigator._client._model == "deepseek-v4-flash"
    assert any("current canary/advisory" in record.message for record in caplog.records)
