"""Regression test for the venv-swap command documented in VOY-1814 / CHG-1820.

CHG-1820 D6 specifies the atomic venv-swap as:

    ln -s <target> ~/.voyager/.venv.swap-$$
    mv -hf ~/.voyager/.venv.swap-$$ ~/.voyager/.venv

The `-h` flag (BSD/macOS) is load-bearing: without it, when `.venv` already
points to a directory, `mv -f` follows the target symlink and moves the
intermediate file INTO the existing venv directory instead of replacing
the `.venv` symlink itself. Result: active venv silently does not change,
operator sees a successful exit code, rollback is broken.

This test reproduces both behaviors in a temp directory:
  - `mv -f` (without -h): the swap FAILS to update `readlink active`.
  - `mv -hf`: the swap SUCCEEDS — `readlink active` points at the new target.

If a future maintainer changes the documented command back to `mv -f`,
this test fires and blocks the change at PR time.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

import pytest

# Only the two OS-level repro tests are macOS-specific (the `mv -h` flag
# is BSD/macOS-only). The doc-grep guard `test_voy_1814_and_chg_1820_use_mv_hf_not_plain_mv_f`
# is pure text matching and MUST run on every platform — it is the
# regression check that blocks `mv -f` from reappearing in the SOP/CHG
# docs, so disabling it on Linux CI would silently weaken protection.
# Codex P2 finding on PR #80 caught the prior module-level skipif.
darwin_only = pytest.mark.skipif(
    platform.system() != "Darwin",
    reason="venv-swap OS repro is macOS-specific (mv -h flag); see VOY-1814",
)


def _make_layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    old_venv = tmp_path / "old-venv"
    new_venv = tmp_path / "new-venv"
    active = tmp_path / "active"
    old_venv.mkdir()
    new_venv.mkdir()
    active.symlink_to("old-venv")
    return old_venv, new_venv, active


@darwin_only
def test_mv_f_alone_silently_fails_to_swap_symlink_on_macos(tmp_path: Path) -> None:
    """Documents the broken pattern. Asserts the bug exists so the fix is non-trivial."""
    _old_venv, _new_venv, active = _make_layout(tmp_path)
    swap = tmp_path / "active.swap"
    swap.symlink_to("new-venv")

    subprocess.run(["mv", "-f", str(swap), str(active)], check=True, cwd=tmp_path)

    # Bug reproduction: the active symlink STILL points at old-venv. The
    # intermediate symlink got consumed by the move (it's gone from the
    # parent dir) — but the move landed it inside the venv directory the
    # symlink pointed at, not in place of the symlink itself.
    assert active.is_symlink()
    assert active.readlink().name == "old-venv", (
        "macOS bug not reproducing — if mv -f now correctly swaps symlinks, "
        "the documented mv -hf workaround may be redundant and this test "
        "should be re-examined."
    )
    # The intermediate is gone from the parent — it followed the symlink
    # and was deposited inside the target directory.
    assert not swap.exists()


@darwin_only
def test_mv_hf_swaps_symlink_atomically(tmp_path: Path) -> None:
    """Verifies the documented CHG-1820 D6 command actually swaps the symlink."""
    old_venv, new_venv, active = _make_layout(tmp_path)
    swap = tmp_path / "active.swap"
    swap.symlink_to("new-venv")

    subprocess.run(["mv", "-hf", str(swap), str(active)], check=True, cwd=tmp_path)

    assert active.is_symlink()
    assert active.readlink().name == "new-venv", (
        "mv -hf did not swap the symlink; verify VOY-1814 / CHG-1820 D6 "
        "still document `mv -hf` (NOT plain `mv -f`)."
    )
    # The intermediate must be consumed by the move — not lurking inside
    # either venv directory.
    assert not (old_venv / "active.swap").exists()
    assert not (new_venv / "active.swap").exists()
    assert not swap.exists()


def test_voy_1814_and_chg_1820_use_mv_hf_not_plain_mv_f() -> None:
    """Doc-level guard: every `mv` line on a `.venv.swap-` intermediate must use `-h`."""
    import re

    project_root = Path(__file__).resolve().parent.parent.parent
    sop = project_root / "rules/VOY-1814-SOP-Wukong-Bridge-Launchd-and-Rollback.md"
    chg = project_root / "rules/VOY-1820-CHG-Bridge-Deployable-Wheel-Artifact.md"

    # Match `mv` followed by a flag bundle that does NOT contain `h`.
    # Covers `mv -f`, `mv -fn`, etc. — but allows `mv -hf`, `mv -fh`, `mv -h ...`.
    mv_without_h = re.compile(r"\bmv\s+-[a-gi-zA-GI-Z]+\b")
    # Lines that mention `mv -f` only in backtick-prose (Markdown inline code,
    # e.g. "DO NOT use `mv -f`") are documentation about the broken pattern,
    # not actual commands. Strip those occurrences before checking.
    backtick_mv = re.compile(r"`[^`]*\bmv +-[a-gi-zA-GI-Z]+[^`]*`")

    for path in (sop, chg):
        text = path.read_text()
        offenders = []
        for line in text.splitlines():
            if ".venv.swap-" not in line:
                continue
            stripped = backtick_mv.sub("[BACKTICK_MV_REFERENCE]", line)
            if mv_without_h.search(stripped):
                offenders.append(line)
        assert not offenders, (
            f"{path.name} contains a `mv` flag bundle without `-h` on a "
            f"`.venv.swap-` intermediate (outside of backtick-prose) — "
            f"CHG-1820 D6 requires `mv -hf` (the `-h` flag is load-bearing "
            f"on macOS):\n  " + "\n  ".join(offenders)
        )
