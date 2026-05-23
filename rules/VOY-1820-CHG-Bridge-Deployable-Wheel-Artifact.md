# CHG-1820: Bridge — Deployable Wheel Artifact + `vyg` CLI

**Applies to:** VOY project
**Last updated:** 2026-05-23
**Last reviewed:** 2026-05-23
**Status:** Proposed
**Date:** 2026-05-23
**Scheduled:** After CHG plan-review approval in the active VOY-1811 loop for #75.
**Requested by:** Frank Xu (via issue #75, 2026-05-23)
**Priority:** P2
**Change Type:** Normal
**Targets:** `voyager/cli.py`, `voyager/build_info.py` (new), `voyager/server.py`, `pyproject.toml`, `deploy/launchd/com.iterwheel.voyager.bridge.plist`, `scripts/build_wheel.sh` (new), `rules/VOY-1814-SOP-Wukong-Bridge-Launchd-and-Rollback.md`, `tests/unit/`, `.gitignore`
**Closes:** #75
**Related:** VOY-1814 SOP (Wukong bridge launchd + rollback), VOY-1807 REF (GitHub App Registry), VOY-1817 CHG (Assembly MVP), VOY-1818 CHG (Actor authz), VOY-1819 CHG (Assembly hardening)

---

## What

Replace the production-bridge startup pattern from "uvicorn run from a mutable
source checkout" with "installed `vyg` CLI from a wheel into a dedicated
production virtualenv". The change has four surfaces:

1. **`vyg` CLI** — wire `voyager/cli.py` (currently a `NotImplementedError`
   stub already registered as the `vyg` script in `pyproject.toml`) into a
   real Typer-based CLI with two subcommands: `vyg bridge serve` (replaces
   `python -m uvicorn voyager.server:app …`) and `vyg version` (operator
   sanity check).
2. **Build-commit metadata** — at wheel-build time, write
   `voyager/_build_info.py` containing the git SHA; at runtime, expose
   `voyager.build_info.BUILD_COMMIT` (falls back to `"dev"` when `_build_info.py`
   is absent, e.g. editable installs). Surface this via `/healthz`
   alongside the package version.
3. **Launchd plist update** — change `deploy/launchd/com.iterwheel.voyager.bridge.plist`
   to exec the installed `vyg bridge serve` from `~/.voyager/.venv/bin/vyg`
   rather than `python -m uvicorn` from the source checkout. Remove the
   `WorkingDirectory` key — the installed CLI does not need it.
4. **VOY-1814 SOP amendment** — add a new step "Install the wheel into the
   production venv" between the existing Step 1 (Repository Artifacts) and
   the current Step 2 (Launchd Service Setup); cascade renumber. Document
   the wheel-build command (`bash scripts/build_wheel.sh`), the venv layout
   (`~/.voyager/.venv-vX.Y.Z` with `~/.voyager/.venv` symlink for the
   active version), and a venv-swap rollback path.

The scope is **wheel-based deployment only**. PyInstaller / Nuitka /
single-file-binary builds remain out of scope per the issue body's "wheel is
sufficient; binaries can remain a later option" clause.

## Why

The current launchd plist execs `python -m uvicorn voyager.server:app` from
`/Users/frank/Projects/voyager`. If that working tree is checked out to a
feature branch (which is a routine development pattern — every Phase 5 loop
of VOY-1811 leaves the tree on a feature branch), a subsequent service
restart silently runs unmerged code in production. The issue cites this as
the motivating risk: a branch switch + launchd restart = production runs
development code.

A wheel-based install fixes the root cause:

- Production runs from `~/.voyager/.venv/bin/vyg`, which is an isolated
  Python installation tree owned by the bridge artifact, not by the dev
  checkout.
- The development checkout under `/Users/frank/Projects/voyager` becomes
  pure source — no longer load-bearing for production.
- `/healthz` reports the installed version + build commit, so an operator
  can verify which artifact is live without ssh-ing into the host and
  running `git log`.
- Rollback becomes "swap `~/.voyager/.venv` symlink to a prior venv" — a
  single atomic operation that cannot accidentally pick up uncommitted
  changes.

This is a P2 deployment-hygiene change that should land before Assembly
expands to production allow-lists beyond `iterwheel/voyager-sandbox` (per
VOY-1819 §Out of Scope). Issue #75 cites this as a follow-up to the
Assembly hardening work in #73 / #79.

## Acceptance Criteria → Surface Map

Maps each of issue #75's 6 acceptance criteria to its primary surface(s),
closing the MiniMax-R1 self-certification gap.

| Issue AC | Primary Surface(s) | Verified by |
|----------|--------------------|-------------|
| AC1: Production bridge startup no longer depends on `/Users/frank/Projects/voyager` being on `main` | Surface 1 (CLI), Surface 6 (plist), Surface 8 (VOY-1814 amendment) | Manual smoke (§Testing) — install wheel, start launchd, switch dev tree to a feature branch, verify `/healthz` still reports the wheel's commit |
| AC2: A clean install from a built wheel can start the bridge and serve `/healthz` | Surfaces 1, 7 (build script), 14 (wheel-integration smoke test) | Surface 14 unit test + manual smoke in throwaway venv |
| AC3: `/healthz` reports package version and build commit | Surface 2 (build_info), Surface 4 (/healthz fields), Surface 12 (test) | Surface 12 TestClient assertion |
| AC4: launchd instructions point to the installed executable in the production venv | Surface 6 (plist `ProgramArguments`), Surface 8 (VOY-1814 new Step 5) | Manual smoke — `launchctl print` matches the deployed plist |
| AC5: Rollback can be performed by switching to a previous installed artifact or venv and restarting launchd | D6 (venv symlink layout), §Rollback plan path 2, Surface 8 (rollback section in VOY-1814) | Manual smoke — install two wheels, swap symlink via `mv -f`, kickstart, verify new commit reported |
| AC6: Existing bridge tests continue to pass | All Surface-10/11/12/13/14 tests + §Testing tooling block | `uv run pytest tests/` → 1140 + new tests, ruff + mypy green |

## Out of Scope

- **PyInstaller / Nuitka / single-file binary builds.** Issue #75 explicitly
  scopes this work to "wheel-based deployment; PyInstaller/Nuitka-style
  single-file binaries can remain a later option if a wheel is not
  sufficient." Wheels are sufficient for Voyager's deployment surface (one
  Mac mini, one venv); a single-file binary's only advantage would be
  shipping to a host without Python, which is not Voyager's situation.
