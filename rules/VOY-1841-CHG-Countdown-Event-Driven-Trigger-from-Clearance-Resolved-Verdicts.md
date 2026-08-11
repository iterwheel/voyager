# CHG-1841: Countdown Event-Driven Trigger from Clearance Resolved Verdicts

**Applies to:** VOY project
**Last updated:** 2026-08-12
**Last reviewed:** 2026-08-12
**Status:** In Progress
**Date:** 2026-08-12
**Requested by:** Frank Xu
**Priority:** Medium
**Change Type:** Normal
**Related:** VOY-1831, VOY-1835, Issue #279 (adaptive scheduler), COR-1500

---

## What

Give the Countdown resolve-loop an event-driven fast path: when Clearance posts
a per-thread verdict reply whose status heading is `✅ **Clearance: resolved**`,
the bridge touches a machine-local trigger file, and the adaptive scheduler
daemon wakes from its sleep slice and runs one scan immediately instead of
waiting out the remainder of its current interval (up to 3600 s on the slow
lane).

The change has two layers:

1. **Bridge route (repo code, deployed wheel):** a new Countdown trigger route
   fires on `pull_request_review_comment.created` when the comment author is
   `CLEARANCE_BOT_LOGIN` and the body contains the RESOLVED status heading
   (both reused from `voyager.bots.clearance` — single source of truth). The
   route's action is to touch the trigger file. No payload data crosses the
   boundary.
2. **Adaptive scheduler (deploy template):** `countdown-resolve-loop-adaptive.sh`
   replaces its monolithic `sleep` with ≤30 s slices; between slices it checks
   the trigger file's mtime against the current run's start. A newer trigger
   ends the sleep early; the file is consumed (deleted) before the next `vyg`
   run so one trigger yields at most one extra scan.

Polling cadence (300 s fast / 3600 s slow / streak cap 6) is unchanged and
remains the delivery-loss fallback.

## Why

On 2026-08-11, Codex review comments on `frankyxhl/alfred` PR #320 landed at
12:52 UTC, eight minutes after a scan that had just backed off to the slow
lane. The threads were mechanically resolvable and gate-approvable the whole
time, but Countdown only resolved them at 13:44 UTC — a 52-minute latency that
is pure polling-schedule artifact. Clearance already emits a precise, stable
signal at the exact moment a thread becomes worth scanning (its RESOLVED
verdict reply), and the bridge already receives and routes the webhook
carrying that signal (`route_clearance_event` guards against self-triggering
on these same events). The missing piece is only a one-way nudge from bridge
to daemon on the same machine.

## Impact Analysis

- **Systems affected:** Bridge webhook routing (new route only); Wukong
  adaptive scheduler script. The `vyg countdown resolve-loop` command itself
  is untouched.
- **Security posture:** Unchanged. The trigger file carries no data — the scan
  that follows runs the identical deterministic prefilter, allowlist, LLM
  should-resolve gate, freshness guard, and audit write-ahead as a timer-fired
  scan. A forged or spoofed comment can at worst cause one extra bounded scan.
- **Runtime impact:** Trigger-to-scan latency ≤ one sleep slice (30 s) plus
  scan time. Sleep-slicing adds negligible wakeup cost. Concurrency is already
  safe: the loop's lock file makes an overlapping manual/daemon run exit with
  `AlreadyRunningError`.
- **Cost impact:** Each trigger costs one scan (one LLM gate call per live
  candidate). Rate is bounded by Clearance's own verdict cadence and the
  consume-on-run debounce; the streak cap continues to bound sustained fast
  cycling.
- **Downtime required:** No. Bridge picks the route up on normal deploy; the
  daemon picks the new script up on the next launchd restart per VOY-1835.
- **Rollback plan:** Revert the commit and redeploy bridge + recopy the
  previous script template. A stale trigger file is inert (consumed or
  ignored; the timer lanes still run).

## Out of Scope

- Removing or lengthening the polling lanes. Webhooks drop; polling stays
  authoritative for completeness.
- Passing repo/PR/thread identifiers through the trigger (scan-scoping). The
  full-allowlist scan is already bounded and keeps the trust boundary trivial.
- Triggering on other signals (Codex review submission, PR synchronize, etc.).
  Add-on candidates once this path is proven.
- Any change to `run_resolve_loop`, its gates, or the VOY-1828 redaction
  contract.
- Cross-machine delivery. Bridge and daemon share one machine (Wukong); if
  that ever splits, this becomes a different design.

## Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Trigger on `pull_request_review_comment.created` with author `CLEARANCE_BOT_LOGIN` and body containing the RESOLVED status heading, both imported from clearance modules. | Clearance per-thread verdicts are review-thread replies; the heading string and login already exist as constants — no new literals to drift. |
| D2 | Match the heading via the existing constant-producing function, not a regex duplicate. | `close_reason.py` is the single producer; reuse keeps the contract in one place. |
| D3 | Bridge action = touch (create/update mtime) one trigger file; default `~/.voyager/countdown-resolve-loop.trigger`, overridable via `COUNTDOWN_TRIGGER_PATH`. | One-way, data-free, same-machine signal; no daemon kill (`kickstart -k` could interrupt a mid-run resolve), no IPC dependency. |
| D4 | Scheduler consumes the trigger by deleting the file before invoking `vyg`; triggers arriving mid-scan (newer mtime) survive to start one follow-up scan. | Consume-before-run is the debounce; mid-scan arrivals must not be lost because the scan snapshot predates them. |
| D5 | A trigger-fired run participates in streak accounting exactly like a timer-fired run. | Keeps the cost bound (streak cap) intact; no second accounting regime. |
| D6 | Fail open on trigger-file I/O errors in the bridge route (log, return non-fatal). | The fallback lane already guarantees eventual resolution; a trigger failure must never fail webhook handling for other bots. |
| D7 | No delivery-ID dedup for the trigger touch; GitHub's at-least-once redelivery semantics apply as-is. | A redelivered webhook re-touches an already-consumed trigger, costing at most one extra bounded scan (same blast radius as D6's forged-comment case) — not worth a dedup store for that ceiling. |

## Event Matrix

| Event | Author / body | Expected action |
|-------|---------------|-----------------|
| `pull_request_review_comment.created` | Clearance bot, body contains `✅ **Clearance: resolved**` | Touch trigger file |
| `pull_request_review_comment.created` | Clearance bot, `still open` / `needs human judgment` heading | No action |
| `pull_request_review_comment.created` | Any other author, body contains the resolved heading (spoof) | No action (author guard first) |
| `pull_request_review_comment` non-`created` actions | Any | No action |
| Any other event type | Any | No action |

## Surfaces

| # | Surface | Required change |
|---|---------|-----------------|
| 1 | `voyager/bots/countdown/` (new) | `routing.py` with `route_countdown_trigger(event, payload)` + trigger-file touch action; author + heading guards per D1/D2/D6. |
| 2 | `voyager/server.py` | Register the new route alongside the existing `route_*_event` calls. |
| 3 | `deploy/wukong/countdown-resolve-loop-adaptive.sh` | Sliced sleep (≤30 s) with trigger-mtime check; consume-before-run per D4; streak accounting per D5. |
| 4 | `deploy/wukong/countdown-resolve-loop.env.example` | Document `COUNTDOWN_TRIGGER_PATH` (optional, with default). |
| 5 | Tests: countdown routing | Unit/BDD coverage for every Event Matrix row, plus fail-open on unwritable trigger path. Fixture: clearance-authored resolved-verdict reply webhook JSON. |
| 6 | `rules/VOY-1835-SOP-Countdown-Resolve-Loop-Launchd-Deployment.md` | Follow-up amendment (separate candidate, after implementation lands): deployment step for the revised script template + trigger-file note. |

## Implementation Plan

1. Write failing tests for the Event Matrix (routing guards, trigger touch,
   fail-open) and for the scheduler's trigger consumption contract where the
   test harness allows shell-level testing; otherwise record the manual
   dry-run steps for VOY-1835.
2. Implement Surface 1–2 (bridge route), then Surface 3–4 (scheduler
   template + env example) to green.
3. Run the repo verification suite (ruff, pytest, `af validate --root .`).
4. Codex review loop per VOY-1832; address findings.
5. PR authored by `ryosaeba1985`; deploy remains an operator-gated VOY-1835
   step after merge (wheel + script template copy + launchd restart).

## Approval

- [ ] Approved by: <reviewer> on <date>

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-08-12 | Initial proposal — event-driven Countdown trigger from Clearance resolved verdicts | Claude Code |
| 2026-08-12 | Codex review fix round: `trigger_newer_than` uses `>=` (BSD `stat` is second-resolution — major 1); `server.py` gates `route_countdown_trigger` behind `_repository_allowed_for_agent` so a non-allowlisted repository cannot wake Countdown (major 2); added D7 (at-least-once redelivery, no dedup); parameterized Event Matrix row 2 test over both non-resolved headings; added a zsh subprocess harness for the scheduler's trigger helpers; documented `COUNTDOWN_TRIGGER_PATH` cross-reference in `bridge.env.example`; removed a redundant `os.utime` call in `touch_trigger_file` | Claude Code |
