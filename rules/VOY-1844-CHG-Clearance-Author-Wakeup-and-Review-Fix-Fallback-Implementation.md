# CHG-1844: Clearance Author Wakeup and Review Fix Fallback Implementation

**Applies to:** VOY project
**Last updated:** 2026-08-30
**Last reviewed:** 2026-08-30
**Status:** In Progress
**Related:** VOY-1843, VOY-1814, PR #318
**Date:** 2026-08-30
**Requested by:** Frank via pfc / graph-engineering.bob
**Priority:** High
**Change Type:** Normal
**Scheduled:** 2026-08-30 (immediate)

---

## What

Implement the merged VOY-1843 contract as a bridge-owned, default-off Clearance
author-wakeup reconciler. It persistently tracks author-keyed state-A review
threads, sends one batched notification through the local PFC door, observes
claim evidence, and invokes the existing governed review-fix loop only after a
verified downstream author-delivery receipt and an unclaimed fallback window.


## Why

Clearance safely leaves unanswered Codex findings OPEN and Countdown safely
blocks merge, but no component wakes the citizen that opened the PR. The merged
PRP defines the missing ownership edge and a bounded, fail-closed repair
fallback. This CHG turns that reviewed contract into tested code without
weakening Clearance, Countdown, approval, or merge gates.

---

## Scope

**In scope:**

- Parse all VOY-1843 `[clearance.author_wakeup]` TOML keys with matching
  env-over-TOML overrides and default-off behavior.
- Add a bridge lifespan reconciler with a durable local SQLite event/current
  ledger under the configured author-wakeup audit directory.
- Derive author-keyed eligibility from the latest persisted Clearance verdict
  plus a live PR/thread re-read keyed to the PR-author login.
- Batch due thread IDs per repo/PR/head, POST the v1 message to
  `http://localhost:8420/api/agent-send`, correlate attempt-scoped send IDs, and
  implement the reviewed receipt/retry/retention state machine.
- Detect claim/supersession evidence and expose a restricted internal
  review-fix invocation with exact finding IDs and a notification-time head
  guard.
- Wire webhook completion to nudge the reconciler and expose safe health/audit
  evidence for testing and rollout.
- Ship repository-safe config/env templates and a sandbox-first VOY-1814
  rollout/rollback plan.

**Out of scope:**

- Enabling author wake-up on `frankyxhl/alfred` or `frankyxhl/trinity` in this
  slice; those remain explicit post-canary Frank clicks.
- Enabling live auto-review-fix during the first sandbox notification canary.
- Changing Clearance verdict/severity rules or Countdown resolve/merge gates.
- Implementing the PFC-side graph-routing receipt extension in the Voyager
  repository. Voyager fails closed when the live door does not advertise the
  reviewed retention/`author_delivered` contract.


## Impact Analysis

- **Systems affected:** Voyager bridge lifespan, Clearance state reads,
  governed review-fix dispatch, private Wukong author-wakeup state, and the
  loopback PFC door.
- **Channels affected:** none; reports remain on citizen edges and never the
  Demonstration group.
- **Downtime required:** no application downtime; one attended bridge restart
  per VOY-1814 after a merged wheel is installed.
- **GitHub permissions:** no new permission. Clearance keeps read access;
  review-fix reuses the existing Assembly App only on its explicit allowlist.
- **PFC permissions:** loopback HTTP only; no credential is added in v1.
- **Data sensitivity:** full-fidelity repo/PR/thread/citizen identifiers stay in
  a 0600 SQLite file under a 0700 directory; public logs remain redacted.
- **Failure posture:** missing config, invalid URL, missing/short PFC send-ID
  retention, non-author delivery, stale head, kill switch, or missing L3
  envelope all fail closed with no fallback mutation.
- **Rollback plan:** set `CLEARANCE_AUTHOR_WAKEUP_ENABLED=false` and
  `CLEARANCE_AUTHOR_WAKEUP_AUTO_REVIEW_FIX=false`, restart the bridge, then use
  VOY-1814's atomic `mv -hf` venv swap to the named prior wheel if code rollback
  is required. Existing OPEN threads remain merge-blocking.


## Implementation Plan

1. Add RED config tests for defaults, valid TOML, env precedence, URL/repository
   validation, and malformed fail-closed inputs.
2. Add RED author-wakeup tests for author-keyed eligibility, continuous N,
   batching/dedupe, SQLite restart recovery, v1 message format, receipt stages,
   attempt IDs, retention/repost limits, claim evidence, and fallback gating.
3. Add RED review-fix tests for exact finding scoping, notification-time head
   refusal, invocation-owned head advancement, and silent internal refusals.
4. Add RED server tests for default-off lifespan behavior, enabled task
   lifecycle, webhook nudge, shutdown, and health evidence.
