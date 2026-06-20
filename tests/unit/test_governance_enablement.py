from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from voyager.governance.enablement import (
    Autonomy,
    EnablementConfigError,
    parse_enablement_config,
)


def _l3_block() -> dict[str, object]:
    return {
        "Autonomy": "L3",
        "envelope": {
            "max_rounds": 3,
            "max_fixes_per_round": 2,
            "kill_switch_path": ".voyager/review-fix.disabled",
            "escalation": "request-human-review",
            "verify_command": "pytest tests/unit/test_governance_enablement.py",
        },
    }


def test_valid_l3_enablement_exposes_full_envelope() -> None:
    enablement = parse_enablement_config(_l3_block(), section_name="[review_fix]")

    assert enablement.autonomy is Autonomy.L3
    assert enablement.envelope is not None
    assert enablement.envelope.max_rounds == 3
    assert enablement.envelope.max_fixes_per_round == 2
    assert enablement.envelope.kill_switch_path == Path(".voyager/review-fix.disabled")
    assert enablement.envelope.escalation == "request-human-review"
    assert enablement.envelope.verify_command == "pytest tests/unit/test_governance_enablement.py"


def test_l3_enablement_parses_from_toml_block() -> None:
    raw = tomllib.loads(
        """
[review_fix]
Autonomy = "L3"

[review_fix.envelope]
max_rounds = 3
max_fixes_per_round = 2
kill_switch_path = ".voyager/review-fix.disabled"
escalation = "request-human-review"
verify_command = "pytest tests/unit/test_governance_enablement.py"
"""
    )

    enablement = parse_enablement_config(raw["review_fix"], section_name="[review_fix]")

    assert enablement.autonomy is Autonomy.L3
    assert enablement.envelope is not None
    assert enablement.envelope.verify_command == "pytest tests/unit/test_governance_enablement.py"


@pytest.mark.parametrize(
    "missing_field",
    [
        "max_rounds",
        "max_fixes_per_round",
        "kill_switch_path",
        "escalation",
        "verify_command",
    ],
)
def test_l3_enablement_requires_every_envelope_field(missing_field: str) -> None:
    block = _l3_block()
    envelope = dict(block["envelope"])
    del envelope[missing_field]
    block["envelope"] = envelope

    with pytest.raises(EnablementConfigError, match=missing_field):
        parse_enablement_config(block, section_name="[review_fix]")


def test_l3_enablement_requires_envelope_table() -> None:
    with pytest.raises(EnablementConfigError, match=r"\[review_fix\]\.envelope"):
        parse_enablement_config({"Autonomy": "L3"}, section_name="[review_fix]")


@pytest.mark.parametrize("autonomy", [Autonomy.L1, Autonomy.L2])
def test_l1_l2_enablement_does_not_require_envelope(autonomy: Autonomy) -> None:
    enablement = parse_enablement_config(
        {"autonomy": autonomy.value},
        section_name="[review_fix]",
    )

    assert enablement.autonomy is autonomy
    assert enablement.envelope is None
