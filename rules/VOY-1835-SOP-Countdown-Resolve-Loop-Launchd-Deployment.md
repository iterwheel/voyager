# SOP-1835: Countdown Resolve Loop Launchd Deployment

**Applies to:** VOY project
**Last updated:** 2026-08-12
**Last reviewed:** 2026-08-12
**Status:** Active
**Related:** VOY-1814, VOY-1820, VOY-1831, VOY-1832, VOY-1838, issue #229, issue #226

---

## What Is It?

The Wukong operator runbook for deploying `vyg countdown resolve-loop` from the
installed Voyager wheel and running it as a scheduled user-level `launchd` job.
The job enumerates allowlisted repositories, applies the Countdown deterministic
prefilter and fail-closed LLM gate, and resolves only approved review threads as
the fixed GitHub user machine account `iterwheel-countdown-bot`.

## Why

Countdown's resolve loop is designed for unattended operation, but running it by
hand from a development checkout does not give operators a repeatable schedule,
rollback path, or audit location. This SOP makes the deployment contract explicit:
the wheel is the artifact, secrets stay out of the repository, the repo allowlist
is operator-owned, dry-run evidence gates live scheduling, and every live resolve
is backed by the loop's JSONL audit trail.

---

## When to Use

- Installing or updating the scheduled Countdown resolve loop on Wukong.
- Preparing the private env file and repo allowlist consumed by the scheduled job.
- Running the dry-run then live preflight before enabling the schedule.
- Inspecting Countdown resolve-loop logs or audit records.
- Rolling the scheduled resolve loop back to a prior installed Voyager wheel.

## When NOT to Use

- Running the FastAPI bridge service. Use VOY-1814 for bridge launchd operations.
- Changing the Countdown resolve-loop safety model. Use VOY-1831 and code review.
- Adding repositories beyond the current operator-approved rollout. File a staged
  rollout issue before expanding the repo allowlist.
- Storing or printing GitHub tokens, DeepSeek keys, private PR numbers, or review
  thread node IDs in public docs, PRs, or issue comments.

## Steps

### 1. Confirm Repository Artifacts

