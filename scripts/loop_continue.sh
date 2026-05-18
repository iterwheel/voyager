#!/bin/zsh
#
# loop_continue.sh — Durable wakeup wrapper for DeepSeek TUI VOY-1811 loops.
#
# Intended to be invoked by a launchd timer (StartInterval). Uses a lock file
# to prevent overlapping runs, sources the bridge environment for API keys and
# gh auth, then invokes deepseek exec in non-interactive agent mode.
#
# Usage:
#   scripts/loop_continue.sh
#
# Environment:
#   VOYAGER_PROJECT_ROOT  — path to the voyager repo (default: $HOME/Projects/voyager)
#   VOYAGER_ENV_FILE      — path to bridge.env (default: $HOME/.voyager/bridge.env)
#   VOYAGER_LOCK_FILE     — lock file path (default: /tmp/voyager-loop.lock)
#   VOYAGER_LOG_DIR       — log directory (default: $HOME/Library/Logs/voyager)
#   VOYAGER_LOOP_INTERVAL — expected loop interval for stale lock detection in seconds (default: 7200)
#
# Exit codes:
#   0 — loop completed or no work available
#   1 — lock held by another instance (normal: previous run still in progress)
#   2 — deepseek CLI not found
#   3 — environment file missing or unreadable
#   4 — deepseek exec returned non-zero
#   5 — stale lock detected and removed; operator should investigate

set -euo pipefail

# ── Configuration ───────────────────────────────────────────────────────────

PROJECT_ROOT="${VOYAGER_PROJECT_ROOT:-$HOME/Projects/voyager}"
ENV_FILE="${VOYAGER_ENV_FILE:-$HOME/.voyager/bridge.env}"
# LOCK_DIR is used for atomic mkdir-based locking; LOCK_FILE retained for env var compat
LOCK_FILE="${VOYAGER_LOCK_FILE:-/tmp/voyager-loop.lock}"
LOG_DIR="${VOYAGER_LOG_DIR:-$HOME/Library/Logs/voyager}"
LOOP_INTERVAL="${VOYAGER_LOOP_INTERVAL:-7200}"

# Verified on DeepSeek CLI v0.8.39. --yolo enables auto-approval for headless
# launchd invocations; exec --auto enables agentic mode. The prompt is quoted
# as a single string to survive shell word-splitting.
LOOP_CMD='deepseek --yolo exec --auto "follow VOY-1811 once"'

# ── Logging setup ───────────────────────────────────────────────────────────

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/loop.out.log"
ERR_FILE="$LOG_DIR/loop.err.log"

log() {
    echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] $*" | tee -a "$LOG_FILE"
}

log_err() {
    echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] ERROR: $*" | tee -a "$ERR_FILE" >&2
}

# ── Lock directory (atomic concurrency guard) ───────────────────────────────
#
# Uses mkdir() which is atomic on all Unix filesystems.  A plain lock *file*
# with a check-then-create sequence is racy: two invocations that start
# within the same window can both observe no lock and both proceed.

LOCK_DIR="${LOCK_FILE}.dir"

acquire_lock() {
    if ! mkdir "$LOCK_DIR" 2>/dev/null; then
        # Lock held — check staleness by directory mtime
        local lock_age
        lock_age=$(($(date +%s) - $(stat -f %m "$LOCK_DIR" 2>/dev/null || stat -c %Y "$LOCK_DIR" 2>/dev/null || echo 0)))
        if (( lock_age < LOOP_INTERVAL )); then
            log "Lock held by another instance (age=${lock_age}s < ${LOOP_INTERVAL}s). Exiting."
            exit 1
        fi
        # Lock appears stale by age — but the original process may still be
        # alive (e.g. a long-running loop). Verify the stored PID before
        # removing the lock to avoid defeating the concurrency guard.
        local lock_pid=""
        [[ -f "$LOCK_DIR/pid" ]] && lock_pid=$(cat "$LOCK_DIR/pid" 2>/dev/null || echo "")
        if [[ -n "$lock_pid" ]] && kill -0 "$lock_pid" 2>/dev/null; then
            log "Stale-aged lock (age=${lock_age}s) but PID $lock_pid is still alive. Exiting."
            exit 1
        fi
        # PID is dead or missing — safe to reap the lock
        log_err "Stale lock detected (age=${lock_age}s >= ${LOOP_INTERVAL}s, PID ${lock_pid:-unknown} not alive). Removing lock."
        rm -f "$LOCK_DIR/pid"
        rmdir "$LOCK_DIR" 2>/dev/null || rm -rf "$LOCK_DIR"
        # Exit 5 so the operator can investigate why a lock outlived the interval
        exit 5
    fi
    # Lock acquired atomically — store PID for inspection
    echo $$ > "$LOCK_DIR/pid"
}

release_lock() {
    # Defense-in-depth: trap is only installed after acquire_lock
    # succeeds, but verifying PID prevents an accidental double-release
    # or a stale trap from removing another process's lock.
    local lock_pid=""
    [[ -f "$LOCK_DIR/pid" ]] && lock_pid=$(cat "$LOCK_DIR/pid" 2>/dev/null || echo "")
    if [[ -z "$lock_pid" ]] || [[ "$lock_pid" == "$$" ]]; then
        rm -f "$LOCK_DIR/pid"
        rmdir "$LOCK_DIR" 2>/dev/null
    fi
    # If lock_pid != $$, do NOT remove — another process owns the lock.
}

# ── Environment ──────────────────────────────────────────────────────────────

source_env() {
    if [[ ! -f "$ENV_FILE" ]]; then
        log_err "Environment file not found: $ENV_FILE"
        log_err "Create it from deploy/wukong/bridge.env.example and chmod 600."
        exit 3
    fi
    if [[ ! -r "$ENV_FILE" ]]; then
        log_err "Environment file not readable: $ENV_FILE"
        exit 3
    fi
    set -a
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    set +a
    log "Environment sourced from $ENV_FILE"
}

# ── Prerequisites ────────────────────────────────────────────────────────────

check_prereqs() {
    if ! command -v deepseek &>/dev/null; then
        log_err "deepseek CLI not found on PATH."
        log_err "Install: brew install deepseek or npm i -g deepseek-tui"
        exit 2
    fi
    log "deepseek CLI found at $(command -v deepseek)"

    if ! command -v gh &>/dev/null; then
        log_err "gh CLI not found on PATH."
        exit 2
    fi
    log "gh CLI found at $(command -v gh)"
}

# ── Main ─────────────────────────────────────────────────────────────────────

main() {
    acquire_lock
    trap release_lock EXIT INT TERM

    log "=== Loop wakeup starting (PID $$) ==="

    source_env
    check_prereqs

    cd "$PROJECT_ROOT" || {
        log_err "Cannot cd to $PROJECT_ROOT"
        exit 4
    }

    log "Running: $LOOP_CMD"
    log "Working directory: $(pwd)"

    # deepseek --yolo exec --auto handles tool approval in headless mode.
    # The loop_continue.sh wrapper is a non-interactive scheduler; there is
    # no operator to approve prompts. If the CLI version changes, verify
    # that --yolo and exec --auto are still the correct flags.
    if eval "$LOOP_CMD" >>"$LOG_FILE" 2>>"$ERR_FILE"; then
        log "=== Loop wakeup completed successfully ==="
    else
        local rc=$?
        log_err "Loop command exited with code $rc"
        log "=== Loop wakeup completed with errors (rc=$rc) ==="
        exit 4
    fi
}

main "$@"
