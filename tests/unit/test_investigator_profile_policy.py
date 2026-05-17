from __future__ import annotations

import logging

from voyager.bots.clearance.investigator import (
    _profile_policy_warning,
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