5. Implement the minimum config, reconciler/store/client, review-fix seam, and
   server wiring required to pass those tests; add no dependency.
6. Refactor only after the focused suite is green; run Ruff and mypy on touched
   production files.
7. Update config/env templates and this CHG with real validation output; run
   `af index`, targeted `af validate`, and the repository's CI stack.
8. Publish a non-draft PR from the fork as `ryosaeba1985`; follow
   COR-1615→1612→1623 until current-head review and all threads are clear.
9. After merge, deploy a versioned wheel per VOY-1814, enable only
   `iterwheel/voyager-sandbox` with auto-review-fix false, and prove exactly one
   wake-up reaches pfc using the pfc-side terminal receipt.
10. Record canary evidence and leave `frankyxhl/alfred` +
    `frankyxhl/trinity` enablement as a batched Frank to-do.

### Implementation Order / TDD Assignment

The project has no distinct `<test-writer-worker-agent>` configured, so the
COR-1500 two-worker split is off. The same worker will run sequential RED then
GREEN cycles and will not write production code before each failing test is
observed. COR-1508 posture is `full`; new files are limited to the dedicated
reconciler module and its focused test module because no existing component owns
this state machine.

---

## Testing / Verification

- **RED/GREEN inner loop:** targeted pytest node(s) in the touched test files.
- **Focused final:**
  `pytest tests/clearance/test_author_wakeup.py tests/unit/test_bridge_assembly_config.py tests/unit/test_review_fix_bot.py tests/unit/test_server_author_wakeup_schedule.py -q`.
- **Static:** Ruff check/format on touched Python files; `mypy voyager`.
- **Docs:** `af fmt --check VOY-1202 VOY-1844`, targeted `af validate`, and
  `git diff --check`.
- **CI:** repository GitHub CI (lint, security, typecheck, Python 3.11/3.12/3.13).
- **Sandbox e2e:** one controlled current-head state-A thread, one PFC POST,
  one pfc-side receipt, no duplicate notification, auto-review-fix false, and
  Countdown still reports unresolved-thread merge refusal.
- **Rollback verification:** disabled config starts no task; prior venv can be
  restored with VOY-1814 `mv -hf`; `/healthz` reports the prior build commit.

---

## Acceptance Criteria

- [x] All author-wakeup settings default off/deny and env overrides win over
  TOML without weakening validation.
- [x] Eligibility ignores maintainer/bot replies and keys only on the PR-author
  login, current head, OPEN verdict, and unresolved/non-outdated state.
- [x] N timing, PR/head batching, duplicate webhook/restart dedupe, and audit
  redaction are deterministic.
- [x] The PFC message carries exactly the reviewed v1 application fields and no
  delivery-derived deadline.
- [x] Receipt correlation, same-ID/new-ID retries, 24h retention, safety margin,
  `pfc_received`/`author_delivered`, and delivery-unknown behavior match
  VOY-1843.
- [x] Claim evidence cancels fallback; stale heads and unrelated replies behave
  per the merged contract.
- [x] Internal review-fix handles only the notified finding IDs, enforces the
  notification-time head, advances only through its own verified commits, and
  retains all L3/kill-switch/dry-run/allowlist controls.
- [x] No author-wakeup path resolves, approves, or merges a PR.
- [ ] Focused tests, static checks, CI, and the sandbox notification canary pass.

---

## Approval

- [x] Reviewed by: Frank via pfc / graph-engineering.bob (owner decision;
  merged VOY-1843 PR #318)
- [x] Approved on: 2026-08-30

---

## Execution Log

| Date | Action | Result |
|------|--------|--------|
| 2026-08-30 | Created implementation CHG from merged VOY-1843 and owner start instruction | In progress; no production code written yet |
| 2026-08-30 | Plan self-review against merged VOY-1843 and current config/server/review-fix seams | PASS — all PRP config, receipt, claim, fallback, rollout, and rollback surfaces mapped; PFC receipt dependency fails closed |
| 2026-08-30 | Sequential RED/GREEN cycles for config, durable reconciler/receipt state machine, claim/fallback, internal review-fix guard, and bridge schedule | PASS — each behavior observed failing before its production change; no split worker configured |
| 2026-08-30 | Focused touched-surface pytest | PASS — 91 passed |
| 2026-08-30 | Touched-file Ruff, mypy, and Bandit | PASS — Ruff clean/format clean; mypy 5 source files clean; Bandit no findings |

---

## Post-Change Review

- Pending implementation, PR merge, and sandbox canary.

---

## Change History

| Date       | Change                                                                           | By    |
|------------|----------------------------------------------------------------------------------|-------|
| 2026-08-30 | Initial owner-approved implementation contract for VOY-1843                      | Codex |
| 2026-08-30 | Plan self-review passed against merged VOY-1843 and current implementation seams | Codex |
