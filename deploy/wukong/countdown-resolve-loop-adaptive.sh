#!/bin/zsh
# Adaptive scheduler for `vyg countdown resolve-loop` (issue #279).
#
# Self-scheduling daemon loop: after each run, sleep COUNTDOWN_FAST_INTERVAL
# when the run saw candidate threads (decision_count > 0), otherwise
# COUNTDOWN_SLOW_INTERVAL. A consecutive fast-streak cap bounds LLM-gate cost:
# the loop has no cross-run thread dedup, so a permanently-vetoed thread would
# otherwise keep the fast lane (and one DeepSeek gate call per candidate per
# run) open forever.
#
# Event-driven fast path (CHG-1841): every sleep is sliced into <=30s steps
# so the loop can wake early when the bridge's Countdown trigger route
# touches the trigger file (a Clearance RESOLVED-verdict reply). The trigger
# is consumed (deleted) immediately before each `vyg` invocation so one
# trigger yields at most one extra scan; a trigger that lands mid-scan has a
# newer mtime than the run start and survives to end the following sleep
# early. A trigger-fired run is otherwise indistinguishable from a
# timer-fired one, so it participates in fast/slow/streak accounting for
# free. Polling remains the delivery-loss fallback — the trigger only closes
# the gap between a webhook and the next lane wakeup.
#
# Deployment contract (VOY-1835):
#   - Template lives in the repo; copy to /Users/frank/.voyager/bin/ and
#     chmod 755 before use. Do not run from a development checkout.
#   - Sourced env file and repos allowlist are machine-local (chmod 600).
#   - Run under launchd with KeepAlive (see the plist template); the script
#     never exits on its own, so a disabled loop SLEEPS instead of exiting —
#     exiting under KeepAlive would crash-loop through launchd throttling.
#   - Single-instance safety comes from the loop's own lock file; a manual
#     `vyg countdown resolve-loop` run alongside this daemon is safe (one of
#     the two exits with AlreadyRunningError).

set -u

ENV_FILE="/Users/frank/.voyager/countdown-resolve-loop.env"
REPOS_FILE="/Users/frank/.voyager/countdown-resolve-loop.repos"
VYG="/Users/frank/.voyager/.venv/bin/vyg"
DEFAULT_TRIGGER_PATH="/Users/frank/.voyager/countdown-resolve-loop.trigger"
SLEEP_SLICE="${SLEEP_SLICE:-30}"

fast_streak=0

# trigger_path — resolve the trigger file path (COUNTDOWN_TRIGGER_PATH override
# or the default), matching the bridge route's own resolution.
trigger_path() {
  echo "${COUNTDOWN_TRIGGER_PATH:-$DEFAULT_TRIGGER_PATH}"
}

# consume_trigger — delete the trigger file so one arrival yields at most one
# extra scan (CHG-1841 D4). Called immediately before every `vyg` invocation.
consume_trigger() {
  rm -f "$(trigger_path)" 2>/dev/null
}

# trigger_newer_than <epoch_seconds> — true when the trigger file exists and
# its mtime is newer than the given epoch (the current run's start).
trigger_newer_than() {
  # NOTE: the local var is deliberately not named "path" — zsh ties that
  # name to $PATH, and shadowing it here empties PATH for the rest of this
  # function's scope (breaks `stat`).
  local since="$1" trigger_file mtime
  trigger_file="$(trigger_path)"
  [[ -f "$trigger_file" ]] || return 1
  mtime=$(stat -f %m "$trigger_file" 2>/dev/null) || return 1
  # >= not >: BSD `stat -f %m` is second-resolution, so a trigger touched in
  # the same second as run_start has mtime == since. Consume-before-run
  # already deletes any pre-existing marker, so equality here can only mean
  # a fresh touch, never a stale leftover re-firing.
  (( mtime >= since ))
}

# sliced_sleep <total_seconds> <run_start_epoch> — sleep in <=SLEEP_SLICE
# chunks, waking early the moment a trigger newer than run_start appears.
sliced_sleep() {
  local total="$1" since="$2" elapsed=0 remaining this_slice
  while (( elapsed < total )); do
    remaining=$(( total - elapsed ))
    this_slice=$(( remaining < SLEEP_SLICE ? remaining : SLEEP_SLICE ))
    sleep "$this_slice"
    elapsed=$(( elapsed + this_slice ))
    if trigger_newer_than "$since"; then
      echo "adaptive: trigger detected; ending sleep early"
      return 0
    fi
  done
}

