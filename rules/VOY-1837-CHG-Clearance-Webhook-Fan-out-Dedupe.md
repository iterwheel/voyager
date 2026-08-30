# CHG-1837: Clearance Webhook Fan-out Dedupe

**Applies to:** VOY project
**Last updated:** 2026-08-30
**Last reviewed:** 2026-08-30
**Status:** Completed
**Date:** 2026-08-02
**Requested by:** Frank Xu
**Priority:** Medium
**Change Type:** Normal
**Scheduled:** 2026-08-02
**Related:** Issue #292, Issues #146 and #197, COR-1503, COR-1500, COR-1616

---

## What

Prevent duplicate Clearance per-thread verdict replies caused by one submitted
review fanning out into a `pull_request_review.submitted` webhook plus one
`pull_request_review_comment.created` webhook for every inline finding.

The change has two layers:

1. Route review-comment `created` events only when the comment is a reply to an
   existing review thread. Root inline comments are already represented by the
   submitted-review event.
2. Serialize the stateful Clearance automation phase per `(running event loop,
   repository, PR)` so concurrent same-loop deliveries cannot all pass the
   fresh-marker check before the first write becomes visible. The registry keeps
   weak lock values, so idle locks and their loop keys expire.

## Why

On `frankyxhl/order_system_django` PR #47, Codex review `4837654859` contained
two inline findings. Voyager recorded three Clearance webhook polls for the same
head at `2026-08-02T07:38:32Z`. Discussion `r3698272722` then received three
identical persisted Clearance replies at `2026-08-02T07:38:39Z`: one intended
reply and two duplicates, with different GitHub comment and review IDs.

The current marker guard is a check-then-create sequence. Its cache is local to
one pipeline invocation, so it is idempotent for sequential reruns but not for
concurrent webhook tasks. PR #47 contains 11 duplicate-body groups and 19 excess
Clearance replies, showing that this is a recurring concurrency defect rather
than a rendering anomaly.

Issue #146 introduced the current same-head marker contract, and issue #197
extended manual-close dedupe across heads. Both fixes correctly handle
sequential re-evaluation; issue #292 closes the remaining concurrent-entry gap
without changing either marker format.

## Impact Analysis

- **Systems affected:** Clearance webhook routing and dynamic writeback dispatch.
- **Channels affected:** Pull-request review-thread comments on repositories
  managed by the Voyager bridge.
- **Downtime required:** No. The fix takes effect after the normal bridge deploy.
- **Runtime impact:** Same-PR Clearance evaluations wait for the preceding full
  pipeline run, including investigator latency when it fires. D1 removes the
  normal N-inline-comment burst, so queued same-PR runs should be exceptional;
  different PRs and repositories retain independent concurrency.
- **Memory impact:** One live in-process `asyncio.Lock` per active `(running
  event loop, repository, PR)` key. Weak values remove idle locks and their
  loop-key entries; no monotonic registry growth is retained between deliveries.
- **Rollback plan:** Revert the implementation commit and redeploy the prior
  bridge version. No migration is required; historical GitHub comments remain
  unchanged.

## Out of Scope

- Changing Clearance reviewer or investigator model configuration.
- Redesigning semantic verdict classification or PR-level readiness summaries.
- Deleting historical duplicate comments from affected pull requests.
- Distributed or cross-process locking. Voyager's deployed bridge is currently
  a single process; a future multi-worker deployment requires a durable claim.
- Serialization across simultaneously running event loops. The normal bridge
  deployment has one long-lived loop per worker; a cross-loop or cross-process
  guarantee requires a different coordination primitive.
- Treating GitHub delivery-ID dedupe as the primary fix. The reproducing
  deliveries have distinct IDs and are all legitimate events from one review.

## Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | A `pull_request_review_comment.created` event routes only when `comment.in_reply_to_id` is non-null. | The route was introduced specifically so an author reply re-evaluates its thread. Root inline comments belong to the submitted review and otherwise double-trigger the same evaluation. |
| D2 | Keep `pull_request_review.submitted` as the canonical trigger for a newly submitted review, regardless of how many inline comments it contains. | One review is one evaluation boundary; fan-out cardinality must not change the number of Clearance runs. |
| D3 | Serialize the public `compute_clearance_automation` entry point, keyed by `(running event loop, full `owner/repo`, integer PR number)`, while keeping its existing body as the private unlocked implementation. | The complete pipeline owns the first snapshot, every fresh-marker check, per-thread mutations, and persistence. `asyncio.Lock` becomes loop-bound under contention, so a module-cached lock must not be reused by a later event loop. Locking at the server would miss direct callers and couple Clearance concurrency to unrelated route batching. |
| D4 | Use `async with` around the lock. | Normal return, exception, and task cancellation all release the lock through the async context manager. |
| D5 | Use independent keys for different repositories or PR numbers. | A global lock would create unnecessary head-of-line blocking across unrelated review traffic. |
| D6 | Use a weak-value in-process lock registry; do not `finally`-pop entries. | Values remain strongly referenced by active holders and waiters, preserving same-loop serialization. Once no caller retains the lock, its weak entry disappears together with the loop-bearing key. A `finally` pop has a release-to-waiter handoff window that can split queued and newly arriving tasks across two locks. A `WeakKeyDictionary[loop, locks]` is also unsuitable because a contended lock retains its bound loop through the value graph. |
| D7 | Do not add a new dependency or durable store. | The current single-process bridge can satisfy the acceptance criteria with `asyncio.Lock`; distributed idempotency is explicitly deferred. |

