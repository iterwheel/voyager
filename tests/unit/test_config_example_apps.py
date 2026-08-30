from __future__ import annotations

import tomllib
from pathlib import Path

from voyager.core.config import load_config
from voyager.governance.enablement import Autonomy


def test_config_example_registers_assembly_app_with_selected_installations() -> None:
    cfg = load_config("config.example.toml")

    assembly = cfg.apps["iterwheel-assembly"]
    assert assembly.app_id == "3821103"
    assert str(assembly.private_key_path).endswith("/.voyager/secrets/iterwheel-assembly.pem")
    assert assembly.installation_id == ""
    assert assembly.installations == {
        "iterwheel": "134829044",
        "frankyxhl": "134830000",
    }


def test_config_example_registers_countdown_resolver_canary_app() -> None:
    cfg = load_config("config.example.toml")

    countdown = cfg.apps["iterwheel-countdown"]
    assert countdown.app_id == "3646540"
    assert str(countdown.private_key_path).endswith("/.voyager/secrets/iterwheel-countdown.pem")
    assert countdown.installation_id == ""
    assert countdown.installations == {
        "iterwheel/voyager-sandbox": "130630407",
    }


def test_config_example_records_review_fix_l3_enablement() -> None:
    cfg = load_config("config.example.toml")

    assert cfg.review_fix.enablement is not None
    assert cfg.review_fix.enablement.autonomy is Autonomy.L3
    assert cfg.review_fix.enablement.envelope is not None
    assert cfg.review_fix.enablement.envelope.max_rounds == 3
    assert cfg.review_fix.enablement.envelope.max_fixes_per_round == 2
    assert str(cfg.review_fix.audit_dir).endswith("/.voyager/state/review-fix/audit")


def test_config_example_keeps_author_wakeup_and_fallback_default_off() -> None:
    raw = tomllib.loads(Path("config.example.toml").read_text(encoding="utf-8"))
    cfg = load_config("config.example.toml")

    section = raw["clearance"]["author_wakeup"]
    assert section["enabled"] is False
    assert section["auto_review_fix"] is False
    assert section["allowed_repositories"] == []
    assert cfg.author_wakeup.enabled is False
    assert cfg.author_wakeup.auto_review_fix is False
    assert cfg.author_wakeup.allowed_repositories == ()
    assert cfg.author_wakeup.pfc_door_url == "http://localhost:8420/api/agent-send"
    assert cfg.author_wakeup.required_send_id_retention_seconds == 86400
