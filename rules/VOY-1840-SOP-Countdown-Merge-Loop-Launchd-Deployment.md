# SOP-1840: Countdown Merge Loop Launchd Deployment

**Applies to:** VOY project
**Last updated:** 2026-08-08
**Last reviewed:** 2026-08-08
**Status:** Active
**Depends on:** VOY-1839
**Related:** VOY-1835, VOY-1831

---

## What Is It?

The Wukong operator runbook for deploying `vyg countdown merge-loop` from the
installed Voyager wheel and running it as a scheduled user-level `launchd` job.
The job enumerates allowlisted repositories, applies the merge-loop's
deterministic merge predicate (author allowlist, CI green, zero unresolved
threads, head-anchored clearance readiness), and rebase-merges only fully-green
agent PRs as the fixed GitHub user machine account `iterwheel-countdown-bot`.

## Why

The merge-loop is designed for unattended, zero-touch operation, but running it
by hand from a development checkout does not give operators a repeatable
schedule, rollback path, or audit location. This SOP makes the deployment
contract explicit: the wheel is the artifact, secrets stay out of the
repository, the repo allowlist is operator-owned, the target repo's GitHub
rulesets must be loosened deliberately before any live merge, a capped canary
gates full-rate operation, and every live merge is backed by the loop's JSONL
audit trail.

---

## When to Use

- Installing or updating the scheduled Countdown merge loop on Wukong.
- Preparing the private env file and repo allowlist consumed by the scheduled job.
- Applying the target-repo ruleset changes that let the machine account merge.
- Running the dry-run then capped-live canary before raising the merge cap.
- Inspecting Countdown merge-loop logs or audit records.
- Rolling the scheduled merge loop back to a prior installed Voyager wheel.

## When NOT to Use

- Running the FastAPI bridge service. Use VOY-1814 for bridge launchd operations.
- Running the review-thread resolve loop. Use VOY-1835 for resolve-loop launchd
  operations.
- Changing the merge-loop's safety model (the merge predicate, the author
  allowlist, ruleset expectations). Use VOY-1839 and code review.
- Adding repositories beyond the current operator-approved rollout (`fx_bin`
  only). File a staged rollout issue before expanding the repo allowlist.
- Storing or printing GitHub tokens, private PR numbers, or review thread node
  IDs in public docs, PRs, or issue comments.

---

## Rollout Gate

Per VOY-1839 §Rollout, the merge loop only reaches full-rate operation through
this five-step sequence — do not skip or reorder steps:

1. Implement behind `--dry-run`; run against fx_bin; operator reviews the
   `would_merge` audit output.
2. Operator applies the target-repo GitHub ruleset changes (below).
3. Enable live mode with `MERGE_MAX_MERGES=1`.
4. Canary: the next naturally-occurring agent PR runs the full pipeline
   (review → clearance → resolve → merge → release). Verify the merge author,
   rebase linearity, and that cd-release fired exactly once.
5. Raise cap to 3; leave fx_bin as the only allowlisted repo until the operator
   explicitly extends the allowlist.

Steps 5–8 below implement this sequence in detail.

### Target-repo GitHub Configuration

Required on `frankyxhl/fx_bin` before enabling live mode (one-time, operator-run
as `frankyxhl`; copied verbatim from VOY-1839 §Target-repo GitHub configuration):

| Ruleset | Change | Why |
|---------|--------|-----|
| `main-pr-gates` | `required_approving_review_count` 1 → 0 | Removes the human-approve gate |
| `protect main` | `require_code_owner_review` true → false | Same — bot cannot satisfy code-owner review |
| `main-pr-gates` | **Add** `required_status_checks` for the CI workflows, **with** `strict_required_status_checks_policy: true` ("Require branches to be up to date before merging") | Merge-time CI enforcement must live in GitHub, not only in the loop's predicate; the strict/up-to-date flag is REQUIRED, not optional — the loop's apply-time base re-read cannot eliminate the base-advance race (`mergePullRequest` has no `expectedBaseOid`), so this server-side gate is the only complete guarantee that merged commits were checked against the current base |
| `main-owner-merge-only` | Add `iterwheel-countdown-bot` to `bypass_actors` | The `update` rule otherwise blocks bot-initiated merges (canary-verify first; skip if the merge succeeds without it) |
| (keep) | `required_review_thread_resolution: true`, CodeQL gate | The remaining machine gates in zero-touch mode |

