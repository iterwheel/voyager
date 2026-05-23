"""Independent cross-test for the ``vyg`` CLI smoke surface (CHG-1820 Surface 13).

Uses subprocess + ``python -m voyager.cli`` rather than the installed ``vyg``
script so it works in editable installs. Per VOY-1817 Phase 6 conventions.
"""

from __future__ import annotations

import subprocess
import sys


def test_xtest_python_m_voyager_cli_help_succeeds() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "voyager.cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "bridge" in result.stdout


def test_xtest_python_m_voyager_cli_version_succeeds() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "voyager.cli", "version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "version:" in result.stdout
    assert "build_commit:" in result.stdout
