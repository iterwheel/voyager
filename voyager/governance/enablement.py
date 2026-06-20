"""Recorded enablement schema for governed automation loops."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final


class EnablementConfigError(ValueError):
    """Raised when a recorded enablement config block is invalid."""


class Autonomy(StrEnum):
    """Supported automation autonomy levels."""

    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


@dataclass(frozen=True, kw_only=True)
class SafetyEnvelope:
    """Safety envelope required before unattended L3 automation may run."""

    max_rounds: int
    max_fixes_per_round: int
    kill_switch_path: Path
    escalation: str
    verify_command: str


@dataclass(frozen=True, kw_only=True)
class EnablementConfig:
    """Typed recorded enablement for one governed automation loop."""

    autonomy: Autonomy
    envelope: SafetyEnvelope | None = None


_AUTONOMY_KEYS: Final[tuple[str, ...]] = ("autonomy", "Autonomy")
_ENVELOPE_FIELDS: Final[tuple[str, ...]] = (
    "max_rounds",
    "max_fixes_per_round",
    "kill_switch_path",
    "escalation",
    "verify_command",
)


def parse_enablement_config(
    block: Mapping[str, Any],
    *,
    section_name: str = "[enablement]",
) -> EnablementConfig:
    """Parse one TOML/config block into a typed enablement object."""

    if not isinstance(block, Mapping):
        raise EnablementConfigError(
            f"{section_name} must be a TOML table, got {type(block).__name__}: {block!r}"
        )

    autonomy = _parse_autonomy(_autonomy_value(block, section_name), section_name)
    envelope_block = block.get("envelope")

    if autonomy is Autonomy.L3:
        if envelope_block is None:
            raise EnablementConfigError(
                f"{section_name}.envelope is required for autonomy {autonomy.value}"
            )
        envelope = _parse_envelope(
            envelope_block,
            f"{section_name}.envelope",
            autonomy=autonomy,
        )
    elif envelope_block is None:
        envelope = None
    else:
        envelope = _parse_envelope(
            envelope_block,
            f"{section_name}.envelope",
            autonomy=autonomy,
        )

    return EnablementConfig(autonomy=autonomy, envelope=envelope)


def _autonomy_value(block: Mapping[str, Any], section_name: str) -> Any:
    present = [key for key in _AUTONOMY_KEYS if key in block]
    if not present:
        raise EnablementConfigError(
            f"{section_name}.autonomy is required and must be one of "
            f"{[level.value for level in Autonomy]!r}"
        )
    if len(present) > 1:
        raise EnablementConfigError(f"{section_name} must not set both 'autonomy' and 'Autonomy'")
    return block[present[0]]


def _parse_autonomy(value: Any, section_name: str) -> Autonomy:
    if not isinstance(value, str):
        raise EnablementConfigError(
            f"{section_name}.autonomy must be a TOML string, got {type(value).__name__}: {value!r}"
        )
    normalized = value.strip()
    try:
        return Autonomy(normalized)
    except ValueError as exc:
        raise EnablementConfigError(
            f"{section_name}.autonomy must be one of {[level.value for level in Autonomy]!r}, "
            f"got {value!r}"
        ) from exc


def _parse_envelope(
    value: Any,
    section_name: str,
    *,
    autonomy: Autonomy,
) -> SafetyEnvelope:
    if not isinstance(value, Mapping):
        raise EnablementConfigError(
            f"{section_name} must be a TOML table, got {type(value).__name__}: {value!r}"
        )
    missing = [field for field in _ENVELOPE_FIELDS if field not in value]
    if missing:
        fields = ", ".join(missing)
        raise EnablementConfigError(
            f"{section_name} missing required field(s) for autonomy {autonomy.value}: {fields}"
        )

    return SafetyEnvelope(
        max_rounds=_positive_int(value, "max_rounds", section_name),
        max_fixes_per_round=_positive_int(value, "max_fixes_per_round", section_name),
        kill_switch_path=_path(value, "kill_switch_path", section_name),
        escalation=_non_empty_string(value, "escalation", section_name),
        verify_command=_non_empty_string(value, "verify_command", section_name),
    )


def _positive_int(block: Mapping[str, Any], key: str, section_name: str) -> int:
    value = block[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise EnablementConfigError(
            f"{section_name}.{key} must be a TOML integer, got {type(value).__name__}: {value!r}"
        )
    if value < 1:
        raise EnablementConfigError(f"{section_name}.{key} must be >= 1, got {value!r}")
    return value


def _non_empty_string(block: Mapping[str, Any], key: str, section_name: str) -> str:
    value = block[key]
    if not isinstance(value, str):
        raise EnablementConfigError(
            f"{section_name}.{key} must be a TOML string, got {type(value).__name__}: {value!r}"
        )
    normalized = value.strip()
    if not normalized:
        raise EnablementConfigError(f"{section_name}.{key} must be a non-empty string")
    return normalized


def _path(block: Mapping[str, Any], key: str, section_name: str) -> Path:
    return Path(_non_empty_string(block, key, section_name)).expanduser()