External-PR safety: after loosening, non-agent PRs still cannot self-merge —
merging requires write access, and the loop's author allowlist never selects
them. They stay open for manual handling.

---

## Steps

### 1. Confirm Repository Artifacts

| Path | Purpose |
|------|---------|
| `deploy/launchd/com.iterwheel.voyager.merge-loop.plist` | Repo-safe launchd template. `KeepAlive` daemon supervising the adaptive wrapper. |
| `deploy/wukong/merge-loop-adaptive.sh` | Adaptive scheduler wrapper: runs the loop, then sleeps `MERGE_FAST_INTERVAL` (default 300 s) while runs see decisions or `MERGE_SLOW_INTERVAL` (default 3600 s) when idle, with a `MERGE_FAST_STREAK_MAX` (default 6) consecutive-fast cap bounding re-check cost. Copy it locally before use. |
| `deploy/wukong/merge-loop.env.example` | Non-secret env-file template. Copy it locally before use. |
| `deploy/wukong/merge-loop.repos.example` | Non-secret repo allowlist template. Copy it locally before use. |
| `scripts/build_wheel.sh` | Builds the deployable Voyager wheel with build commit metadata. |

The wrapper sources `/Users/frank/.voyager/merge-loop.env` on every iteration
because launchd does not load dotenv files itself (and re-sourcing means env
edits apply without a launchd reload). The wrapper gates execution on
`MERGE_LOOP_ENABLED=true` — when disabled it sleeps instead of exiting, because
exiting under `KeepAlive` would crash-loop through launchd throttling. Merely
installing the LaunchAgent does not perform live merges.

There is no LLM gate in this loop: the merge predicate is fully deterministic
(author allowlist, CI rollup, unresolved-thread count, clearance readiness
marker). No DeepSeek key or clearance-model config is consumed here.

### 2. Prepare Private Wukong Files

These files are machine-local and must not be committed:

| Path | Contents | Required permissions |
|------|----------|----------------------|
| `/Users/frank/.voyager/merge-loop.env` | `MERGE_LOOP_ENABLED`, `MERGE_MAX_MERGES`, adaptive-interval knobs (`MERGE_FAST_INTERVAL`, `MERGE_SLOW_INTERVAL`, `MERGE_FAST_STREAK_MAX`), and `VOYAGER_MERGE_EXTRA_REPOS`. | `600` |
| `/Users/frank/.voyager/bin/merge-loop-adaptive.sh` | Installed copy of the adaptive wrapper (from `deploy/wukong/`). | `755` |
| `/Users/frank/.voyager/merge-loop.repos` | OWNER/REPO allowlist consumed by `vyg countdown merge-loop --repos`. | `600` |
| `/Users/frank/.voyager/merge-loop.audit.jsonl` | Redacted append-only merge-loop audit trail written by `merge_loop.py`. | file `600`, parent directory `700` preferred |
| `/Users/frank/.voyager/merge-loop.lock` | Single-instance lock file created by the loop. | parent directory `700` preferred |
| `/Users/frank/Library/LaunchAgents/com.iterwheel.voyager.merge-loop.plist` | Installed copy of the launchd plist. | `644` |
| `/Users/frank/Library/Logs/voyager/` | launchd stdout/stderr logs (shared directory with the resolve-loop; distinct `merge-loop.out.log` / `merge-loop.err.log` files). | directory `755` |

The GitHub token must stay in the `gh` credential store for the fixed machine
account — the same account and credential path the resolve loop uses. It must
not be copied into the env file:

```bash
gh auth token --hostname github.com --user iterwheel-countdown-bot >/dev/null
```

That command proves the credential path exists without printing the token. If
the resolve loop (VOY-1835) is already deployed on this machine, this
credential is already verified and this step is a no-op check.