- **Automated wheel publishing to PyPI / GitHub Releases.** The wheel is
  built locally on Wukong (or any operator's machine) and installed into
  the local production venv. An automated release pipeline (CI builds
  wheel → uploads to GitHub Release → Wukong pulls) is a follow-up issue.
- **Cross-platform wheel** (manylinux / Apple Silicon-specific). The
  current Voyager wheel is pure-Python; `pyproject.toml` already declares
  `requires-python = ">=3.11"` and no native code is being added.
- **Bot business logic.** The issue explicitly says "should not change bot
  business logic" — Blueprint, Stack, Clearance, Assembly behavior is
  untouched.
- **GitHub Actions wheel-build workflow.** Local wheel build is sufficient
  for this change; CI-built wheels are deferred.
- **Cloudflare Tunnel changes.** The tunnel terminates at the same local
  `127.0.0.1:8787` endpoint regardless of how the bridge is launched.

## Impact Analysis

### Systems affected

- `voyager/cli.py`: stub → Typer CLI with `bridge serve` and `version`.
- `voyager/build_info.py` (new): runtime accessor for `__version__` +
  `BUILD_COMMIT`.
- `voyager/_build_info.py` (generated, gitignored): build-time SHA constant.
- `voyager/server.py`: `/healthz` adds `version` and `build_commit` fields.
- `pyproject.toml`: already has `vyg = "voyager.cli:main"`; no change. May
  add `[tool.hatch.build]` exclusion for `_build_info.py` if needed
  (currently the file lives inside the package, so it gets included
  automatically — desired).
- `deploy/launchd/com.iterwheel.voyager.bridge.plist`: ProgramArguments
  rewrite + WorkingDirectory removal.
- `scripts/build_wheel.sh` (new): write `_build_info.py` from
  `git rev-parse HEAD` → run `uv build` → assert wheel artifact exists.
- `rules/VOY-1814-SOP-Wukong-Bridge-Launchd-and-Rollback.md`: new wheel-install
  step + cascade renumber + Change History row.
- `.gitignore`: add `voyager/_build_info.py`.

### Channels affected

- `/healthz` JSON response gains two new keys. Existing keys preserved
  (additive change). Cloudflare-side `/healthz` probes continue to work.

### Downtime required

A single-shot bridge restart when the operator switches from the
source-checkout-based plist to the wheel-based plist. ~5 s downtime
per VOY-1814's existing restart procedure.

### External dependencies

- `typer` is already in `pyproject.toml` dependencies (line 18) — used in
  the CLI.
- `uv` is already the operator's build tool (see VOY-1810 release process).
- No new third-party packages.

### Rollback plan

Three layered rollback paths:

1. **Service-restart only (no code change)**: if the new plist starts but
   `/healthz` reports the wrong commit, `launchctl kickstart -k
   gui/$UID/com.iterwheel.voyager.bridge` re-execs the service. Same plist,
   same venv.
2. **Venv swap (atomic `rename(2)` via `mv -f`)**: keep prior
   `~/.voyager/.venv-v0.3.0` directory alongside `~/.voyager/.venv-v0.4.0`.
   The launchd plist references `~/.voyager/.venv/bin/vyg`, which is a
   symlink. To roll back (matches D6 / Surface 8):
   ```
   ln -s ~/.voyager/.venv-v0.3.0 ~/.voyager/.venv.swap-$$ \
     && mv -f ~/.voyager/.venv.swap-$$ ~/.voyager/.venv \
     && launchctl kickstart -k gui/$UID/com.iterwheel.voyager.bridge
   ```
   The `mv -f` is the atomic step (`rename(2)` on APFS/HFS+). **No git
   involvement**. (Do NOT use `ln -sfn` for the swap — it is `unlink +
   symlink` and exposes a μs window where the symlink does not exist; D6
   covers this.)
3. **Full revert** (this PR): revert the merge commit. Two prerequisites for
   the source-checkout flow to work again after revert:
   (a) Restore the prior plist via `git show HEAD~1:deploy/launchd/com.iterwheel.voyager.bridge.plist > /Users/frank/Library/LaunchAgents/com.iterwheel.voyager.bridge.plist`
       (the prior commit hash is the merge commit's first parent; if multiple
       revert-and-redeploy cycles have happened, search the history with
       `git log --oneline deploy/launchd/`).
   (b) `cd /Users/frank/Projects/voyager && uv sync` — after the revert, the
       source-checkout `.venv/` may be missing dependencies the prior plist
       expected (a later post-merge `uv sync` would have mutated it). The
       source-checkout flow's `python -m uvicorn voyager.server:app` requires
       a working `.venv/` with `uvicorn` + `fastapi` etc.
   Restart launchd via `launchctl kickstart -k gui/$UID/com.iterwheel.voyager.bridge`.
   Source-checkout-based behavior is restored.

Per-finding rollback verification:

- Confirm `~/.voyager/.venv/bin/vyg --version` reports the expected version
  (or no longer exists after a full revert).
- Confirm `/healthz` JSON contains the expected `version` + `build_commit`
  values (or no longer contains those keys after a full revert).
- Confirm the running launchd plist matches the deployed file:
  `launchctl print gui/$UID/com.iterwheel.voyager.bridge | grep arguments`.

## Surfaces

| # | Surface | Change |
|---|---------|--------|
| 1 | `voyager/cli.py` | Replace `NotImplementedError` stub with a real Typer CLI. **Module-level structure**: `app = typer.Typer(no_args_is_help=True)` + `bridge_app = typer.Typer(no_args_is_help=True)` + `app.add_typer(bridge_app, name="bridge")`. Two commands: `vyg bridge serve --host <str> --port <int> --log-level <str>` invokes `uvicorn.run("voyager.server:app", host=host, port=port, log_level=log_level)`. Defaults: `host="127.0.0.1"`, `port=8787`, `log_level="info"` (matches the current launchd command). The `--log-level` flag is exposed (not hardcoded per GLM advisory) so operators can crank up to `debug` without redeploying. `vyg version` prints two lines: `version: <version>` and `build_commit: <commit-or-"dev">`, reading from `voyager.build_info`. Keep `def main() -> None: app()` so the existing entry point in `pyproject.toml:37` resolves. All commands use Typer's built-in help. **Signal handling (per GLM advisory)**: `uvicorn.run` installs its own SIGTERM/SIGINT handlers; launchd's `bootout` propagates SIGTERM via the `/bin/zsh -lc "exec vyg …"` chain (the `exec` keyword is load-bearing — it replaces the zsh process with `vyg`, so launchd's signal lands directly on uvicorn). Manual verification step in the smoke list (§Testing). |
| 2 | `voyager/build_info.py` (new) | Public runtime accessor module. Defines `VERSION` (from `voyager.__version__`) and `BUILD_COMMIT` (defaults to `"dev"`; replaced via `from voyager._build_info import BUILD_COMMIT` if the generated file exists). Also exposes `def get_info() -> dict[str, str]` returning `{"version": VERSION, "build_commit": BUILD_COMMIT}` for `/healthz`. No external dependencies — must work in editable installs without the build hook running. |
| 3 | `voyager/_build_info.py` (generated, gitignored) | Created by `scripts/build_wheel.sh`. One-liner: `BUILD_COMMIT = "<git-sha>"`. Lives inside the `voyager/` package directory so the wheel-build step picks it up automatically (per `[tool.hatch.build.targets.wheel] packages = ["voyager"]` in `pyproject.toml`). Listed in `.gitignore` so editable installs (developer machines) never have it; the runtime accessor (Surface 2) falls back to `"dev"` cleanly. |
| 4 | `voyager/server.py` `/healthz` endpoint | Add two keys to the returned dict: `"version": VERSION` and `"build_commit": BUILD_COMMIT`. Import from `voyager.build_info`. Keep existing keys (`ok`, `service`, `time`, `dry_run`) untouched — additive change so any existing probe consumer is unaffected. |
| 5 | `pyproject.toml` | (1) **Critical** — add `[tool.hatch.build]` section with `artifacts = ["voyager/_build_info.py"]` at the **general** build scope (not the per-target scope). This is required because hatchling respects `.gitignore` by default and silently excludes the gitignored `_build_info.py` from the wheel even though it lives inside the `voyager/` package directory. The fix is **empirically verified** in a throwaway hatch project (build with `[tool.hatch.build] artifacts = [...]` includes the file; without it, the wheel ships without the build SHA and `/healthz` reports `"dev"`). (2) Entry point at line 37 (`vyg = "voyager.cli:main"`) is already declared — no change. (3) Add a comment block above the `[project.scripts]` section documenting the wheel-build flow: "Build a deployable wheel via `bash scripts/build_wheel.sh`. The script writes `voyager/_build_info.py` with the current git SHA before invoking `uv build`. **DO NOT run `uv build` directly** — the wrapping script is the only safe entry point because it both generates the SHA file and verifies the dirty-tree gate. A direct `uv build` ships a wheel with `BUILD_COMMIT='dev'` even from a clean tree (the file does not exist yet). The generated file is gitignored; editable installs fall back to `BUILD_COMMIT='dev'`." |
| 6 | `deploy/launchd/com.iterwheel.voyager.bridge.plist` | Rewrite `ProgramArguments` to: `/bin/zsh -lc "set -a && source /Users/frank/.voyager/bridge.env && set +a && exec /Users/frank/.voyager/.venv/bin/vyg bridge serve --host 127.0.0.1 --port 8787"`. Remove the `WorkingDirectory` key entirely — the installed CLI does not need a working directory anchored to the source tree. Keep `RunAtLoad`, `KeepAlive`, `ThrottleInterval`, `Standard*Path`, `Umask` unchanged. |
| 7 | `scripts/build_wheel.sh` (new) | Bash script. **Lives at `scripts/build_wheel.sh`** — `ls scripts/` confirms the directory currently has only `scripts/e2e/`, so a top-level `scripts/build_wheel.sh` is consistent (the wheel-build is a deployment-adjacent operation, not an e2e test). Steps: (a) `set -euo pipefail`; (b) require `git`/`uv` in PATH; (c) `commit=$(git rev-parse HEAD)`; (d) ensure `git status --porcelain` is empty OR `VOYAGER_BUILD_ALLOW_DIRTY=1` is set (refuse to build a dirty tree by default, with the env-var override for emergency rebuilds and for future CI workflow env injection); (e) `trap 'rm -f voyager/_build_info.py' EXIT INT TERM HUP` — cleanup fires on normal exit AND signals; (f) `printf 'BUILD_COMMIT = "%s"\n' "$commit" > voyager/_build_info.py`; (g) `uv build`; (h) assert `dist/iterwheel_voyager-*.whl` exists AND contains `voyager/_build_info.py` (`unzip -l dist/iterwheel_voyager-*.whl \| grep -q '_build_info.py'`) — this catches the hatchling-gitignore regression at build time, before any wheel ships; (i) print the wheel path + the commit SHA; (j) the trap from step (e) cleans up the `voyager/_build_info.py` so the dev tree is left clean. Exit code 0 on success. The wheel itself already has the file baked in. **SIGKILL caveat (per GLM advisory)**: an EXT trap does not fire on SIGKILL (`kill -9`); in that rare case the developer's dev tree may have a stray `_build_info.py`. The file is gitignored so it does not pollute git; the only side effect is editable installs in the same venv reading the stale commit until the file is manually removed. Mitigation: a `make clean` target (out of scope here) and the gitignore entry, both already covered. |
| 8 | `rules/VOY-1814-SOP-Wukong-Bridge-Launchd-and-Rollback.md` | Insert a new step **5. "Install the production wheel"** between the existing Step 4 (Run Preflight) and the current Step 5 (Install the LaunchAgent, which becomes Step 6 after cascade renumber). The placement is deliberate: preflight must pass (port free, plist syntactically valid) before the venv is built, and the venv must exist before launchd boots the plist that references `~/.voyager/.venv/bin/vyg`. **Correct VOY-1814 step structure (verified)**: Step 1 = Confirm Repository Artifacts; Step 2 = Prepare Private Wukong Files; Step 3 = Preserve the Production Environment Contract; Step 4 = Run Preflight; Step 5 = Install the LaunchAgent. The new step 5 inserts before the current step 5 (LaunchAgent install), which becomes step 6. New step content: build wheel via `bash scripts/build_wheel.sh`; create `~/.voyager/.venv-vX.Y.Z` via `uv venv ~/.voyager/.venv-vX.Y.Z`; install via `~/.voyager/.venv-vX.Y.Z/bin/pip install dist/iterwheel_voyager-X.Y.Z-py3-none-any.whl`; **atomic symlink swap**: `ln -s ~/.voyager/.venv-vX.Y.Z ~/.voyager/.venv.swap-$$ && mv -f ~/.voyager/.venv.swap-$$ ~/.voyager/.venv` (per D6: the `mv -f` step is what's actually atomic via `rename(2)`; bare `ln -sfn` is `unlink + symlink` and has a μs window where the symlink is missing; the `$$` PID suffix on the intermediate name avoids collision in scripted contexts). Verify `~/.voyager/.venv/bin/vyg version` prints the expected version + commit. Also update §Rollback section: add the venv-swap rollback path as the **preferred** approach (atomic, no git involvement). Add a Change History row dated 2026-05-23. |
| 9 | `.gitignore` | Append `voyager/_build_info.py` (with a one-line comment: `# Generated by scripts/build_wheel.sh; baked into the wheel.`). |
| 10 | `tests/unit/test_cli.py` (new) | Unit tests for the Typer CLI. Cases: (a) `vyg --help` exits 0 and contains "bridge" + "version"; (b) `vyg version` prints `version:` + `build_commit:` lines (use Typer's `CliRunner`); (c) `vyg bridge --help` shows `serve`; (d) `vyg bridge serve --help` shows `--host` + `--port` and exits 0 (asserts help works, not the actual server invocation); (e) `vyg bridge serve` is **invoked with `monkeypatch.setattr` on `uvicorn.run`** (or equivalent — e.g., patch `voyager.cli.uvicorn.run`) so the test never blocks on a real server, and the patched function records the `host`/`port` args; assertion: defaults of `127.0.0.1` and `8787` arrive at uvicorn.run when no flags are passed. (Per DS-R1 P1: with `--host` and `--port` defaulted, `vyg bridge serve` is RUNNABLE — `no_args_is_help` at the parent `bridge` subgroup level does not block its leaf commands. The original "no_args triggers help" expectation was wrong.) |
| 11 | `tests/unit/test_build_info.py` (new) | Unit tests for `voyager.build_info`. Cases: (a) **absent-file fallback**: use `monkeypatch.setitem(sys.modules, "voyager._build_info", None)` — this **poisons the import** so even if a real `_build_info.py` exists on disk in `site-packages/voyager/` (e.g., from a prior wheel install in the same venv), the import returns `None` and the accessor falls back to `"dev"`. Re-import `voyager.build_info` (use `importlib.reload`) and assert `BUILD_COMMIT == "dev"` and `get_info() == {"version": VERSION, "build_commit": "dev"}`. **`delitem`-only is wrong** because it merely clears the cache; the next import re-reads from disk. (b) **present-file path**: `monkeypatch.setitem(sys.modules, "voyager._build_info", types.SimpleNamespace(BUILD_COMMIT="abc1234"))`, reload `voyager.build_info`, assert `BUILD_COMMIT == "abc1234"`. NEVER write the actual `_build_info.py` file in tests. |
| 12 | `tests/unit/test_healthz_metadata.py` (new) | Unit test for the `/healthz` response shape. Use FastAPI's TestClient: assert `r.json()` contains `version`, `build_commit` keys with string values; assert existing keys (`ok`, `service`, `time`, `dry_run`) still present (negative regression assertion against accidental key removal). |
| 13 | `tests/unit/test_xtest_cli_smoke.py` (new) | Independent cross-test (per VOY-1817 §Phase 6). Exercises `vyg --help` and `vyg version` only via `subprocess.run([sys.executable, "-m", "voyager.cli", "--help"])` (uses the module entry point, not the installed script). Asserts exit code + output substrings, no reach into Typer internals. |
| 14 | `tests/unit/test_wheel_build_smoke.py` (new) | **Wheel-integration smoke test** (per GLM-R1 advisory). **Test contract** — one test `test_built_wheel_contains_build_info_and_reports_commit` asserting two invariants: (1) the wheel built by `scripts/build_wheel.sh` contains `voyager/_build_info.py` (regression gate for hatchling's gitignore-exclusion behavior), and (2) that file's `BUILD_COMMIT` constant equals the current `git rev-parse HEAD` (regression gate for the build script's SHA-injection step). **Skip when `uv` is unavailable**: `pytestmark = pytest.mark.skipif(not shutil.which("uv"), reason="uv required for wheel build")`. Also `pytest.mark.slow` so pre-merge CI runs it but the fast unit loop skips it. **Why in-place** (not `tmp_path`): `scripts/build_wheel.sh` calls `git rev-parse HEAD`, which requires a `.git/` directory. A naive `cp -r voyager pyproject.toml tmp_path/` clone has no `.git/` and would fail. Building in the project root with `cwd=PROJECT_ROOT` reuses the existing `.git/`. **Implementer-facing protocol sketch**: snapshot `set(os.listdir("dist"))` before; `subprocess.run(["bash", "scripts/build_wheel.sh"], cwd=PROJECT_ROOT, check=True)`; compute the new wheel by set-diff against the pre-snapshot; open it with `zipfile.ZipFile(wheel)` and verify `voyager/_build_info.py` appears in `.namelist()` and its decoded contents match `f'BUILD_COMMIT = "{current_sha}"\n'`. Cleanup: delete the new wheel from `dist/` at end-of-test so the dev tree is left clean. This single test would have caught the GLM-P1 hatch-include bug at PR time. |

## Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Typer for the CLI** (not Click directly, not argparse). | `typer` is already in `pyproject.toml` dependencies (line 18). Typer is a Click-on-top-of-typing wrapper that gives the same CLI ergonomics with less boilerplate. Adding a second framework (raw Click or argparse) just to avoid Typer would be friction. |
| D2 | **`vyg bridge serve` calls `uvicorn.run(...)` directly**, does NOT shell out to `python -m uvicorn`. | The whole point of this CHG is to avoid depending on the Python interpreter's CWD or sys.path being correct. Direct `uvicorn.run` is the canonical embedded-server pattern; the wheel-installed `vyg` finds the correct `voyager.server:app` via the installed package metadata. |
| D3 | **Build-commit injection via a generated `voyager/_build_info.py` written by a shell script**, not via a hatchling custom build hook. | Hatchling has a `BuildHookInterface` for arbitrary build-time data injection, but a custom hook adds a `hatch_build.py` at the project root + a `[tool.hatch.build.hooks.custom]` config block + an extra import path that runs on every editable install. A shell script is a single 30-line file that runs only at wheel-build time, leaves the dev tree clean afterward (trap cleanup), and is trivially auditable. The cost is one extra `bash scripts/build_wheel.sh` invocation before `uv build` — acceptable for a deployment script. |
| D4 | **`BUILD_COMMIT = "dev"` fallback for editable installs.** | Developers running `uv pip install -e .` will not have `_build_info.py` present (it is gitignored). The runtime accessor (Surface 2) catches the `ImportError` and returns `"dev"`. `/healthz` then reports `build_commit: "dev"`, which is a clear signal that the operator is looking at a non-production install. |
| D5 | **`scripts/build_wheel.sh` refuses to build a dirty tree by default.** | A wheel built from a dirty tree carries a commit SHA that does not represent the actual code shipped — a deployment trap. The script's `git status --porcelain` check refuses to proceed unless `VOYAGER_BUILD_ALLOW_DIRTY=1` is exported. Operators who need to test a WIP wheel can override; the default is safe. |
| D6 | **Venv layout: `~/.voyager/.venv-vX.Y.Z` directories with `~/.voyager/.venv` symlink for the active version.** Use `ln -s target ~/.voyager/.venv.swap-$$ && mv -f ~/.voyager/.venv.swap-$$ ~/.voyager/.venv` for the atomic swap — **NOT** `ln -sfn`. The `$$` (shell PID) on the intermediate name avoids collision if two simultaneous swap operations ever run (a real concern in scripted contexts, even though Voyager is single-operator today). | Atomic rollback. `mv -f` is implemented via `rename(2)` which **is** atomic on APFS/HFS+; `ln -sfn` is `unlink(2) + symlink(2)` and exposes a μs window where the symlink does not exist (launchd's `KeepAlive` covers this in practice, but the correct primitive is `mv -f`). The PID-suffixed intermediate (`.venv.swap-$$`) is the standard "unique tmp name" pattern from `mktemp(1)`-style idioms — prevents a stuck `.venv.new` from a previously crashed swap from blocking a retry. The launchd service always reads from `~/.voyager/.venv/bin/vyg`, so the rollback boundary is just the symlink target. Multi-venv coexistence costs ~50 MB per version (Python wheels are tiny); even 10 historical versions fit in a small disk budget. |
| D7 | **`/healthz` change is additive.** | Existing probe consumers (Cloudflare, monitoring scripts) read `ok` / `service` / `time` / `dry_run`. Adding `version` + `build_commit` does not invalidate any current key; consumers that ignore unknown fields (the standard pattern) are unaffected. Surface 12 includes a negative regression assertion. |
| D8 | **VOY-1814 amendment: keep existing Steps 1-4 wording; insert wheel-install as new Step 5; current Step 5 (Install the LaunchAgent) becomes Step 6.** | The launchd-setup steps are mechanically unchanged once the plist is updated; the only new operator action is "build wheel and install into venv before running launchd setup". Inserting between Preflight (Step 4) and LaunchAgent install (current Step 5) preserves the existing operator muscle-memory while documenting the new dependency (the plist references `~/.voyager/.venv/bin/vyg`, so the venv must exist before launchd boots). |
| D9 | **`WorkingDirectory` key removed from the plist.** | The installed `vyg` CLI does not read CWD-relative paths. The `bridge.env` file path is **absolute** (`/Users/frank/.voyager/bridge.env`), so `$PWD` does not affect env-file resolution. `vyg` itself uses no relative paths — config / state / log paths are all absolute via env vars (per VOY-1814 Step 2). Keeping `WorkingDirectory: /Users/frank/Projects/voyager` after the CLI-based plist would falsely imply the source checkout is still load-bearing for production — exactly the antipattern this CHG fixes. |
| D10 | **No GitHub Actions / CI build automation in this CHG.** | The issue body says "Start with wheel-based deployment". Shipping the local build script first lets the operator validate the wheel-install flow end-to-end without depending on a CI pipeline. CI-based wheel publishing (with attestation, signing, and a versioned download step) is a clear follow-up issue. |
| D11 | **Atomic-commit policy: one commit per logical surface group.** | The 13 surfaces decompose into 6 atomic commits: (a) CLI + build-info + .gitignore; (b) /healthz update; (c) build script + pyproject comment; (d) launchd plist + VOY-1814 amendment; (e) unit tests; (f) cross-test. This matches the per-finding atomicity used in CHG-1819 — surgical revert remains possible per surface group. |

## /healthz Schema (after this CHG)

```python
{
    "ok": bool,                    # existing
    "service": str,                # existing
    "time": str,                   # existing — UTC iso
    "dry_run": bool,               # existing
    "version": str,                # NEW — e.g. "0.4.0"
    "build_commit": str,           # NEW — git SHA or "dev"
}
```

## CLI Surface (after this CHG)

```
$ vyg --help
Usage: vyg [OPTIONS] COMMAND [ARGS]...

  Voyager CLI

Commands:
  bridge   Bridge (FastAPI server) commands.
  version  Print version and build-commit metadata.

$ vyg bridge --help
Usage: vyg bridge [OPTIONS] COMMAND [ARGS]...

Commands:
  serve   Run the bridge HTTP server.

$ vyg bridge serve --help
Usage: vyg bridge serve [OPTIONS]

  Run the bridge HTTP server.

Options:
  --host TEXT   [default: 127.0.0.1]
  --port INTEGER  [default: 8787]
  --help        Show this message and exit.

$ vyg version
version: 0.4.0
build_commit: a1b2c3d4…   # or "dev"
```

## Testing / Verification

**TDD cadence**: per-surface RED → GREEN → REFACTOR. Surface 10 / 11 / 12 / 13
are the test commits; the impl commits (Surfaces 1, 2, 4, 6, 7, 8) follow.

**Build-info isolation**: tests that exercise the build-info fallback path
use the `monkeypatch.setitem(sys.modules, "voyager._build_info", None)`
import-poison pattern (followed by `importlib.reload(voyager.build_info)`),
as canonicalised in Surface 11. **Do NOT use `monkeypatch.delitem`** — it
only clears the cache; the next import re-reads from disk if a prior
wheel install left `_build_info.py` in the venv's `site-packages/voyager/`.

Unit:

- `tests/unit/test_cli.py` — Surface 10 (5 cases).
- `tests/unit/test_build_info.py` — Surface 11 (2 cases).
- `tests/unit/test_healthz_metadata.py` — Surface 12 (1 case + negative regression).

Cross-test:

- `tests/unit/test_xtest_cli_smoke.py` — Surface 13 (independent author, subprocess invocation).

Smoke (manual / one-shot, NOT in pytest):

- `bash scripts/build_wheel.sh` from a clean tree → asserts the wheel exists, prints commit SHA.
- Install into a throwaway venv: `uv venv /tmp/test-voyager-venv && /tmp/test-voyager-venv/bin/pip install dist/iterwheel_voyager-*.whl && /tmp/test-voyager-venv/bin/vyg version` → prints expected version + commit.
- Curl smoke: spawn `vyg bridge serve --port 8888` in the throwaway venv, `curl http://127.0.0.1:8888/healthz` returns the new schema. Kill the process.

Tooling:

- `uv run ruff check .` — must stay green.
- `uv run mypy voyager` — must stay green.
- `uv run pytest tests/` — must stay green (current baseline: 1140).

## Open Questions for Reviewers

1. **Venv lifecycle and disk pressure.** D6 keeps every historical venv
   under `~/.voyager/.venv-vX.Y.Z`. Should there be a cleanup policy
   (e.g., keep the last 3 versions)? Current proposal: defer; Voyager's
   release cadence is slow enough that 10 venvs × ~50 MB is trivial.
2. **`/healthz` exposing build commit publicly.** The endpoint is
   loopback-only at the bridge (`127.0.0.1:8787`) but Cloudflare proxies
   `gh.iterwheel.com/healthz` publicly. Build commit is the same
   information visible from the public repo — no new disclosure. Confirm
   or override.

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-05-23 | Initial CHG for issue #75 — deployable wheel artifact + `vyg` CLI. | Claude (via VOY-1811 #75) |
| 2026-05-23 | Round 2/3 plan-review cleanup (after GLM 9.2 PASS, DeepSeek 9.3 PASS, MiniMax 9.03 PASS): D8 wording fix (was "Step 2 (Launchd Service Setup)" — corrected to match Step 4 → new Step 5 → old Step 5 = Step 6); §Testing build-info isolation paragraph rewritten to canonical `setitem(..., None)` import-poison pattern (was stale `delitem`); §Rollback path 2 example replaced `ln -sfn` with `ln -s tmp && mv -f tmp` (matches D6 / Surface 8); Surface 14 in-place build pattern + `.git/` rationale + `pytest.mark.skipif(not shutil.which("uv"))` (per MM-R2 git-dir gap); D6 / Surface 8 / §Rollback path 2 intermediate name `.venv.new` → `.venv.swap-$$` (PID-suffix uniqueness, per MM-R2 collision finding); Surface 14 restructured to lead with test contract before implementer protocol (per MM-R3 TDD-prose nit). | Claude (via VOY-1811 #75) |
| 2026-05-23 | Round 1 plan-review remediation (GLM 8.8 FIX, DeepSeek 8.9 FIX, MiniMax 8.75 FIX): **P1 fixes** — Surface 5: added `[tool.hatch.build] artifacts = ["voyager/_build_info.py"]` at the general scope (empirically verified via throwaway hatch project that gitignored package files are excluded from the wheel without this flag); Surface 8 + D6: corrected VOY-1814 step numbering (new step inserts between Step 4 Preflight and Step 5 LaunchAgent install, not between Steps 1 and 2 as originally written); D6 + Surface 8: replaced `ln -sfn` with `ln -s tmp && mv -f tmp ~/.voyager/.venv` for actual `rename(2)` atomicity on APFS; Surface 10 case (e): removed the wrong `no_args_is_help` expectation (`vyg bridge serve` with defaults runs uvicorn; would block tests) — replaced with monkeypatch on `uvicorn.run` to verify default args arrive correctly; Surface 11: replaced `monkeypatch.delitem` with `monkeypatch.setitem(..., None)` import-poison pattern (delitem only clears the cache; the next import re-reads from disk if a wheel-install `_build_info.py` is in `site-packages`). **P2 fixes** — added §AC→Surface Map (issue #75 has 6 ACs; previously implicit); D9: noted `bridge.env` is absolute path so `$PWD` does not matter after `WorkingDirectory` removal; Surface 1: added `--log-level` flag (was hardcoded `"info"`) and signal-handling note (the `exec` keyword in the plist chain is load-bearing for SIGTERM propagation); Surface 5: explicit warning that `uv build` directly bypasses the dirty-tree gate; Surface 7: trap covers `EXIT INT TERM HUP` (not just normal exit); Surface 7: explicit wheel-content assertion catches the hatchling-include regression at build time; Surface 14 (new): wheel-integration smoke test that builds the wheel + asserts `_build_info.py` is inside; §Rollback path 3: explicit `git show HEAD~1:deploy/launchd/...` plist snapshot step + `uv sync` step for the source-checkout flow restoration. | Claude (via VOY-1811 #75) |
