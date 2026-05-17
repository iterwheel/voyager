from __future__ import annotations

import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLIST_PATH = ROOT / "deploy/launchd/com.iterwheel.voyager.bridge.plist"
ENV_PATH = ROOT / "deploy/wukong/bridge.env.example"
SOP_PATH = ROOT / "rules/VOY-1814-SOP-Wukong-Bridge-Launchd-and-Rollback.md"


def test_launchd_plist_defines_wukong_bridge_contract() -> None:
    plist = plistlib.loads(PLIST_PATH.read_bytes())

    assert plist["Label"] == "com.iterwheel.voyager.bridge"
    assert plist["WorkingDirectory"] == "/Users/frank/Projects/voyager"
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert plist["StandardOutPath"] == "/Users/frank/Library/Logs/voyager/bridge.out.log"
    assert plist["StandardErrorPath"] == "/Users/frank/Library/Logs/voyager/bridge.err.log"

    args = plist["ProgramArguments"]
    assert args[:2] == ["/bin/zsh", "-lc"]
    command = args[2]
    assert "source /Users/frank/.voyager/bridge.env" in command
    assert "exec /Users/frank/Projects/voyager/.venv/bin/python" in command
    assert "-m uvicorn voyager.server:app --host 127.0.0.1 --port 8787" in command


def test_wukong_env_example_preserves_production_safety_contract() -> None:
    text = ENV_PATH.read_text()

    assert "DRY_RUN=false" in text
    assert "# BRIDGE_ALLOWED_REPOSITORIES=" in text
    assert (
        "BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_BLUEPRINT="
        "frankyxhl/alfred,frankyxhl/trinity,iterwheel/voyager"
    ) in text
    assert (
        "BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_STACK="
        "frankyxhl/alfred,frankyxhl/trinity,iterwheel/voyager"
    ) in text
    assert "BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CLEARANCE=iterwheel/voyager" in text

    forbidden_secret_markers = ("ghp_", "gho_", "github_pat_", "-----BEGIN")
    for marker in forbidden_secret_markers:
        assert marker not in text


def test_launchd_sop_covers_operator_lifecycle_and_rollback() -> None:
    text = SOP_PATH.read_text()

    required_snippets = (
        "/Users/frank/.voyager/bridge.env",
        "chmod 600 /Users/frank/.voyager/bridge.env",
        "DRY_RUN=false",
        "launchctl bootstrap gui/$(id -u)",
        "launchctl bootout gui/$(id -u)",
        "launchctl kickstart -kp gui/$(id -u)/com.iterwheel.voyager.bridge",
        "launchctl print gui/$(id -u)/com.iterwheel.voyager.bridge",
        "tail -n 100 -F /Users/frank/Library/Logs/voyager/bridge.err.log",
        "curl -fsS http://127.0.0.1:8787/healthz",
        "curl -fsS https://gh.iterwheel.com/healthz",
        'git switch --detach "${PREVIOUS_TAG}"',
    )

    for snippet in required_snippets:
        assert snippet in text
