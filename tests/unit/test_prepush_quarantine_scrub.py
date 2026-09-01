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

import pytest

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


def _child_pytest(
    env_extra: dict[str, str], *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    if env is None:
        # Fresh baseline: strip any ambient hook/quarantine state so the
        # child sees exactly what the caller passes. An EXPLICIT env is
        # respected as-is (the intentional-pointer scenarios depend on it).
        env = dict(os.environ)
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


@pytest.mark.parametrize("keep", ["both", "object_dir", "alternates"])
def test_object_store_vars_survive_without_quarantine(keep):
    """Codex P2: intentional object-store config (no quarantine) is preserved —
    including configurations that set only ONE of the pair."""
    base = {k: v for k, v in _HOOK_ENV_QUARANTINE.items() if k != "GIT_QUARANTINE_PATH"}
    if keep == "object_dir":
        base.pop("GIT_ALTERNATE_OBJECT_DIRECTORIES")
    elif keep == "alternates":
        base.pop("GIT_OBJECT_DIRECTORY")
    result = _child_pytest(
        base,
        "tests/unit/test_prepush_quarantine_scrub.py::test_object_store_vars_preserved",
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]


def test_no_hook_env_leak():
    """Runs as a CHILD pytest under the full hook env: nothing may leak through."""
    for name in {**_HOOK_ENV_QUARANTINE, **_REPO_POINTERS}:
        assert name not in os.environ, f"{name} leaked through the session scrub"


def test_object_store_vars_preserved():
    """Runs as a CHILD pytest: intentional object-store vars survive (P2).

    Codex P2 on #332: only the variables PRESENT AT LAUNCH are asserted —
    a valid intentional configuration may set just one of the pair."""
    import pytest

    expected = [
        name
        for name in ("GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES")
        if name in os.environ
    ]
    if not expected:
        pytest.skip("child-only probe: run via test_object_store_vars_survive_without_quarantine")
    assert "GIT_QUARANTINE_PATH" not in os.environ  # precondition: no quarantine
    for name in expected:
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


def _tmp_git_repo(tmp: Path) -> Path:
    """A real throwaway git repo for intentional-pointer scenarios.

    Codex P2 round 6: under a pointer-only checkout the session KEEPS the
    pointers, so a bare `git init` would honor the inherited GIT_DIR and
    reinitialize the outer checkout — initialize isolated and verify the
    resulting git dir is under tmp.
    """
    from conftest import isolated_git_env

    subprocess.run(
        ["git", "init", "--quiet", str(tmp)],
        env=isolated_git_env(),
        check=True,
        timeout=60,
    )
    git_dir = tmp / ".git"
    assert git_dir.exists(), "isolated git init must create tmp/.git, not reuse GIT_DIR"
    return git_dir


def test_intentional_external_repo_pointers_survive(tmp_path):
    """discussion_r3890533687 (Codex P2 round 2): a checkout whose git
    location is supplied ONLY via GIT_DIR/GIT_WORK_TREE (git dir mounted
    elsewhere, no local .git) must keep its pointers — dropping them makes
    git-spawning tests fail with 'not a git repository'."""
    external = _tmp_git_repo(tmp_path / "external")
    work_tree = tmp_path / "external"
    env = dict(os.environ)
    for name in _REPO_POINTERS:
        env.pop(name, None)
    env.update(
        {
            "GIT_DIR": str(external),
            "GIT_WORK_TREE": str(work_tree),
            "GIT_INDEX_FILE": str(external / "index"),
        }
    )
    env["VOYAGER_PROBE_EXPECT_POINTERS"] = "1"
    result = _child_pytest(
        {},
        "tests/unit/test_prepush_quarantine_scrub.py::test_intentional_pointers_preserved",
        env=env,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]


def test_intentional_pointers_preserved():
    """Runs as a CHILD pytest under external GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE.

    The VOYAGER_PROBE_EXPECT_POINTERS marker distinguishes the intentional-
    pointer probe run from a plain standalone invocation; without it a scrub
    that already dropped the vars would turn this into a vacuous skip/pass.
    """
    import pytest

    if os.environ.get("VOYAGER_PROBE_EXPECT_POINTERS") != "1":
        pytest.skip("child-only probe: run via test_intentional_external_repo_pointers_survive")
    for name in _REPO_POINTERS:
        assert os.environ.get(name), f"{name} must survive when it points elsewhere"


def test_hook_contaminated_repo_pointers_are_scrubbed():
    """Pointers that point at THIS repo are hook leakage and must be scrubbed."""
    git_dir = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.strip()
    result = _child_pytest(
        {
            "GIT_DIR": git_dir,
            "GIT_WORK_TREE": str(_REPO_ROOT),
        },
        "tests/unit/test_prepush_quarantine_scrub.py::test_no_hook_env_leak",
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]


def test_pointer_only_checkout_keeps_all_pointers():
    """Codex P2 on #338: a checkout with NO local .git entry is described only
    by its env pointers — discovery without them fails, and the decision must
    KEEP every pointer (they are the checkout metadata, not leakage)."""
    from conftest import _decide_pointer_scrub

    assert _decide_pointer_scrub(None, has_local_git_entry=False) == []


def test_unresolvable_normal_checkout_scrubs_all():
    """Discovery failure WITH a local .git entry fails toward the safe scrub."""
    from conftest import _decide_pointer_scrub

    assert _decide_pointer_scrub(None, has_local_git_entry=True) == [
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
    ]


def test_normal_checkout_scrubs_only_this_repos_pointers(monkeypatch):
    """Pointers pointing elsewhere survive; pointers here are scrubbed."""
    import conftest
    from conftest import _decide_pointer_scrub

    own = conftest._REPO_ROOT / ".git"
    monkeypatch.setenv("GIT_DIR", "/tmp/elsewhere/.git")
    monkeypatch.setenv("GIT_WORK_TREE", str(conftest._REPO_ROOT))
    scrub = _decide_pointer_scrub(own, True)
    assert "GIT_WORK_TREE" in scrub
    assert "GIT_DIR" not in scrub


def test_ancestor_git_dir_discovery_is_rejected(tmp_path, monkeypatch):
    """Codex P2 on #346: a pointer-only checkout nested INSIDE another git
    repository must not inherit the ancestor's git dir from discovery —
    that would drop its pointer-only status and scrub the intentional
    GIT_DIR/GIT_WORK_TREE pair that describes the checkout."""
    import conftest
    from conftest import _decide_pointer_scrub, _outer_repo_git_dir

    ancestor = tmp_path / "ancestor"
    ancestor.mkdir()
    _tmp_git_repo(ancestor)
    nested = ancestor / "nested"
    nested.mkdir()

    monkeypatch.setattr(conftest, "_REPO_ROOT", nested.resolve())
    # Discovery without pointers walks UP to the ancestor — must be rejected
    # because the ancestor's top level is not the checkout root.
    assert _outer_repo_git_dir() is None
    # Pointer-only branch therefore keeps every pointer.
    assert _decide_pointer_scrub(None, has_local_git_entry=False) == []

    # Control: a real checkout of the ancestor discovers its own git dir.
    monkeypatch.setattr(conftest, "_REPO_ROOT", ancestor.resolve())
    assert _outer_repo_git_dir() == (ancestor / ".git").resolve()
