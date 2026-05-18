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
LOCK_FILE="${VOYAGER_LOCK_FILE:-/tmp/voyager-loop.lock}"
LOG_DIR="${VOYAGER_LOG_DIR:-$HOME/Library/Logs/voyager}"
LOOP_INTERVAL="${VOYAGER_LOOP_INTERVAL:-7200}"

LOOP_CMD="deepseek exec --auto follow VOY-1811 once"

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

# ── Lock file (concurrency guard) ───────────────────────────────────────────

acquire_lock() {
    if [[ -f "$LOCK_FILE" ]]; then
        local lock_age
        lock_age=$(($(date +%s) - $(stat -f %m "$LOCK_FILE" 2>/dev/null || stat -c %Y "$LOCK_FILE" 2>/dev/null || echo 0)))
        if (( lock_age < LOOP_INTERVAL )); then
            log "Lock held by another instance (age=${lock_age}s < ${LOOP_INTERVAL}s). Exiting."
            exit 1
        fi
        log_err "Stale lock detected (age=${lock_age}s >= ${LOOP_INTERVAL}s). Removing lock."
        rm -f "$LOCK_FILE"
        exit 5
    fi
    echo $$ > "$LOCK_FILE"
}

release_lock() {
    # Only remove the lock file if this process owns it.
    # Defense-in-depth: the trap is only installed after acquire_lock
    # succeeds, but verifying PID prevents an accidental double-release
    # or a stale trap from removing another process's lock.
    if [[ -f "$LOCK_FILE" ]]; then
        local lock_pid
        lock_pid=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
        if [[ "$lock_pid" == "$$" ]] || [[ -z "$lock_pid" ]]; then
            rm -f "$LOCK_FILE"
        fi
        # If lock_pid != $$, do NOT remove — another process owns the lock.
    fi
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

    # deepseek exec auto-accepts approval requests when --auto is set.
    # The loop_continue.sh wrapper is a non-interactive scheduler; there is
    # no operator to approve prompts. Run with --auto.
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
