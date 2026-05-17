from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOP = ROOT / "rules/VOY-1816-SOP-Managed-Repository-Canary-Expansion.md"


def _text() -> str:
    return SOP.read_text()


def test_canary_expansion_sop_names_candidate_order_and_exclusion() -> None:
    text = _text()

    assert "1. `frankyxhl/babs` if it is not already active" in text
    assert "2. `frankyxhl/screen-harness`" in text
    assert "`frankyxhl/sweeping-monk` is not a target" in text
    assert "Current verified canary set for issue #48, no expansion implied" in text
    assert "`frankyxhl/fx_bin`" in text


def test_canary_expansion_sop_requires_preflight_and_bot_scope() -> None:
    text = _text()

    required = (
        "Issue #45 / PR #49 writeback failure observability",
        "Issue #44 / PR #50 launchd and rollback runbook",
        "must merge before this SOP is merged or used",
        "reconcile this SOP with the live Wukong",
        "Inventory the live app-specific Wukong allow-list values",
        "selected-repository GitHub App installation access",
        "Confirm required labels exist",
        "https://gh.iterwheel.com/github/webhook",
        "Inspect default-branch protection",
        "| Blueprint | Yes |",
        "| Stack | Yes |",
        "| Clearance | Optional second phase |",
        "| Static Fire | No |",
        "| Countdown | No |",
    )
    for snippet in required:
        assert snippet in text


def test_canary_expansion_sop_covers_allowlist_rollback_and_denied_routes() -> None:
    text = _text()

    required = (
        "Do not expand beyond the current canary",
        "Leave `BRIDGE_ALLOWED_REPOSITORIES` unset",
        "/Users/frank/.voyager/bridge.env",
        "launchctl kickstart -kp gui/$(id -u)/com.iterwheel.voyager.bridge",
        "Remove the repository from each app-specific allow-list",
        "curl -fsS https://gh.iterwheel.com/healthz",
        "repository_allowlist_denied",
        "no bot marker comment is created or updated",
    )
    for snippet in required:
        assert snippet in text
