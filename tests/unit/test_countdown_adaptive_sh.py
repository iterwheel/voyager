"""Functional harness for the adaptive scheduler's trigger helpers (CHG-1841 D4).

Extracts the pure helper functions (trigger_path / consume_trigger /
trigger_newer_than / sliced_sleep) out of
deploy/wukong/countdown-resolve-loop-adaptive.sh — stopping before the
`while true` daemon loop, so sourcing the fragment never blocks and no `vyg`
stub is needed — and exercises them in a zsh subprocess. `sleep` is replaced
with a no-op so the whole suite runs without any real waiting; all timing is
driven deterministically via `touch -t` mtimes instead of wall-clock delays.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).parent.parent.parent / "deploy" / "wukong" / "countdown-resolve-loop-adaptive.sh"
)

pytestmark = pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is required")


def _helper_functions() -> str:
    text = SCRIPT.read_text()
    marker = "\nwhile true; do"
    return text[: text.index(marker)]


def _run(driver: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "harness.sh"
    script.write_text(
        "#!/bin/zsh\nset -u\nsleep() { :; }  # no-op: no real waiting in tests\n\n"
        f"{_helper_functions()}\n\n{driver}\n"
    )
    script.chmod(0o755)
    return subprocess.run(
        ["zsh", str(script)],
        capture_output=True,
        text=True,
        env={**os.environ, "COUNTDOWN_TRIGGER_PATH": str(tmp_path / "trigger")},
        timeout=10,
    )


def _mtime_touch(epoch: int) -> str:
    """Shell snippet: create the trigger file with mtime pinned to `epoch`."""
    return f'touch -t "$(date -r {epoch} +%Y%m%d%H%M.%S)" "$COUNTDOWN_TRIGGER_PATH"'


def test_full_sleep_completes_when_idle(tmp_path: Path) -> None:
    result = _run(
        'run_start=$(date +%s)\nsliced_sleep 90 "$run_start"\necho DONE',
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "trigger detected" not in result.stdout
    assert "DONE" in result.stdout


def test_mid_sleep_trigger_wakes_early(tmp_path: Path) -> None:
    driver = f"""\
run_start=$(date +%s)
{_mtime_touch("$((run_start + 5))")}
sliced_sleep 90 "$run_start"
echo DONE
"""
    result = _run(driver, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "trigger detected" in result.stdout
    assert "DONE" in result.stdout


def test_same_second_trigger_wakes(tmp_path: Path) -> None:
    """Regression for major finding 1: BSD `stat -f %m` is second-resolution,
    so a trigger touched in the same second as run_start has mtime == since.
    `trigger_newer_than` must treat that as "newer" (>=), not miss it (>)."""
    driver = f"""\
run_start=$(date +%s)
{_mtime_touch("$run_start")}
sliced_sleep 90 "$run_start"
echo DONE
"""
    result = _run(driver, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "trigger detected" in result.stdout


def test_stale_pre_run_start_trigger_ignored(tmp_path: Path) -> None:
    driver = f"""\
run_start=$(date +%s)
{_mtime_touch("$((run_start - 10))")}
sliced_sleep 60 "$run_start"
echo DONE
"""
    result = _run(driver, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "trigger detected" not in result.stdout
    assert "DONE" in result.stdout


def test_consume_trigger_deletes_file(tmp_path: Path) -> None:
    driver = (
        'touch "$COUNTDOWN_TRIGGER_PATH"\n'
        "consume_trigger\n"
        '[[ -f "$COUNTDOWN_TRIGGER_PATH" ]] && echo STILL_EXISTS || echo GONE'
    )
    result = _run(driver, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "GONE" in result.stdout
