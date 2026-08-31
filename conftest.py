"""Root conftest: establish a persistent event loop for sync BDD step functions.

pytest-bdd step functions are synchronous and use asyncio.get_event_loop().run_until_complete().
In Python 3.12+, get_event_loop() raises RuntimeError when no loop is set in the current
thread. This fixture sets one for the duration of the session.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

# Git exports repo/quarantine state to hooks: during pre-push (git >= 2.11)
# GIT_QUARANTINE_PATH / GIT_OBJECT_DIRECTORY / GIT_ALTERNATE_OBJECT_DIRECTORIES
# describe the outer push's object quarantine, and GIT_DIR / GIT_WORK_TREE /
# GIT_INDEX_FILE point at THIS repository. The pre-push hook in
# .pre-commit-config.yaml runs the full pytest suite, so every test that
# spawns a `git` subprocess (governance verify-rollback fixtures, Assembly
# OMP LFS fixtures) inherits that state and operates on the wrong repository
# or against a nonexistent quarantine.
#
# Codex P2 (round 1): only scrub the object-store variables as part of a
# quarantine context — without GIT_QUARANTINE_PATH, GIT_OBJECT_DIRECTORY /
# GIT_ALTERNATE_OBJECT_DIRECTORIES can be an intentional configuration (e.g.
# a CI checkout with a relocated or shared object store) and must survive.
#
# Codex P2 (round 2, discussion_r3890533687): the repo pointers
# (GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE) are ALSO legitimate intentional
# configuration — a CI workspace with the git directory mounted elsewhere
# and NO local .git entry supplies the checkout location ONLY through them,
# and tests that invoke git against the real checkout (e.g.
# tests/test_release_readiness_ci.py) fail with "not a git repository" when
# they are dropped. Scrub each pointer only when it actually points at THIS
# outer repository (the repo containing conftest.py) — that distinguishes
# hook leakage (scrub) from intentional external pointers (keep).
_QUARANTINE_VARS = (
    "GIT_QUARANTINE_PATH",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)
_REPO_POINTER_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
)

_REPO_ROOT = Path(__file__).resolve().parent


def _outer_repo_git_dir() -> Path | None:
    """Absolute git dir of THIS checkout, discovered WITHOUT the pointer env.

    Returns None when git is unavailable or this checkout has no discoverable
    git dir (callers then fail toward the previous unconditional scrub).
    """
    env = {k: v for k, v in os.environ.items() if k not in _REPO_POINTER_VARS}
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--absolute-git-dir"],
            cwd=str(_REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    resolved = Path(out.stdout.strip())
    return resolved if str(resolved) else None


def _pointer_targets_this_repo(name: str, own_git_dir: Path | None) -> bool:
    """True when the pointer variable points at THIS outer repository."""
    raw = os.environ.get(name)
    if not raw:
        return False
    if own_git_dir is None:
        # Cannot identify the outer repo — fail toward the historically safe
        # behavior (scrub) rather than letting hook leakage through.
        return True
    path = Path(raw)
    if not path.is_absolute():
        # Git resolves relative GIT_DIR/GIT_WORK_TREE against the cwd; the
        # suite's cwd is the repository root.
        path = _REPO_ROOT / path
    try:
        resolved = path.resolve()
    except OSError:
        return True
    if name == "GIT_WORK_TREE":
        return resolved == _REPO_ROOT
    if name == "GIT_INDEX_FILE":
        # The hook's index lives inside this repo's git dir.
        return resolved.parent == own_git_dir
    return resolved == own_git_dir


def isolated_git_env() -> dict[str, str]:
    """Env for git subprocesses that operate on THROWAWAY repositories.

    Strips hook/quarantine state and repo pointers regardless of the session
    scrub decision: an intentional external pointer is correct for the suite
    (it describes the checkout) but catastrophic for a subprocess git cwd'd
    into a temp repo (Codex P2 on #338 — isolation is the tests' job here).
    """
    env = dict(os.environ)
    for name in (*_REPO_POINTER_VARS, *_QUARANTINE_VARS):
        env.pop(name, None)
    return env


def _decide_pointer_scrub(own_git_dir: Path | None, has_local_git_entry: bool) -> list[str]:
    """Which repo-pointer variables to scrub (pure decision, unit-tested).

    - Pointer-only checkout (no local .git entry, discovery without the
      pointers fails): the env pointers ARE this checkout's metadata — keep
      them all (Codex P2 on #338).
    - Unresolvable otherwise: fail toward the historically safe scrub.
    - Normal checkout: scrub exactly the pointers that target THIS repo.
    """
    if own_git_dir is None and not has_local_git_entry:
        return []
    if own_git_dir is None:
        return list(_REPO_POINTER_VARS)
    return [name for name in _REPO_POINTER_VARS if _pointer_targets_this_repo(name, own_git_dir)]


@pytest.fixture(scope="session", autouse=True)
def _scrub_git_hook_env():
    in_quarantine = "GIT_QUARANTINE_PATH" in os.environ
    own_git_dir = _outer_repo_git_dir()
    scrub = _decide_pointer_scrub(own_git_dir, (_REPO_ROOT / ".git").exists())
    if in_quarantine:
        scrub += list(_QUARANTINE_VARS)
    removed = {name: os.environ.pop(name) for name in scrub if name in os.environ}
    yield removed
    os.environ.update(removed)


@pytest.fixture(scope="session", autouse=True)
def _session_event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()
