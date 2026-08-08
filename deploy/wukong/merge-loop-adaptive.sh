#!/bin/zsh
# Adaptive scheduler for `vyg countdown merge-loop` (VOY-1839).
#
# Self-scheduling daemon loop: after each run, sleep MERGE_FAST_INTERVAL
# when the run saw candidate threads (decision_count > 0), otherwise
# MERGE_SLOW_INTERVAL. A consecutive fast-streak cap bounds re-check cost:
# the loop has no cross-run thread dedup, so a permanently-blocked thread
# would otherwise keep the fast lane open forever.
#
# Deployment contract (VOY-1839):
#   - Template lives in the repo; copy to /Users/frank/.voyager/bin/ and
#     chmod 755 before use. Do not run from a development checkout.
#   - Sourced env file and repos allowlist are machine-local (chmod 600).
#   - Run under launchd with KeepAlive (see the plist template); the script
#     never exits on its own, so a disabled loop SLEEPS instead of exiting —
#     exiting under KeepAlive would crash-loop through launchd throttling.
#   - Single-instance safety comes from the loop's own lock file; a manual
#     `vyg countdown merge-loop` run alongside this daemon is safe (one of
#     the two exits with AlreadyRunningError).

set -u

ENV_FILE="/Users/frank/.voyager/merge-loop.env"
REPOS_FILE="/Users/frank/.voyager/merge-loop.repos"
VYG="/Users/frank/.voyager/.venv/bin/vyg"

fast_streak=0

while true; do
  # Re-source every iteration so env edits apply without a launchd reload.
  # Fail closed on reload: clear the managed variables first, so a vanished,
  # unreadable, or truncated env file (or a deleted line) cannot leave a stale
  # kill switch or interval from a prior iteration — the daemon keeps one
  # long-lived shell, so leftovers would otherwise survive until a launchd
  # restart.
  unset MERGE_LOOP_ENABLED MERGE_MAX_MERGES \
        MERGE_FAST_INTERVAL MERGE_SLOW_INTERVAL \
        MERGE_FAST_STREAK_MAX VOYAGER_MERGE_EXTRA_REPOS \
        VOYAGER_MERGE_EXTRA_AUTHORS
  set -a
  if ! source "$ENV_FILE" 2>/dev/null; then
    set +a
    # Env is untrusted here — use the hardcoded default, not a possibly
    # half-loaded knob.
    echo "adaptive: cannot source ${ENV_FILE}; failing closed, sleeping 3600s"
    fast_streak=0
    sleep 3600
    continue
  fi
  set +a

  slow="${MERGE_SLOW_INTERVAL:-3600}"
  fast="${MERGE_FAST_INTERVAL:-300}"
  streak_max="${MERGE_FAST_STREAK_MAX:-6}"

  # Malformed knobs (non-numeric, zero, negative) would make `sleep` fail
  # instantly and turn the while-true loop into a busy loop / log storm.
  # Validate as integers; fall back to defaults loudly.
  if [[ "$slow" != <-> || "$slow" -eq 0 ]]; then
    echo "adaptive: invalid MERGE_SLOW_INTERVAL='${slow}'; using 3600"
    slow=3600
  fi
  if [[ "$fast" != <-> || "$fast" -eq 0 ]]; then
    echo "adaptive: invalid MERGE_FAST_INTERVAL='${fast}'; using 300"
    fast=300
  fi
  if [[ "$streak_max" != <-> ]]; then
    echo "adaptive: invalid MERGE_FAST_STREAK_MAX='${streak_max}'; using 6"
    streak_max=6
  fi

  if [[ "${MERGE_LOOP_ENABLED:-false}" != "true" ]]; then
    echo "MERGE_LOOP_ENABLED is not true; sleeping ${slow}s"
    fast_streak=0
    sleep "$slow"
    continue
  fi

  out=$("$VYG" countdown merge-loop \
        --repos "$REPOS_FILE" \
        --max-merges "${MERGE_MAX_MERGES:-3}" \
        --json)
  rc=$?
  # Pass the output through so launchd's log captures the same JSON lines
  # operators already grep (VOY-1839 §logs) — and, on failure, the CLI's
  # diagnostic (vyg reports errors via typer.echo on stdout). Never swallow it.
  echo "$out"

  if [[ "$rc" -ne 0 ]]; then
    # Real failures (auth, config, AlreadyRunningError) must look like
    # failures, not like quiet runs; take the slow lane and say why.
    echo "adaptive: vyg exited rc=${rc}; sleeping ${slow}s before retry"
    fast_streak=0
    sleep "$slow"
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
    sleep "$fast"
  else
    if [[ "$decisions" -gt 0 ]]; then
      echo "adaptive: fast-streak cap reached; backing off to ${slow}s"
    else
      echo "adaptive: idle; next check in ${slow}s"
    fi
    fast_streak=0
    sleep "$slow"
  fi
done
