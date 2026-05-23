"""Wheel-integration smoke test (CHG-1820 Surface 14).

Test contract — asserts two invariants about the wheel built by
``scripts/build_wheel.sh``:

1. The wheel contains ``voyager/_build_info.py`` (regression gate for
   hatchling's gitignore-exclusion behavior).
2. That file's ``BUILD_COMMIT`` equals the current ``git rev-parse HEAD``
   (regression gate for the build script's SHA-injection step).

Marked ``slow`` (deselected by default in the fast unit loop) and skipped
when ``uv`` is unavailable.

Protocol: record the build-start time; build via ``bash scripts/build_wheel.sh``;
locate the wheel via glob + mtime (robust to ``uv build`` overwriting same-name
artifacts on repeat runs); verify with ``zipfile.ZipFile``; cleanup the
freshly-built artifacts so ``dist/`` returns to its pre-test state.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.skipif(not shutil.which("uv"), reason="uv required for wheel build")


@pytest.mark.slow
def test_built_wheel_contains_build_info_and_reports_commit() -> None:
    dist_dir = PROJECT_ROOT / "dist"
    build_script = PROJECT_ROOT / "scripts" / "build_wheel.sh"

    # Record a build-start anchor so we can identify just-built artifacts even
    # when uv-build overwrites same-name wheels from prior runs (the set-diff
    # snapshot pattern misses that case).
    build_start = time.time() - 1  # 1-second buffer for clock skew
    # The build script's dirty-tree gate is operator-facing; in a pytest run
    # other tests may leave untracked files (caches, dist/ leftovers) that
    # `git status --porcelain` reports. Override the gate here — the test
    # explicitly controls the input state and re-asserts wheel content below.
    env = {**os.environ, "VOYAGER_BUILD_ALLOW_DIRTY": "1"}
    subprocess.run(
        ["bash", str(build_script)],
        cwd=PROJECT_ROOT,
        check=True,
        env=env,
    )

    fresh_wheels = [
        p for p in dist_dir.glob("iterwheel_voyager-*.whl") if p.stat().st_mtime >= build_start
    ]
    assert len(fresh_wheels) == 1, f"expected exactly one fresh wheel, got {fresh_wheels}"
    new_wheel_path = fresh_wheels[0]
    fresh_artifacts = [
        p for p in dist_dir.iterdir() if p.is_file() and p.stat().st_mtime >= build_start
    ]
    try:
        current_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        with zipfile.ZipFile(new_wheel_path) as zf:
            names = zf.namelist()
            assert "voyager/_build_info.py" in names, (
                f"_build_info.py missing from wheel; namelist: {names}"
            )
            content = zf.read("voyager/_build_info.py")
            expected = f'BUILD_COMMIT = "{current_sha}"\n'.encode()
            assert content == expected, (
                f"build_info content mismatch:\n  got: {content!r}\n  want: {expected!r}"
            )
    finally:
        for artifact in fresh_artifacts:
            artifact.unlink(missing_ok=True)
