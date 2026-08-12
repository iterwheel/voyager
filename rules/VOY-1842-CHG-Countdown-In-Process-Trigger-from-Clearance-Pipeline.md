# CHG-1842: Countdown In-Process Trigger from Clearance Pipeline

**Applies to:** VOY project
**Last updated:** 2026-08-12
**Last reviewed:** 2026-08-12
**Status:** In Progress
**Date:** 2026-08-12
**Requested by:** Frank Xu
**Priority:** Medium
**Change Type:** Normal
**Related:** VOY-1841, VOY-1835, COR-1500

---

## What

Touch the Countdown trigger file directly from the Clearance pipeline, in
process, at the moment Clearance successfully posts a per-thread verdict
comment whose verdict is RESOLVED — both the thread-conclusion reply and the
manual-close/close-reason comment. The touch is gated by the same
`_repository_allowed_for_agent(repo, COUNTDOWN_AGENT_SLUG, cfg)` predicate the
webhook route uses, and reuses `touch_trigger_file()` from
`voyager.bots.countdown.routing` (fail-open, no payload data).

The CHG-1841 webhook route stays unchanged as a secondary path for
repositories where another app's webhook delivers Clearance-authored comment
events.

## Why

CHG-1841's D1 relies on the bridge *receiving a webhook for a comment the
bridge itself posted*. GitHub does not deliver webhook events to a GitHub App
for actions performed by that same App — Clearance verdict comments are posted
with the `iterwheel-clearance` App credentials, so on repositories where that
App is the only source of review-comment deliveries, the RESOLVED event is
suppressed at GitHub's side and the trigger never fires.

Observed on 2026-08-12, `frankyxhl/alfred` PR #326: Clearance posted
`✅ Clearance: resolved` at 00:38:52 UTC (and earlier at 23:48:01); no trigger
touch followed either comment (trigger-wake count unchanged), and the second
verdict landed just as the scheduler backed off to the 3600 s slow lane —
recreating exactly the latency CHG-1841 was built to remove. Replaying the
real comment body through the deployed route touches the trigger, and the
repository gate passes for `frankyxhl/alfred` with the live environment, which
isolates the failure to event delivery. The 2026-08-11 19:07 UTC
`frankyxhl/order_system_django` end-to-end success shows delivery does occur
on some repositories (a second App's webhook), which makes the webhook path
installation-topology-dependent — not a contract the trigger should depend on.

In-process touch removes the delivery dependency entirely: the producer of the
signal (the Clearance pipeline inside the bridge) performs the touch itself,
on the same machine, with zero delivery latency.

## Impact Analysis

- **Systems affected:** Clearance pipeline (verdict writeback path) gains one
  post-success side call; Countdown routing module unchanged; scheduler
  unchanged.
- **Security posture:** Unchanged. Same data-free trigger file, same
  repository allow-list predicate, same fail-open contract (a touch failure
  must never fail the Clearance writeback that just succeeded).
- **Cost bound:** One extra bounded scan per Clearance RESOLVED verdict on an
  allow-listed repository — identical to CHG-1841's intended behavior; the
  webhook path double-firing on multi-app repositories is absorbed by the
  consume-before-run debounce (one trigger file, one scan).
- **Downtime:** No. Normal bridge wheel deploy per VOY-1835/VOY-1814.
- **Rollback:** Revert the commit and redeploy; the webhook-only behavior of
  CHG-1841 returns.

## Out of Scope

- Removing the CHG-1841 webhook route.
- Changing GitHub App webhook topology or installations.
- Any change to `run_resolve_loop`, the scheduler script, or trigger-file
  semantics (consume, mtime, slicing).

## Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Touch in-process, after the RESOLVED verdict comment post **succeeds**. | The signal must mean "a RESOLVED verdict is now visible on GitHub"; touching before the post could wake a scan that finds nothing. |
| D2 | Cover both RESOLVED surfaces: thread-conclusion reply and manual-close/close-reason comment. | The alfred miss was a `clearance-manual-close` comment; both carry the same heading and the same meaning for Countdown. |
| D3 | Gate with the same `_repository_allowed_for_agent(repo, COUNTDOWN_AGENT_SLUG, cfg)` predicate as the webhook route. | One policy, one knob (`BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_COUNTDOWN`); default-deny in production is preserved. |
| D4 | Reuse `touch_trigger_file()`; no new trigger mechanics. | Single producer implementation; CHG-1841's fail-open and path-override contracts apply unchanged. |
| D5 | Keep the webhook route. | On multi-app repositories it is a working redundant path; the debounce makes double-fire free. Removing it is a separate decision once in-process touch has soaked. |

## Surfaces

| # | Surface | Required change |
|---|---------|-----------------|
| 1 | `voyager/bots/clearance/` (pipeline verdict writeback site) | After a successful RESOLVED verdict comment post (both D2 surfaces), call the countdown trigger touch behind the D3 gate. Locate the exact post-success seam(s) by reading the pipeline; do not touch verdict logic. |
| 2 | Tests | RESOLVED conclusion → touch; RESOLVED manual-close → touch; OPEN / NEEDS_HUMAN_JUDGMENT → no touch; denied repository → no touch; touch failure → writeback result unaffected. |
| 3 | `rules/VOY-1841-CHG-...md` | Change History row noting the delivery-suppression gap and pointing to this CHG (no rewrite of 1841's body). |

## Implementation Plan

1. Failing tests first for every Surface-2 case, following the repo's existing
   clearance pipeline test patterns.
2. Implement the post-success touch call(s) behind the gate.
3. Full verification suite (ruff, ruff format, mypy, pytest, af validate).
4. Codex review loop per VOY-1832; PR by `ryosaeba1985`; deploy per VOY-1835.

## Approval

- [ ] Approved by: <reviewer> on <date>

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-08-12 | Initial proposal — in-process Countdown trigger from the Clearance pipeline, motivated by GitHub same-app webhook suppression observed on alfred #326 | Claude Code |
