from __future__ import annotations

from voyager.core.config import load_config


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
