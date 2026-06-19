from __future__ import annotations

import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLIST_PATH = ROOT / "deploy/launchd/com.iterwheel.voyager.bridge.plist"
ENV_PATH = ROOT / "deploy/wukong/bridge.env.example"
SOP_PATH = ROOT / "rules/VOY-1814-SOP-Wukong-Bridge-Launchd-and-Rollback.md"
WUKONG_PROJECT_DIR = "/Users/frank/Projects/voyager"
WUKONG_LOG_DIR = "/Users/frank/Library/Logs/voyager"


def test_launchd_plist_defines_wukong_bridge_contract() -> None:
    plist = plistlib.loads(PLIST_PATH.read_bytes())

    assert plist["Label"] == "com.iterwheel.voyager.bridge"
    # CHG-1820 D9: WorkingDirectory removed — installed CLI uses absolute paths only.
    assert "WorkingDirectory" not in plist
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert plist["ThrottleInterval"] == 10
    assert plist["Umask"] == 63
    assert plist["StandardOutPath"] == f"{WUKONG_LOG_DIR}/bridge.out.log"
    assert plist["StandardErrorPath"] == f"{WUKONG_LOG_DIR}/bridge.err.log"

    args = plist["ProgramArguments"]
    assert len(args) == 3
    assert args[:2] == ["/bin/zsh", "-lc"]
    command = args[2]
    assert "set -a && source /Users/frank/.voyager/bridge.env && set +a && exec" in command
    assert "; exec" not in command
    # CHG-1820 Surface 6: exec the installed vyg CLI from ~/.voyager/.venv, not
    # the source-checkout python+uvicorn pair.
    assert "exec /Users/frank/.voyager/.venv/bin/vyg" in command
    assert "bridge serve --host 127.0.0.1 --port 8787" in command


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
    assert "BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CHANGELOG=iterwheel/voyager" in text
    assert "BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CLEANUP=iterwheel/voyager" in text
    assert "BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CI_FAILING=iterwheel/voyager" in text
    assert "BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_ASSEMBLY=" not in text
    assert "GITHUB_REPOSITORY_WEBHOOK_SECRET=replace-with-repository-webhook-secret" in text
    assert "GITHUB_WEBHOOK_SECRET=replace-with-repository-webhook-secret" not in text

    forbidden_secret_markers = ("ghp_", "gho_", "github_pat_", "-----BEGIN")
    for marker in forbidden_secret_markers:
        assert marker not in text


def test_wukong_env_example_enables_deployed_version_drift_schedule() -> None:
    text = ENV_PATH.read_text()

    assert "BRIDGE_DRIFT_ALERT_ENABLED=true" in text
    assert "BRIDGE_DRIFT_ALERT_REPOSITORY=iterwheel/voyager" in text
    assert "BRIDGE_DRIFT_ALERT_BRIDGE_URL=https://gh.iterwheel.com" in text
    assert "BRIDGE_DRIFT_ALERT_INTERVAL_SECONDS=3600" in text
    assert "BRIDGE_DRIFT_ALERT_APP_SLUG=iterwheel-assembly" in text


def test_wukong_env_example_enables_ci_failing_schedule() -> None:
    text = ENV_PATH.read_text()

    assert "BRIDGE_CI_FAILING_ENABLED=true" in text
    assert "BRIDGE_CI_FAILING_REPOSITORY=iterwheel/voyager" in text
    assert "BRIDGE_CI_FAILING_INTERVAL_SECONDS=86400" in text
    assert "BRIDGE_CI_FAILING_APP_SLUG=iterwheel-assembly" in text
    assert "BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CI_FAILING=iterwheel/voyager" in text


def test_launchd_sop_covers_operator_lifecycle_and_rollback() -> None:
    text = SOP_PATH.read_text()

    required_snippets = (
        "/Users/frank/.voyager/bridge.env",
        "if [[ ! -f /Users/frank/.voyager/bridge.env ]]; then",
        "install -m 600 deploy/wukong/bridge.env.example /Users/frank/.voyager/bridge.env",
        "/Users/frank/.voyager/bridge.env.backup.$(date -u +%Y%m%dT%H%M%SZ)",
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
