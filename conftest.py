"""Root conftest: establish a persistent event loop for sync BDD step functions.

pytest-bdd step functions are synchronous and use asyncio.get_event_loop().run_until_complete().
In Python 3.12+, get_event_loop() raises RuntimeError when no loop is set in the current
thread. This fixture sets one for the duration of the session.
"""

from __future__ import annotations

import asyncio
import os

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
# Codex P2: only scrub the object-store variables as part of a quarantine
# context — without GIT_QUARANTINE_PATH, GIT_OBJECT_DIRECTORY /
# GIT_ALTERNATE_OBJECT_DIRECTORIES can be an intentional configuration (e.g.
# a CI checkout with a relocated or shared object store) and must survive.
# The repo pointers (GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE) are scrubbed
# whenever set: in the suite's context they always describe the OUTER repo,
# never the throwaway repositories the tests build.
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


@pytest.fixture(scope="session", autouse=True)
def _scrub_git_hook_env():
    in_quarantine = "GIT_QUARANTINE_PATH" in os.environ
    scrub = list(_REPO_POINTER_VARS) + (list(_QUARANTINE_VARS) if in_quarantine else [])
    removed = {name: os.environ.pop(name) for name in scrub if name in os.environ}
    yield removed
    os.environ.update(removed)


@pytest.fixture(scope="session", autouse=True)
def _session_event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()
