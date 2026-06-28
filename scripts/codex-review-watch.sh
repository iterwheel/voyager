#!/usr/bin/env bash
# codex-review-watch.sh — trigger a Codex PR review and wait for its verdict.
#
# Encodes the hard-won lessons from PR #224's review loop:
#   1. Codex DEDUPES rapid "@codex review" comments → a trigger can be silently
#      dropped. We confirm Codex reacts 👀 to OUR trigger; if not, we re-trigger.
#   2. The /pulls/{n}/reviews LIST endpoint is EVENTUALLY CONSISTENT (lags a few
#      minutes) — a review can exist (fetchable by ID) yet be absent from the list.
#      We can't beat the lag, only wait it out, so we poll with patience and key on
#      `commit_id == <current head>` (re-anchored comments from older rounds carry an
#      old created_at but the NEW head only appears once the real new review lands).
#   3. "Clean" = Codex reacts 👍 on the PR with zero inline comments on the head.
#      "Findings" = ≥1 inline comment on the head commit.
#
# Usage:
#   scripts/codex-review-watch.sh <PR> [--repo OWNER/REPO] [--no-trigger]
#                                      [--bot LOGIN] [--timeout-min N]
#
# Exit: 0 = clean (👍) · 2 = findings (printed) · 1 = operational error / timed out.
set -euo pipefail

REPO="iterwheel/voyager"
BOT="chatgpt-codex-connector[bot]"
TRIGGER=1
TIMEOUT_MIN=30
PR=""

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --bot) BOT="$2"; shift 2 ;;
    --no-trigger) TRIGGER=0; shift ;;
    --timeout-min) TIMEOUT_MIN="$2"; shift 2 ;;
    -h|--help) grep '^# ' "$0" | sed 's/^# //'; exit 0 ;;
    *) PR="$1"; shift ;;
  esac
done
[ -n "$PR" ] || { echo "ERROR: PR number required (see --help)" >&2; exit 1; }

api() { gh api "$@" 2>/dev/null; }
head_sha() { api "repos/$REPO/pulls/$PR" --jq '.head.sha'; }

# Count of bot activity strictly AFTER $1 (ISO8601).
#
# TWO traps, both fatal to naive polling:
#  1. PAGINATION. These list endpoints return 30/page, OLDEST-FIRST, and `gh api` does
#     NOT auto-paginate. Without --paginate you only ever see the 30 oldest items (the
#     stale re-anchored comments) and NEVER the newest verdict (on a later page). MUST
#     use --paginate. And with --paginate, `--jq '...|length'` emits a length PER PAGE —
#     so we emit one line per match and count with `wc -l` instead.
#  2. RE-ANCHORING. An unresolved old comment is re-anchored to the new head, sharing its
#     commit_id but keeping its old created_at — so key on created_at > trigger, NOT commit_id.
# A transient gh/network failure during a long poll must NOT kill the watch (set -e +
# pipefail): capture with `|| true` and count the captured lines, so a hiccup reads as 0.
new_inline() {
  local ids
  ids="$(api --paginate "repos/$REPO/pulls/$PR/comments" \
    --jq ".[] | select(.user.login==\"$BOT\") | select(.created_at > \"$1\") | .id" 2>/dev/null || true)"
  [ -z "$ids" ] && { echo 0; return 0; }
  printf '%s\n' "$ids" | wc -l | tr -d ' '
}
new_thumbs() {
  local ids
  ids="$(api --paginate "repos/$REPO/issues/$PR/reactions" \
    --jq ".[] | select(.content==\"+1\" and .user.login==\"$BOT\") | select(.created_at > \"$1\") | .id" 2>/dev/null || true)"
  [ -z "$ids" ] && { echo 0; return 0; }
  printf '%s\n' "$ids" | wc -l | tr -d ' '
}

HEAD="$(head_sha)"
SINCE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"   # default cutoff (used by --no-trigger)
echo "watching $REPO#$PR @ ${HEAD:0:7} (bot=$BOT, timeout=${TIMEOUT_MIN}m)"

trigger_and_confirm_ack() {
  local url cid ack ts
  url="$(gh pr comment "$PR" --repo "$REPO" --body "@codex review" 2>&1 | tail -1)"
  cid="$(printf '%s' "$url" | grep -oE '[0-9]+$' || true)"
  [ -n "$cid" ] || { echo "ERROR: could not post trigger comment ($url)" >&2; return 1; }
  ts="$(api "repos/$REPO/issues/comments/$cid" --jq '.created_at' || true)"
  [ -n "$ts" ] && SINCE="$ts"   # detect only activity after OUR trigger
  echo "triggered: $url"
  for _ in 1 2 3 4 5 6; do
    # Must be Codex's OWN 👀 — a human/other-bot reaction must NOT count as an ack,
    # else a deduped/dropped trigger looks acked and we wait for a verdict never started.
    ack="$(api "repos/$REPO/issues/comments/$cid/reactions" \
      --jq "[.[] | select(.content==\"eyes\" and .user.login==\"$BOT\")] | length" || echo 0)"
    [ "${ack:-0}" -gt 0 ] && { echo "  codex acked 👀"; return 0; }
    sleep 10
  done
  echo "  WARN: no ack within 60s (Codex may have dropped it)"; return 1
}

if [ "$TRIGGER" -eq 1 ]; then
  trigger_and_confirm_ack || { echo "retrying trigger once…"; trigger_and_confirm_ack || true; }
fi
echo "detecting codex activity after: $SINCE"

# Two definitive signals, both timely: a NEW inline comment (findings) or a NEW 👍
# (clean). The /reviews list endpoint lags and a bare wrapper review is ambiguous, so
# we don't gate on it.
deadline=$(( TIMEOUT_MIN * 60 / 40 ))   # 40s cadence keeps the prompt cache warm
for i in $(seq 1 "$deadline"); do
  cmt="$(new_inline "$SINCE")"; up="$(new_thumbs "$SINCE")"
  if [ "${cmt:-0}" -gt 0 ] || [ "${up:-0}" -gt 0 ]; then
    echo "signal @ iter $i: new_inline=$cmt new_thumbs=$up"
    break
  fi
  sleep 40
done

cmt="$(new_inline "$SINCE")"; up="$(new_thumbs "$SINCE")"
if [ "${cmt:-0}" -gt 0 ]; then
  echo "=== FINDINGS (codex, after $SINCE) ==="
  api --paginate "repos/$REPO/pulls/$PR/comments" \
    --jq ".[] | select(.user.login==\"$BOT\") | select(.created_at > \"$SINCE\") |
          \"--- \(.path):\(.line // .original_line)\n\(.body)\n\""
  exit 2
elif [ "${up:-0}" -gt 0 ]; then
  # The 👍 is a PR-level reaction with no commit SHA. If a new commit landed while we
  # waited, that 👍 is for the OLD head — accepting it would green-light an unreviewed
  # head. Re-check before declaring clean.
  cur="$(head_sha)"
  if [ "$cur" != "$HEAD" ]; then
    echo "=== HEAD MOVED ${HEAD:0:7} → ${cur:0:7} during watch; 👍 is for the old head — NOT clean ===" >&2
    echo "    re-run to review the new head" >&2
    exit 1
  fi
  echo "=== CLEAN — codex reacted 👍 on $REPO#$PR @ ${HEAD:0:7} ==="
  exit 0
else
  echo "=== TIMED OUT after ${TIMEOUT_MIN}m with no NEW verdict since $SINCE ===" >&2
  echo "    (re-run with --no-trigger to keep waiting without re-triggering)" >&2
  exit 1
fi