| Path | Purpose |
|------|---------|
| `deploy/launchd/com.iterwheel.voyager.countdown-resolve-loop.plist` | Repo-safe launchd template. `KeepAlive` daemon supervising the adaptive wrapper (issue #279). |
| `deploy/wukong/countdown-resolve-loop-adaptive.sh` | Adaptive scheduler wrapper: runs the loop, then sleeps `COUNTDOWN_FAST_INTERVAL` (default 300 s) while runs see candidate threads or `COUNTDOWN_SLOW_INTERVAL` (default 3600 s) when idle, with a `COUNTDOWN_FAST_STREAK_MAX` (default 6) consecutive-fast cap bounding LLM-gate cost. Since CHG-1838 every sleep is sliced (≤30 s) so a bridge-touched trigger file wakes the loop early; the trigger is consumed before each run. Copy it locally before use. |
| `deploy/wukong/countdown-resolve-loop.env.example` | Non-secret env-file template. Copy it locally before use. |
| `deploy/wukong/countdown-resolve-loop.repos.example` | Non-secret repo allowlist template. Copy it locally before use. |
| `scripts/build_wheel.sh` | Builds the deployable Voyager wheel with build commit metadata. |

The wrapper sources `/Users/frank/.voyager/countdown-resolve-loop.env` on every
iteration because launchd does not load dotenv files itself (and re-sourcing
means env edits apply without a launchd reload). The wrapper gates execution on
`COUNTDOWN_RESOLVE_LOOP_ENABLED=true` — when disabled it sleeps instead of
exiting, because exiting under `KeepAlive` would crash-loop through launchd
throttling. Merely installing the LaunchAgent does not perform live resolves.

### 2. Prepare Private Wukong Files

These files are machine-local and must not be committed:

| Path | Contents | Required permissions |
|------|----------|----------------------|
| `/Users/frank/.voyager/countdown-resolve-loop.env` | `COUNTDOWN_RESOLVE_LOOP_ENABLED`, `COUNTDOWN_MAX_RESOLVES`, `VOYAGER_DEEPSEEK_API_KEY`, adaptive-interval knobs, and non-secret runtime knobs. | `600` |
| `/Users/frank/.voyager/bin/countdown-resolve-loop-adaptive.sh` | Installed copy of the adaptive wrapper (from `deploy/wukong/`). | `755` |
| `/Users/frank/.voyager/countdown-resolve-loop.repos` | OWNER/REPO allowlist consumed by `vyg countdown resolve-loop --repos`. | `600` |
| `/Users/frank/.voyager/countdown-resolve-loop.audit.jsonl` | Redacted append-only resolve-loop audit trail written by `countdown_loop.py`. | file `600`, parent directory `700` preferred |
| `/Users/frank/.voyager/countdown-resolve-loop.lock` | Single-instance lock file created by the loop. | parent directory `700` preferred |
| `/Users/frank/.voyager/countdown-resolve-loop.trigger` | Transient event-trigger marker (CHG-1838): touched by the bridge's Countdown route on a Clearance RESOLVED verdict reply, consumed (deleted) by the wrapper before each run. Carries no data; absent most of the time; safe to delete at any point (polling lanes are unaffected). Path override: `COUNTDOWN_TRIGGER_PATH` — if set, it must be identical in this env file and in `bridge.env`, or the bridge and wrapper silently split into producer/consumer of different files. | created with default perms; parent directory `700` preferred |
| `/Users/frank/Library/LaunchAgents/com.iterwheel.voyager.countdown-resolve-loop.plist` | Installed copy of the launchd plist. | `644` |
| `/Users/frank/Library/Logs/voyager/` | launchd stdout/stderr logs. | directory `755` |

The GitHub token must stay in the `gh` credential store for the fixed machine
account. It must not be copied into the env file:

```bash
gh auth token --hostname github.com --user iterwheel-countdown-bot >/dev/null
```

That command proves the credential path exists without printing the token.

### 3. Install or Update the Wheel

Follow the wheel installation pattern from VOY-1814:

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

### 4. Install Private Countdown Files

```bash
cd /Users/frank/Projects/voyager

install -d -m 700 /Users/frank/.voyager
install -d -m 755 /Users/frank/Library/Logs/voyager
install -d -m 755 /Users/frank/Library/LaunchAgents

if [[ ! -f /Users/frank/.voyager/countdown-resolve-loop.env ]]; then
  install -m 600 deploy/wukong/countdown-resolve-loop.env.example \
    /Users/frank/.voyager/countdown-resolve-loop.env
else
  install -m 600 /Users/frank/.voyager/countdown-resolve-loop.env \
    "/Users/frank/.voyager/countdown-resolve-loop.env.backup.$(date -u +%Y%m%dT%H%M%SZ)"
fi

if [[ ! -f /Users/frank/.voyager/countdown-resolve-loop.repos ]]; then
  install -m 600 deploy/wukong/countdown-resolve-loop.repos.example \
    /Users/frank/.voyager/countdown-resolve-loop.repos
else
  install -m 600 /Users/frank/.voyager/countdown-resolve-loop.repos \
    "/Users/frank/.voyager/countdown-resolve-loop.repos.backup.$(date -u +%Y%m%dT%H%M%SZ)"
fi
```

Install the adaptive wrapper (always overwrite — it is code, not operator
state; local edits belong in the repo template):

```bash
install -d -m 700 /Users/frank/.voyager/bin
install -m 755 deploy/wukong/countdown-resolve-loop-adaptive.sh \
  /Users/frank/.voyager/bin/countdown-resolve-loop-adaptive.sh
```

Edit the private env file locally. Keep `COUNTDOWN_RESOLVE_LOOP_ENABLED=false`
until Step 5 passes.

### 5. Run Credential and Dry-Run Gates

Verify the fixed machine account credential path:

```bash
gh auth token --hostname github.com --user iterwheel-countdown-bot >/dev/null
```

Verify the DeepSeek key is available without printing it:

```bash
set -a
source /Users/frank/.voyager/countdown-resolve-loop.env
set +a
test -n "${VOYAGER_DEEPSEEK_API_KEY:-}"
test "${VOYAGER_DEEPSEEK_API_KEY:-}" != "replace-with-deepseek-api-key"
```

Run the resolve loop in dry-run mode:

```bash
/Users/frank/.voyager/.venv/bin/vyg countdown resolve-loop \
  --repos /Users/frank/.voyager/countdown-resolve-loop.repos \
  --dry-run \
  --json
```

One-line equivalent:

```bash
/Users/frank/.voyager/.venv/bin/vyg countdown resolve-loop --repos /Users/frank/.voyager/countdown-resolve-loop.repos --dry-run --json
```

The dry-run must not write resolve mutations. Treat any systemic failure,
credential error, gate error, unexpected repository skip, or surprising
`would_resolve` count as a HOLD until inspected.

### 6. Run One Live Preflight

After the dry-run output is understood and approved, run one foreground live pass:

```bash
/Users/frank/.voyager/.venv/bin/vyg countdown resolve-loop \
  --repos /Users/frank/.voyager/countdown-resolve-loop.repos \
  --json
```

One-line equivalent:

```bash
/Users/frank/.voyager/.venv/bin/vyg countdown resolve-loop --repos /Users/frank/.voyager/countdown-resolve-loop.repos --json
```

Then inspect the audit trail:

```bash
tail -n 20 /Users/frank/.voyager/countdown-resolve-loop.audit.jsonl
```

For non-sandbox repos, public output and audit records must stay redacted. If the
audit file cannot be written, the loop fails closed before mutating GitHub state.

### 7. Enable and Install the Schedule

Only after Steps 5 and 6 pass, edit the private env file:

```bash
COUNTDOWN_RESOLVE_LOOP_ENABLED=true
```

Install and start the LaunchAgent:

```bash
cd /Users/frank/Projects/voyager

plutil -lint deploy/launchd/com.iterwheel.voyager.countdown-resolve-loop.plist
install -m 644 deploy/launchd/com.iterwheel.voyager.countdown-resolve-loop.plist \
  /Users/frank/Library/LaunchAgents/com.iterwheel.voyager.countdown-resolve-loop.plist

launchctl bootstrap gui/$(id -u) \
  /Users/frank/Library/LaunchAgents/com.iterwheel.voyager.countdown-resolve-loop.plist
launchctl enable gui/$(id -u)/com.iterwheel.voyager.countdown-resolve-loop
launchctl kickstart -kp gui/$(id -u)/com.iterwheel.voyager.countdown-resolve-loop
```

The checked-in plist starts the adaptive wrapper at load and keeps it alive as
a daemon. The wrapper owns the cadence: `COUNTDOWN_SLOW_INTERVAL` (default
3600 s) between runs while idle, dropping to `COUNTDOWN_FAST_INTERVAL` (default
300 s) while runs see candidate threads, for at most
`COUNTDOWN_FAST_STREAK_MAX` (default 6) consecutive fast rechecks. The loop's
own flock prevents overlapping executions, including against manual runs.

**Event-driven trigger (CHG-1838).** The wrapper additionally wakes early —
within one ≤30 s sleep slice — when the trigger file's mtime is newer than the
current run's start. The producer is the bridge's Countdown webhook route,
which fires on a Clearance RESOLVED verdict reply. This path only works when
the **bridge** deployment allow-lists the Countdown agent:

```
BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_COUNTDOWN=<owner/repo,...>
```

in the machine-local `bridge.env` (see `deploy/wukong/bridge.env.example`).
Without that entry the server gate default-denies in production
(`DRY_RUN=false`) and Clearance RESOLVED replies never touch the trigger —
the wrapper still functions on its polling lanes, so the omission is silent.
Keep the entry in sync with the Clearance allow-list (the trigger only fires
where Clearance posts verdicts) and expand it via staged rollout, matching the
repos allowlist discipline above. Current scope: canary, `iterwheel/voyager`.

### 8. Operate the Scheduled Job

Stop:

```bash
launchctl bootout gui/$(id -u) \
  /Users/frank/Library/LaunchAgents/com.iterwheel.voyager.countdown-resolve-loop.plist
```

Restart immediately:

```bash
launchctl kickstart -kp gui/$(id -u)/com.iterwheel.voyager.countdown-resolve-loop
```

Status:

```bash
launchctl print gui/$(id -u)/com.iterwheel.voyager.countdown-resolve-loop
```

Logs:

```bash
tail -n 100 -F /Users/frank/Library/Logs/voyager/countdown-resolve-loop.out.log
tail -n 100 -F /Users/frank/Library/Logs/voyager/countdown-resolve-loop.err.log
```

Audit:

```bash
tail -n 100 -F /Users/frank/.voyager/countdown-resolve-loop.audit.jsonl
```

### 9. Roll Back

Fastest stop:

```bash
launchctl bootout gui/$(id -u) \
  /Users/frank/Library/LaunchAgents/com.iterwheel.voyager.countdown-resolve-loop.plist
```

Preferred artifact rollback uses the same venv-swap pattern as VOY-1814:

```bash
ln -s /Users/frank/.voyager/.venv-vX.Y.Z-prior /Users/frank/.voyager/.venv.swap-$$
mv -hf /Users/frank/.voyager/.venv.swap-$$ /Users/frank/.voyager/.venv
launchctl kickstart -kp gui/$(id -u)/com.iterwheel.voyager.countdown-resolve-loop
/Users/frank/.voyager/.venv/bin/vyg version
```

If a safety concern is repo-specific, remove that repository from
`/Users/frank/.voyager/countdown-resolve-loop.repos` or set
`COUNTDOWN_RESOLVE_LOOP_ENABLED=false`. The wrapper re-sources the env file
every iteration, so the kill switch takes effect at the next wake without a
launchd reload; `launchctl kickstart -kp` forces it immediately.

## Verification

Before declaring the scheduled deployment complete, record:

- `plutil -lint deploy/launchd/com.iterwheel.voyager.countdown-resolve-loop.plist` passes.
- `gh auth token --hostname github.com --user iterwheel-countdown-bot >/dev/null` passes.
- The private env file contains `VOYAGER_DEEPSEEK_API_KEY` and does not print it.
- Foreground dry-run passes with expected `would_resolve` and no mutations.
- Foreground live run passes or intentionally records zero decisions.
- `/Users/frank/.voyager/countdown-resolve-loop.audit.jsonl` is present and inspectable.
- `launchctl print gui/$(id -u)/com.iterwheel.voyager.countdown-resolve-loop` shows the job.
- `tail` of `countdown-resolve-loop.err.log` shows no startup loop or credential error.
- Trigger wake (CHG-1838): while the daemon is sleeping,
  `touch /Users/frank/.voyager/countdown-resolve-loop.trigger`; within ~30 s
  `countdown-resolve-loop.out.log` shows `adaptive: trigger detected; ending
  sleep early`, a new run starts, and the trigger file is gone (consumed).
  If the bridge is also being deployed, verify end-to-end instead: a Clearance
  RESOLVED reply on an allow-listed repository updates the trigger file's
  mtime.

## Pitfalls

- Issue #229 originally named `iterwheel-countdown-user`; issue #226 renamed the
  fixed machine account to `iterwheel-countdown-bot`. Use the new login.
- launchd does not expand `~`; use absolute paths in the plist and commands.
- launchd does not parse dotenv files. The plist uses zsh to source the env file.
- Do not set `COUNTDOWN_RESOLVE_LOOP_ENABLED=true` before the dry-run and live
  foreground gates pass.
- Do not add private repositories or private canary identifiers to public docs or
  PR text.
- Do not bypass `scripts/build_wheel.sh`; direct `uv build` can miss build commit
  metadata.
- The CHG-1838 trigger path fails silently when
  `BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_COUNTDOWN` is missing from the live
  `bridge.env` — polling masks the gap, so verify the trigger wake explicitly
  after any bridge redeploy.
- If `COUNTDOWN_TRIGGER_PATH` is overridden on one side only (bridge vs
  wrapper env), the two silently use different files; leave both unset unless
  a non-default path is genuinely required, and then set both.

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-08-12 | CHG-1838 (deferred Surface 6): documented the event-driven trigger — trigger-file row in §2, sliced-sleep note in §1, bridge allow-list requirement (`BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_COUNTDOWN`) and trigger semantics in §7, trigger-wake check in Verification, silent-failure and split-path pitfalls. | Claude Code |
| 2026-07-04 | Issue #279: switched the launchd job to a KeepAlive daemon running the adaptive wrapper (`deploy/wukong/countdown-resolve-loop-adaptive.sh`) — fast rechecks (default 300 s, capped at 6 consecutive) while runs see candidate threads, slow cadence (default 3600 s) when idle; wrapper install step, private-file row, operate/rollback notes updated. | Claude Code |
| 2026-06-28 | Initial Countdown resolve-loop launchd deployment SOP for issue #229 | Codex |