while true; do
  run_start=$(date +%s)

  # Re-source every iteration so env edits apply without a launchd reload.
  # Fail closed on reload: clear the managed variables first, so a vanished,
  # unreadable, or truncated env file (or a deleted line) cannot leave a stale
  # kill switch, credential, or interval from a prior iteration — the daemon
  # keeps one long-lived shell, so leftovers would otherwise survive until a
  # launchd restart.
  unset COUNTDOWN_RESOLVE_LOOP_ENABLED COUNTDOWN_MAX_RESOLVES \
        COUNTDOWN_FAST_INTERVAL COUNTDOWN_SLOW_INTERVAL \
        COUNTDOWN_FAST_STREAK_MAX COUNTDOWN_TRIGGER_PATH \
        VOYAGER_DEEPSEEK_API_KEY VOYAGER_RESOLVE_EXTRA_REPOS
  set -a
  if ! source "$ENV_FILE" 2>/dev/null; then
    set +a
    # Env is untrusted here — use the hardcoded default, not a possibly
    # half-loaded knob.
    echo "adaptive: cannot source ${ENV_FILE}; failing closed, sleeping 3600s"
    fast_streak=0
    sliced_sleep 3600 "$run_start"
    continue
  fi
  set +a

  slow="${COUNTDOWN_SLOW_INTERVAL:-3600}"
  fast="${COUNTDOWN_FAST_INTERVAL:-300}"
  streak_max="${COUNTDOWN_FAST_STREAK_MAX:-6}"

  # Malformed knobs (non-numeric, zero, negative) would make `sleep` fail
  # instantly and turn the while-true loop into a busy loop / log storm.
  # Validate as integers; fall back to defaults loudly.
  if [[ "$slow" != <-> || "$slow" -eq 0 ]]; then
    echo "adaptive: invalid COUNTDOWN_SLOW_INTERVAL='${slow}'; using 3600"
    slow=3600
  fi
  if [[ "$fast" != <-> || "$fast" -eq 0 ]]; then
    echo "adaptive: invalid COUNTDOWN_FAST_INTERVAL='${fast}'; using 300"
    fast=300
  fi
  if [[ "$streak_max" != <-> ]]; then
    echo "adaptive: invalid COUNTDOWN_FAST_STREAK_MAX='${streak_max}'; using 6"
    streak_max=6
  fi

  if [[ "${COUNTDOWN_RESOLVE_LOOP_ENABLED:-false}" != "true" ]]; then
    echo "COUNTDOWN_RESOLVE_LOOP_ENABLED is not true; sleeping ${slow}s"
    fast_streak=0
    sliced_sleep "$slow" "$run_start"
    continue
  fi

  consume_trigger
  out=$("$VYG" countdown resolve-loop \
        --repos "$REPOS_FILE" \
        --max-resolves "${COUNTDOWN_MAX_RESOLVES:-20}" \
        --json)
  rc=$?
  # Pass the output through so launchd's log captures the same JSON lines
  # operators already grep (VOY-1835 §logs) — and, on failure, the CLI's
  # diagnostic (vyg reports errors via typer.echo on stdout). Never swallow it.
  echo "$out"

  if [[ "$rc" -ne 0 ]]; then
    # Real failures (auth, config, AlreadyRunningError) must look like
    # failures, not like quiet runs; take the slow lane and say why.
    echo "adaptive: vyg exited rc=${rc}; sleeping ${slow}s before retry"
    fast_streak=0
    sliced_sleep "$slow" "$run_start"
    continue
  fi

  decisions=$(printf '%s' "$out" | python3 -c \
    'import json,sys
try:
    print(int(json.load(sys.stdin).get("decision_count", 0)))
except Exception:
    print(0)' 2>/dev/null || echo 0)

  if [[ "$decisions" -gt 0 && "$fast_streak" -lt "$streak_max" ]]; then
    fast_streak=$((fast_streak + 1))
    echo "adaptive: ${decisions} decision(s); fast recheck in ${fast}s (streak ${fast_streak}/${streak_max})"
    sliced_sleep "$fast" "$run_start"
  else
    if [[ "$decisions" -gt 0 ]]; then
      echo "adaptive: fast-streak cap reached; backing off to ${slow}s"
    else
      echo "adaptive: idle; next check in ${slow}s"
    fi
    fast_streak=0
    sliced_sleep "$slow" "$run_start"
  fi
done
