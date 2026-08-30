"""Regression: the pytest session must not inherit git's pre-push hook env.

`git push` runs pre-push hooks with GIT_QUARANTINE_PATH / GIT_OBJECT_DIRECTORY /
GIT_ALTERNATE_OBJECT_DIRECTORIES (git >= 2.11 object quarantine) and
GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE (pointing at the outer repository)
exported. The pre-push hook runs this suite; tests that spawn `git` subprocesses
against throwaway repos inherit that state and operate on the wrong repository
(16 failures observed during a real push). The session-scoped autouse fixture
in conftest.py must scrub those variables.

Codex P2 refinement: the object-store variables are only scrubbed as part of a
quarantine context (GIT_QUARANTINE_PATH present). Without a quarantine they can
be an intentional launch configuration (e.g. a CI checkout with a relocated or
shared object store) and must survive; the repo pointers are always scrubbed —
in the suite's context they always describe the outer repository.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

_HOOK_ENV_QUARANTINE = {
    "GIT_QUARANTINE_PATH": "/tmp/does-not-exist-quarantine",
    "GIT_OBJECT_DIRECTORY": "/tmp/does-not-exist-quarantine-objects",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/tmp/does-not-exist-quarantine-alt",
}
_REPO_POINTERS = {
    "GIT_DIR": "/tmp/does-not-exist-quarantine/.git",
    "GIT_WORK_TREE": "/tmp/does-not-exist-quarantine",
    "GIT_INDEX_FILE": "/tmp/does-not-exist-quarantine-index",
}


def _child_pytest(env_extra: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update(env_extra)
    env.pop("GIT_QUARANTINE_PATH", None)
    for name in _REPO_POINTERS:
        env.pop(name, None)
    for name in _HOOK_ENV_QUARANTINE:
        env.pop(name, None)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *args],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_repo_pointers_and_quarantine_are_scrubbed():
    """Under a full hook env (quarantine + pointers) nothing leaks."""
    result = _child_pytest(
        {**_HOOK_ENV_QUARANTINE, **_REPO_POINTERS},
        "tests/unit/test_prepush_quarantine_scrub.py::test_no_hook_env_leak",
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]


def test_object_store_vars_survive_without_quarantine():
    """Codex P2: intentional object-store config (no quarantine) is preserved."""
    result = _child_pytest(
        {k: v for k, v in _HOOK_ENV_QUARANTINE.items() if k != "GIT_QUARANTINE_PATH"},
        "tests/unit/test_prepush_quarantine_scrub.py::test_object_store_vars_preserved",
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]


def test_no_hook_env_leak():
    """Runs as a CHILD pytest under the full hook env: nothing may leak through."""
    for name in {**_HOOK_ENV_QUARANTINE, **_REPO_POINTERS}:
        assert name not in os.environ, f"{name} leaked through the session scrub"


def test_object_store_vars_preserved():
    """Runs as a CHILD pytest: intentional object-store vars survive (P2)."""
    import pytest

    if not (
        "GIT_OBJECT_DIRECTORY" in os.environ or "GIT_ALTERNATE_OBJECT_DIRECTORIES" in os.environ
    ):
        pytest.skip("child-only probe: run via test_object_store_vars_survive_without_quarantine")
    assert "GIT_QUARANTINE_PATH" not in os.environ  # precondition: no quarantine
    for name in ("GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        assert os.environ.get(name), f"{name} must survive outside quarantine contexts"


def test_git_spawning_suite_passes_with_quarantine_parent_env():
    """Reproduce the pre-push hook context: run the two suites that spawn real
    `git` subprocesses as a child pytest with the hook env exported, and
    require them to pass. Before the conftest scrub this failed 16/45."""
    result = _child_pytest(
        {**_HOOK_ENV_QUARANTINE, **_REPO_POINTERS},
        "tests/unit/test_governance_verify_rollback.py",
        "tests/unit/test_assembly_pi_omp_adapter_red.py",
    )
    assert result.returncode == 0, (
        "git-spawning tests fail under hook env (pre-push hook context):\n"
        f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
    )
