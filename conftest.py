"""Root conftest: establish a persistent event loop for sync BDD step functions.

pytest-bdd step functions are synchronous and use asyncio.get_event_loop().run_until_complete().
In Python 3.12+, get_event_loop() raises RuntimeError when no loop is set in the current
thread. This fixture sets one for the duration of the session.
"""

from __future__ import annotations

import asyncio
import os

import pytest

# Git exports repo-pointing environment variables to hooks: during pre-push
# (git >= 2.11) GIT_QUARANTINE_PATH / GIT_OBJECT_DIRECTORY /
# GIT_ALTERNATE_OBJECT_DIRECTORIES describe the outer push's object quarantine,
# and GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE point at THIS repository. The
# pre-push hook in .pre-commit-config.yaml runs the full pytest suite, so every
# test that spawns a `git` subprocess (governance verify-rollback fixtures,
# Assembly OMP LFS fixtures) inherits that state and operates on the wrong
# repository or against a nonexistent quarantine. Scrub the variables for the
# whole session — they only ever describe the OUTER push, never the repos the
# tests build.
_GIT_HOOK_ENV_VARS = (
    "GIT_QUARANTINE_PATH",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
)


@pytest.fixture(scope="session", autouse=True)
def _scrub_git_hook_env():
    removed = {name: os.environ.pop(name) for name in _GIT_HOOK_ENV_VARS if name in os.environ}
    yield removed
    os.environ.update(removed)


@pytest.fixture(scope="session", autouse=True)
def _session_event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()