## Event Matrix

| Event | Comment shape / actor | Expected routing |
|-------|-----------------------|------------------|
| `pull_request_review.submitted` | Any non-Clearance actor | One Clearance route |
| `pull_request_review.submitted` | No inline findings | One Clearance route; submitted review alone remains a complete trigger |
| `pull_request_review_comment.created` | Root inline comment (`in_reply_to_id=null`) | No route |
| `pull_request_review_comment.created` | Thread reply (`in_reply_to_id` set) | One Clearance route |
| `pull_request_review_comment.created` | Clearance-authored root or reply | No route; the existing actor guard wins before shape filtering |
| Any other supported review event | Clearance actor | No route; existing self-trigger guard remains authoritative |
| Review-comment action other than `created` | Root or reply | No route; existing action filter remains authoritative |

## Concurrency Matrix

| Repository | PR | Expected behavior |
|------------|----|-------------------|
| Same loop, same repository | Same | Second task waits until the first stateful automation phase exits |
| Same | Different | Both tasks can enter concurrently |
| Different | Same number | Both tasks can enter concurrently |
| Fresh loop after prior same-key contention | Same | A fresh loop obtains a distinct loop-scoped lock and can itself serialize same-key contenders without a bound-to-different-loop error |
| Simultaneously running different loops | Same | No in-process cross-loop serialization guarantee; out of scope for this single-loop-per-worker deployment |
| Any | Any | Exception or cancellation in the first task releases the key's lock |

## Surfaces

| # | Surface | Required change |
|---|---------|-----------------|
| 1 | `tests/bdd/features/clearance.feature` | Rewrite the existing root-comment scenario at line 43 to expect no route; add explicit reply, zero-inline submitted-review, and Clearance-authored review-comment scenarios. |
| 2 | `tests/fixtures/webhooks/clearance_pull_request_review_comment.json` | Keep this fixture as the root-comment shape and make the missing/null `in_reply_to_id` contract explicit. |
| 3 | `tests/fixtures/webhooks/clearance_pull_request_review_comment_reply.json` | Add a reply fixture with a non-null `in_reply_to_id`. |
| 4 | Clearance webhook fixture/step mapping | Register the new reply fixture and, if needed, a Clearance-authored reply fixture without weakening existing self-trigger coverage. |
| 5 | `tests/clearance/test_pipeline_concurrency.py` | Add deterministic same-key serialization, distinct-key parallelism, exception-release, and two-fresh-event-loop same-key-contention tests against the public pipeline entry. |
| 6 | `voyager/bots/clearance/routing.py` | Gate review-comment `created` routing on a non-null `comment.in_reply_to_id`, after the existing actor guard. |
| 7 | `voyager/bots/clearance/pipeline.py` | Add the keyed lock registry and preserve `compute_clearance_automation` as the locked public API over a private unlocked body. |

## Implementation Plan

1. Rewrite the committed BDD scenario that currently expects a root
   `pull_request_review_comment.created` event to route. Preserve its fixture as
   the root shape, add a reply fixture with `in_reply_to_id`, and add explicit
   scenarios for root=no route, reply=one route, submitted review with no inline
   findings=one route, and Clearance-authored root/reply=no route.
2. Add a lazy weak-value per-`(running event loop, repository, PR)`
   `asyncio.Lock` registry inside the Clearance pipeline module. Capture the
   running loop at lookup time; do not reuse a contended lock in a later loop,
   and do not add a `finally` map-pop cleanup.
3. Keep `compute_clearance_automation` as the locked public API and move its
   current body behind a private unlocked implementation. The lock covers the
   entire snapshot/classification/marker-read/thread-mutation/persistence
   pipeline without serializing unrelated PRs or later PR-level enrichment.