### 3. Install or Update the Wheel

Same wheel and venv as VOY-1835 — the merge loop ships in the same Voyager
wheel and runs from the same `/Users/frank/.voyager/.venv`. If the resolve loop
is already deployed at the target version, this step is already done; verify
with `vyg version` and skip to Step 4.

```bash
cd /Users/frank/Projects/voyager
bash scripts/build_wheel.sh

uv venv /Users/frank/.voyager/.venv-vX.Y.Z
uv pip install --python /Users/frank/.voyager/.venv-vX.Y.Z/bin/python \
  dist/iterwheel_voyager-X.Y.Z-py3-none-any.whl

ln -s /Users/frank/.voyager/.venv-vX.Y.Z /Users/frank/.voyager/.venv.swap-$$
mv -hf /Users/frank/.voyager/.venv.swap-$$ /Users/frank/.voyager/.venv

/Users/frank/.voyager/.venv/bin/vyg version
```

The `mv -hf` command is load-bearing on macOS: it swaps the symlink itself and
does not follow the existing `.venv` target.

### 4. Install Private Merge-Loop Files

```bash
cd /Users/frank/Projects/voyager

install -d -m 700 /Users/frank/.voyager
install -d -m 755 /Users/frank/Library/Logs/voyager
install -d -m 755 /Users/frank/Library/LaunchAgents

if [[ ! -f /Users/frank/.voyager/merge-loop.env ]]; then
  install -m 600 deploy/wukong/merge-loop.env.example \
    /Users/frank/.voyager/merge-loop.env
else
  install -m 600 /Users/frank/.voyager/merge-loop.env \
    "/Users/frank/.voyager/merge-loop.env.backup.$(date -u +%Y%m%dT%H%M%SZ)"
fi

if [[ ! -f /Users/frank/.voyager/merge-loop.repos ]]; then
  install -m 600 deploy/wukong/merge-loop.repos.example \
    /Users/frank/.voyager/merge-loop.repos
else
  install -m 600 /Users/frank/.voyager/merge-loop.repos \
    "/Users/frank/.voyager/merge-loop.repos.backup.$(date -u +%Y%m%dT%H%M%SZ)"
fi
```

Install the adaptive wrapper (always overwrite — it is code, not operator
state; local edits belong in the repo template):

```bash
install -d -m 700 /Users/frank/.voyager/bin
install -m 755 deploy/wukong/merge-loop-adaptive.sh \
  /Users/frank/.voyager/bin/merge-loop-adaptive.sh
```

Edit the private env file locally. Keep `MERGE_LOOP_ENABLED=false` until Step 7.
The example repo allowlist ships with `iterwheel/voyager-sandbox` only, and the
built-in merge ceiling (`MERGE_ALLOWED_REPOS`) is sandbox-only — `fx_bin` is
never scannable by default. Authorize it now, before Step 5's dry-run, or
`gate_repos` ceiling-skips it and the dry-run silently proves nothing:

- Add `VOYAGER_MERGE_EXTRA_REPOS=frankyxhl/fx_bin` to
  `/Users/frank/.voyager/merge-loop.env`.
- Add `frankyxhl/fx_bin` to `/Users/frank/.voyager/merge-loop.repos`.

This only authorizes the repo to be *scanned*; it does not enable live merges.
Live mutations on `fx_bin` still wait on Steps 5 and 6 passing (Rollout Gate
steps 1–2) before Step 7 flips `MERGE_LOOP_ENABLED=true`.

### 5. Run the Dry-Run Gate

Confirm Step 4's `fx_bin` authorization landed (`VOYAGER_MERGE_EXTRA_REPOS` in
`merge-loop.env` and the `merge-loop.repos` line) — without it this dry-run
scans `iterwheel/voyager-sandbox` only and never touches `fx_bin`.

Verify the fixed machine account credential path:

```bash
gh auth token --hostname github.com --user iterwheel-countdown-bot >/dev/null
```

