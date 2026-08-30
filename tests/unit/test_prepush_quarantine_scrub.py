"""Regression: the pytest session must not inherit git's pre-push hook env.

`git push` runs pre-push hooks with GIT_QUARANTINE_PATH / GIT_OBJECT_DIRECTORY /
GIT_ALTERNATE_OBJECT_DIRECTORIES (git >= 2.11 object quarantine) and
GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE (pointing at the outer repository)
exported. The pre-push hook runs this suite; tests that spawn `git` subprocesses
against throwaway repos inherit that state and operate on the wrong repository
(16 failures observed during a real push). The session-scoped autouse fixture
in conftest.py must scrub those variables.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

_HOOK_ENV = {
    "GIT_QUARANTINE_PATH": "/tmp/does-not-exist-quarantine",
    "GIT_OBJECT_DIRECTORY": "/tmp/does-not-exist-quarantine-objects",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/tmp/does-not-exist-quarantine-alt",
    "GIT_DIR": "/tmp/does-not-exist-quarantine/.git",
    "GIT_WORK_TREE": "/tmp/does-not-exist-quarantine",
    "GIT_INDEX_FILE": "/tmp/does-not-exist-quarantine-index",
}


def test_hook_env_vars_are_scrubbed_from_session_env():
    for name in _HOOK_ENV:
        assert name not in os.environ, (
            f"{name} leaked through the session; spawned git subprocesses would "
            "inherit the outer push's repository/quarantine state"
        )


def test_git_spawning_suite_passes_with_hook_parent_env():
    """Reproduce the pre-push hook context: run the two suites that spawn real
    `git` subprocesses as a child pytest with the hook env exported, and
    require them to pass. Before the conftest scrub this failed 16/45."""
    env = dict(os.environ)
    env.update(_HOOK_ENV)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/unit/test_governance_verify_rollback.py",
            "tests/unit/test_assembly_pi_omp_adapter_red.py",
        ],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, (
        "git-spawning tests fail under quarantine env (pre-push hook context):\n"
        f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
    )