4. Preserve missing-repository and legacy `store is None` behavior.
5. Add deterministic concurrency regression tests against the public
   `compute_clearance_automation` API. Gate the fake client's first
   `pull_request` fetch with `asyncio.Event`; this seam exists before the fix,
   so RED requires no production stub or test-only API. Cover same-key
   serialization, same-repo/different-PR and different-repo/same-number
   parallelism, exception release followed by successful same-key entry, and
   same-key contention in each of two fresh event loops.
6. Run focused tests, the full project validation stack, and independent code
   review before publishing a ready-for-review PR.

## Implementation Order

1. **RED — tests-only worker.** Modify only the test/fixture Surfaces 1–5. Rewrite
   the existing root-comment BDD scenario, add the reply and self-trigger cases,
   and add deterministic same-key/distinct-key/exception-release concurrency
   tests. The concurrency test must gate the existing fake-client
   `pull_request` boundary with `asyncio.Event`; do not modify production files,
   add production stubs, assert only that a future private helper exists, or use
   an unconditional sleep as synchronization. For the same key, assert exactly
   one task reaches the gated fetch before release and both eventually finish;
   for distinct keys, assert both reach their gates before either is released.
   Also run two separate `asyncio.run` rounds with same-key contention in each;
   the second round must not reuse a lock bound by the first loop.
   Run the focused tests and report the expected behavioral failures against
   current `origin/main`.
2. **RED quality gate — orchestrator.** Confirm failures constrain missing
   behavior rather than fixtures, inspect for vacuous/trivial assertions, and
   verify no production path changed.
3. **GREEN — implementation-only worker.** Read this CHG, the committed test
   files, and production source; do not read the tests-worker commentary. Modify
   only production code, do not weaken or edit tests, and implement the smallest
   change that passes the RED tests.
4. **GREEN verification — orchestrator.** Re-run focused tests and the full
   validation stack, then inspect the event and concurrency invariants directly.
5. **REFACTOR — implementation worker if needed.** Make only behavior-preserving
   simplifications justified by the new diff; rerun focused tests after each
   change. No new abstraction, file, or dependency is expected.
6. **REVIEW — Trinity panel.** Review the final diff under COR-1610. Every viable
   reviewer must score at least 9.0/10 with no blocking finding before commit and
   PR publication.

## Testing / Verification

- `uv run pytest -q tests/bdd/step_defs/test_clearance_steps.py`
- `uv run pytest -q tests/clearance/test_pipeline_concurrency.py`
- The focused routing run must include: submitted review with no inline
  findings, root comment, thread reply, and Clearance-authored root/reply.