Run the merge loop in dry-run mode. This command invokes `vyg` directly (not
through the adaptive wrapper), so it must source the env file itself first —
otherwise `VOYAGER_MERGE_EXTRA_REPOS` never reaches the process,
`merge_allowed_repos()` ceiling-skips `fx_bin`, and the dry-run silently
scans `iterwheel/voyager-sandbox` only, passing vacuously:

```bash
set -a
if source /Users/frank/.voyager/merge-loop.env; then
  set +a
  /Users/frank/.voyager/.venv/bin/vyg countdown merge-loop \
    --repos /Users/frank/.voyager/merge-loop.repos \
    --dry-run \
    --json
else
  set +a
  echo "FAIL-CLOSED: cannot source merge-loop.env — fix the env file first" >&2
  false
fi
```

One-line equivalent:

```bash
set -a; if source /Users/frank/.voyager/merge-loop.env; then set +a; /Users/frank/.voyager/.venv/bin/vyg countdown merge-loop --repos /Users/frank/.voyager/merge-loop.repos --dry-run --json; else set +a; echo "FAIL-CLOSED: cannot source merge-loop.env — fix the env file first" >&2; false; fi
```

A missing, unreadable, or broken env file must abort the dry-run (mirroring
the adaptive wrapper's fail-closed source guard) — running `vyg` anyway would
leave `VOYAGER_MERGE_EXTRA_REPOS` unset and the gate would pass vacuously
against the sandbox only. The trailing `false` makes the block's exit status
nonzero on sourcing failure, so preflight scripts and `set -e` callers must
treat it as a failed gate rather than a silent no-op.

The dry-run must not write merge mutations. Treat any systemic failure,
credential error, predicate error, unexpected repository skip, or surprising
`would_merge` count as a HOLD until inspected. This satisfies Rollout Gate
step 1 — operator review of the `would_merge` audit output.

### 6. Apply Target-repo GitHub Configuration

Rollout Gate step 2. Apply the ruleset changes from the table above to
`frankyxhl/fx_bin`, authenticated as `frankyxhl` (not the bot account). Confirm
each change lands (e.g. `gh api repos/frankyxhl/fx_bin/rulesets` or the repo
Settings → Rulesets UI) before proceeding. Do not enable live mode until every
row in the table is applied.

### 7. Enable Live Mode with Cap 1 and Install the Schedule

Rollout Gate step 3. Edit the private env file:

```bash
MERGE_LOOP_ENABLED=true
MERGE_MAX_MERGES=1
```

Install and start the LaunchAgent:

```bash
cd /Users/frank/Projects/voyager

plutil -lint deploy/launchd/com.iterwheel.voyager.merge-loop.plist
install -m 644 deploy/launchd/com.iterwheel.voyager.merge-loop.plist \
  /Users/frank/Library/LaunchAgents/com.iterwheel.voyager.merge-loop.plist

launchctl bootstrap gui/$(id -u) \
  /Users/frank/Library/LaunchAgents/com.iterwheel.voyager.merge-loop.plist
launchctl enable gui/$(id -u)/com.iterwheel.voyager.merge-loop
launchctl kickstart -kp gui/$(id -u)/com.iterwheel.voyager.merge-loop
```

The checked-in plist starts the adaptive wrapper at load and keeps it alive as
a daemon. The wrapper owns the cadence: `MERGE_SLOW_INTERVAL` (default 3600 s)
between runs while idle, dropping to `MERGE_FAST_INTERVAL` (default 300 s)
while runs see decisions, for at most `MERGE_FAST_STREAK_MAX` (default 6)
consecutive fast rechecks. The loop's own flock prevents overlapping
executions, including against manual runs.

### 8. Verify the Canary and Raise the Cap

Rollout Gate steps 4 and 5. Wait for the next naturally-occurring agent PR
(author `ryosaeba1985`) to clear CI, threads, and clearance on `fx_bin`. When
the scheduled job merges it, inspect the audit trail:

```bash
tail -n 20 /Users/frank/.voyager/merge-loop.audit.jsonl
```

Verify:

- The merge author is `iterwheel-countdown-bot`.
- The merge is a rebase merge (linear history, no merge commit).
- `cd-release` fired exactly once for the merge.
- No other PR was touched in the same run beyond the cap.

Only after this canary is confirmed, raise the cap:

```bash
MERGE_MAX_MERGES=3
```

The wrapper re-sources the env file every iteration, so the new cap applies at
the next wake without a launchd reload; `launchctl kickstart -kp` forces it
immediately. Leave `fx_bin` as the only allowlisted repo until the operator
explicitly extends `/Users/frank/.voyager/merge-loop.repos`.

### 9. Operate the Scheduled Job

Stop:

```bash
launchctl bootout gui/$(id -u) \
  /Users/frank/Library/LaunchAgents/com.iterwheel.voyager.merge-loop.plist
```

Restart immediately:

```bash
launchctl kickstart -kp gui/$(id -u)/com.iterwheel.voyager.merge-loop
```

Status:

```bash
launchctl print gui/$(id -u)/com.iterwheel.voyager.merge-loop
```

Logs:

```bash
tail -n 100 -F /Users/frank/Library/Logs/voyager/merge-loop.out.log
tail -n 100 -F /Users/frank/Library/Logs/voyager/merge-loop.err.log
```

Audit:

```bash
tail -n 100 -F /Users/frank/.voyager/merge-loop.audit.jsonl
```

### 10. Roll Back

Fastest stop:

```bash
launchctl bootout gui/$(id -u) \
  /Users/frank/Library/LaunchAgents/com.iterwheel.voyager.merge-loop.plist
```

Preferred artifact rollback uses the same venv-swap pattern as VOY-1814 /
VOY-1835:

```bash
ln -s /Users/frank/.voyager/.venv-vX.Y.Z-prior /Users/frank/.voyager/.venv.swap-$$
mv -hf /Users/frank/.voyager/.venv.swap-$$ /Users/frank/.voyager/.venv
launchctl kickstart -kp gui/$(id -u)/com.iterwheel.voyager.merge-loop
/Users/frank/.voyager/.venv/bin/vyg version
```

If a safety concern is repo-specific, remove that repository from
`/Users/frank/.voyager/merge-loop.repos` or set `MERGE_LOOP_ENABLED=false`. The
wrapper re-sources the env file every iteration, so the kill switch takes
effect at the next wake without a launchd reload; `launchctl kickstart -kp`
forces it immediately. A merge is final and this loop has no disarm/rollback
logic for GitHub state — rolling back stops future merges, it does not undo a
completed one. If the ruleset drift risk materializes (operator re-adds an
approve requirement), merges fail closed with `merge_failed` audit entries and
no retries — safe, visible, no rollback action required beyond investigating
the audit log.

---

## Verification

Before declaring the scheduled deployment complete, record:

- `plutil -lint deploy/launchd/com.iterwheel.voyager.merge-loop.plist` passes.
- `gh auth token --hostname github.com --user iterwheel-countdown-bot >/dev/null` passes.
- Every row of the Target-repo GitHub Configuration table is applied on
  `frankyxhl/fx_bin`.
- Foreground dry-run passes with expected `would_merge` and no mutations.
- The live canary at `MERGE_MAX_MERGES=1` produced exactly one merge with the
  expected author, rebase linearity, and a single `cd-release` firing.
- `/Users/frank/.voyager/merge-loop.audit.jsonl` is present and inspectable.
- `launchctl print gui/$(id -u)/com.iterwheel.voyager.merge-loop` shows the job.
- `tail` of `merge-loop.err.log` shows no startup loop or credential error.

## Pitfalls

- This loop has no LLM gate and no human terminal gate — the deterministic
  predicate plus GitHub's merge-time ruleset enforcement are the only safety
  layers. Do not treat this SOP as adding a review step; it does not have one.
- The author allowlist is fixed to `ryosaeba1985`. The loop never touches PRs
  by any other author, including the repo owner. Do not "fix" a stuck non-agent
  PR by widening the allowlist.
- Do not set `MERGE_LOOP_ENABLED=true` before the dry-run gate (Step 5) and the
  target-repo ruleset changes (Step 6) both pass.
- Do not set `MERGE_MAX_MERGES` above `1` before the canary (Step 8) is
  confirmed.
- launchd does not expand `~`; use absolute paths in the plist and commands.
- launchd does not parse dotenv files. The plist uses zsh to source the env file.
- Do not add private repositories or private canary identifiers to public docs
  or PR text.
- Do not bypass `scripts/build_wheel.sh`; direct `uv build` can miss build
  commit metadata.
- The readiness scan pages through ALL PR comments (`first:100` per page,
  paged to exhaustion), not a fixed recent-comments window. If the
  clearance readiness comment was never posted, or was posted for a stale
  head SHA, the PR is skipped fail-closed as `readiness_missing` (or
  `readiness_stale_head`) until clearance re-posts for the current head.
- An apply-time race (`expectedHeadOid` mismatch) records `merge_failed` and
  still consumes a cap slot (attempt-counting), so a canary run at
  `MERGE_MAX_MERGES=1` can be consumed entirely by a race; re-run or wait for
  the next cycle rather than assuming the cap means nothing merged.
- At most one PR merges per repo per cycle: once a merge succeeds, every
  other green PR in that repo is deferred to the next cycle as
  `base_moved_by_merge` (its cached `base_behind` was read before main
  moved). Batch landings across several agent PRs in one repo take one
  cycle per PR by design — this is not a bug or a stuck loop.
- The loop skips green PRs whose base advanced after their checks ran
  (`base_stale`) until the PR is rebased/re-checked — `expectedHeadOid` only
  guards the PR head, not the base. GitHub's "require branches up to date
  before merging" (strict required checks) is a REQUIRED entry in the
  Target-repo GitHub Configuration table above, not an optional extra.
- The live path re-reads base freshness immediately before merging
  (`base_stale_at_apply`), narrowing but not eliminating the base-advance
  race — the merge mutation has no `expectedBaseOid` — which is exactly why
  the strict up-to-date requirement above is a mandatory pre-live gate: with
  it enabled, GitHub itself refuses a merge whose head was not checked
  against the current base.

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-08-08 | Initial version | Claude Code |
| 2026-08-08 | Add pitfalls: readiness-comment window (last 50), apply-time race consumes cap slot | Claude Code |
| 2026-08-08 | Step 5 dry-run now sources `merge-loop.env` before invoking `vyg` directly, so operator-set `VOYAGER_MERGE_EXTRA_REPOS` reaches the process instead of ceiling-skipping `fx_bin`; corrected the readiness pitfall to describe paged-to-exhaustion comment reads (not a last-50 window), matching 14d2e9e | Claude Code |
| 2026-08-08 | Add pitfall: `base_stale` skip when main advances after checks ran; recommend GitHub's "require branches up to date" required check as server-side defense in depth (Codex round-4 review) | Claude Code |
| 2026-08-08 | Add pitfall: at most one PR merges per repo per cycle, rest deferred as `base_moved_by_merge` (Codex round-5 review) | Claude Code |
| 2026-08-08 | Step 5 dry-run snippets fail closed when `merge-loop.env` cannot be sourced — `vyg` no longer runs with an unset ceiling on a broken env file (Codex round-6 review) | Claude Code |
| 2026-08-08 | Step 5 dry-run snippets' else branch now ends in `false` (both multi-line and one-line forms) so a sourcing failure exits nonzero instead of 0, matching the fail-closed intent for preflight/`set -e` callers (Codex round-7 review) | Claude Code |
| 2026-08-08 | Add pitfall: live path re-reads base freshness immediately before merging (`base_stale_at_apply`), narrowing but not eliminating the base-advance race since the merge mutation has no `expectedBaseOid` (Codex round-9 review) | Claude Code |
| 2026-08-08 | Promote "Require branches to be up to date before merging" (`strict_required_status_checks_policy: true`) from optional pitfall recommendation to REQUIRED entry in the Target-repo GitHub Configuration table (Codex round-10 review) | Claude Code |
