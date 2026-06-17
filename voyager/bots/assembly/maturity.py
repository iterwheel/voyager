"""Gate maturity levels for Assembly bot gates.

Each gate declares a maturity level that controls how findings affect
publication:

- ``L1`` — Advisory: findings are recorded but never block publish.
- ``L2`` — Assisted: findings are surfaced for operator review.
- ``L3`` — Unattended: findings block publish until resolved.

New gates default to ``L1`` so they earn their blocking power
progressively instead of starting unattended.
"""

from __future__ import annotations

from enum import Enum


class GateMaturity(Enum):
    """Maturity level for an Assembly gate."""

    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


DEFAULT_GATE_MATURITY = GateMaturity.L1
"""Maturity assigned to gates that do not explicitly set one."""