- The focused concurrency run must include: same-key serialization,
  same-repository/different-PR parallelism, different-repository/same-number
  parallelism, exception release followed by later same-key progress, and
  same-key contention across two fresh event loops.
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run mypy voyager`
- `uv run pytest -q`
- `af validate --root .`
- `git diff --check`
- GitHub Actions and current-head Codex review must pass on the published PR.

## Acceptance Criteria

- [x] `pull_request_review.submitted` still produces exactly one Clearance route.
- [x] Root `pull_request_review_comment.created` produces no Clearance route.
- [x] Reply `pull_request_review_comment.created` still produces exactly one
      Clearance route.
- [x] Clearance-authored review events still do not self-trigger.
- [x] Clearance-authored root and reply review-comment events are both rejected
      before the new root/reply shape filter can schedule work.
- [x] Two concurrent stateful Clearance evaluations in the same running event
      loop for the same `(repository, PR)` cannot enter the
      marker-check/writeback section together.
- [x] Evaluations for different PRs in the same repository can enter concurrently.
- [x] Evaluations for the same PR number in different repositories can enter
      concurrently.
- [x] An exception in one evaluation does not strand the lock or block a later
      delivery permanently.
- [x] Same-key contention in each of two fresh event loops completes without a
      lock-bound-to-a-different-loop error; each loop retains same-key
      serialization.
- [x] No reviewer/model configuration changes.
- [x] Final focused and CI-equivalent full validation, including the P2
      fresh-loop regression, pass: 81 focused tests and 2025 full tests at
      86.67% coverage; ruff, format, mypy, Alfred, and diff hygiene are clean.
- [x] Final P2-delta implementation review passes at least 9.0/10 with no
      blockers: GLM 9.3, DeepSeek 9.5, and MiniMax 9.5.

## Approval

- [x] Operator pre-authorized the fix on 2026-08-02, subject to the required
      plan and implementation review gates.
- [x] Independent plan review Round 2 passed with no blockers: GLM 9.5,
      DeepSeek 9.4, and MiniMax 9.33 on 2026-08-02.
- [x] Pre-P2 independent implementation review passed with no blockers: GLM
      9.9, DeepSeek 9.4, and MiniMax 9.5 on 2026-08-02.
- [x] P2-delta COR-1610 review passed with no blockers: GLM 9.3, DeepSeek 9.5,
      and MiniMax 9.5 on 2026-08-02; evidence is in
      `.trinity/reviews/20260802-182719-voy-1837-p2-delta-cor1610-r1`.

## Execution Log

| Date | Action | Result |
|------|--------|--------|
| 2026-08-02 | Reproduced the reported duplicate discussion through GitHub REST data and local Clearance poll records. | Three same-head webhook polls all observed no marker, followed by three identical persisted replies. |
| 2026-08-02 | Created issue #292 and claimed branch `fix/292-clearance-reply-dedupe`. | Blueprint intake passed and the claim is unique. |
| 2026-08-02 | Ran Trinity plan review Round 1. | GLM 9.5 PASS and DeepSeek 9.2 PASS; MiniMax 7.6 FIX identified missing explicit BDD/fixture reversal and concurrency-seam coverage. Contract remediation is in progress. |
| 2026-08-02 | Ran Trinity plan review Round 2 after remediation. | GLM 9.5, DeepSeek 9.4, and MiniMax 9.33 all passed with no blockers. The CHG is Approved for RED. |
| 2026-08-02 | Completed independent RED and GREEN phases. | RED reproduced three intended behavioral failures; the separate implementation worker changed only `routing.py` and `pipeline.py`, and the focused suite passed 80 tests. |
| 2026-08-02 | Ran the full validation stack. | 2024 tests passed; ruff check, ruff format, mypy, Alfred validation, and diff hygiene passed. Alfred reported only the repository's known tag-vocabulary warning. |
| 2026-08-02 | Ran Trinity implementation review and COR-1610 scoring. | GLM 9.9, DeepSeek 9.4, and MiniMax 9.5 all passed with no blockers. |
| 2026-08-02 | Remediated the initial PR CI release-readiness failure. | Added the #293 `[Unreleased]` changelog entry; the targeted release-readiness test and the CI-equivalent 2024-test coverage run passed locally. |
| 2026-08-02 | Reproduced the Codex P2 fresh-event-loop regression. | A module-cached lock keyed only by repository and PR becomes bound when same-key work contends in the first `asyncio.run` loop; same-key contention in a second fresh loop then raises a bound-to-different-loop error. This is RED for the P2 contract. |
| 2026-08-02 | Approved the P2 lock-registry remediation. | Lock identity is `(running event loop, full repository, PR)` and the registry uses weak values so idle locks and loop keys expire. No `finally` pop is permitted. The subsequent implementation, local GREEN validation, and P2-delta review are recorded below. |
| 2026-08-02 | Implemented the P2 loop-qualified weak-lock registry. | `compute_clearance_automation` now uses a `WeakValueDictionary` keyed by running event loop, full repository, and PR; the fresh-loop regression is GREEN while same-loop serialization and distinct-key parallelism remain covered. |
| 2026-08-02 | Ran final local P2 validation. | Focused suite: 81 passed. CI-equivalent full suite: 2025 passed at 86.67% coverage. Ruff check, ruff format check, mypy, Alfred validation, and `git diff --check` were clean. Remote GitHub Actions CI remains pending. |
| 2026-08-02 | Ran P2-delta Trinity COR-1610 review. | GLM 9.3, DeepSeek 9.5, and MiniMax 9.5 all PASS with no blockers. Evidence: `.trinity/reviews/20260802-182719-voy-1837-p2-delta-cor1610-r1`. |

## Post-Change Review

- Pre-P2 and P2 local implementation, validation, and COR-1610 review are
  complete. The P2 delta uses the loop-qualified weak registry and passed its
  fresh-loop regression. Published-PR review, remote GitHub Actions CI, merge,
  and deployment remain pending.

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-08-02 | Recorded completed P2 implementation, fresh-loop GREEN validation, CI-equivalent coverage, and delta COR-1610 evidence. | Codex |
| 2026-08-02 | Added the Codex P2 loop-scoped weak-lock contract, fresh-event-loop RED regression, and pending final validation/review gates. | Codex |
| 2026-08-02 | Marked In Progress after implementation, full validation, and three-reviewer COR-1610 review passed. | Codex |
| 2026-08-02 | Marked Approved after all three Round 2 plan reviewers passed with no blockers; incorporated key-type and deterministic-assertion advisories. | Codex |
| 2026-08-02 | Remediated Round 1 plan-review findings with explicit BDD fixture reversal, deterministic existing test seam, exception-release coverage, and latency/provenance notes. | Codex |
| 2026-08-02 | Initial proposed change contract for issue #292. | Codex |
| 2026-08-30 | Lifecycle closeout: PR #293 merged as `c7aba1a9`; source issue #292 is closed and remote CI completed. Status changed from In Progress to Completed. | Codex |
