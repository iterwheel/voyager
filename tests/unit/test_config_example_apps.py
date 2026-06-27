from __future__ import annotations

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
