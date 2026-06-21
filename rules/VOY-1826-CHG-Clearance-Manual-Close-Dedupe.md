# CHG-1826: Clearance Manual-Close Dedupe

**Applies to:** VOY project
**Last updated:** 2026-06-21
**Last reviewed:** 2026-06-21
**Status:** Completed
**Date:** 2026-06-21
**Requested by:** Frank Xu via issue #197
**Priority:** P2
**Change Type:** Normal
**Targets:** `voyager/bots/clearance/close_reason.py`, `voyager/bots/clearance/pipeline.py`, Clearance tests
**Closes:** #197
**Related:** VOY-1813, OCO-2508, OCO-2509

---

## What

Add a narrow cross-head dedupe guard for Clearance Stage 1.5 manual-close
fallback replies when a review thread is semantically `RESOLVED`, GitHub still
shows it visually unresolved, `viewerCanResolve=false`, and no authorized
fallback resolver can call `resolveReviewThread`.

The fix must suppress repeated manual-close thread replies across new PR heads
only while the latest Clearance semantic state for that review thread remains
the same manual-close `RESOLVED` state.

## Why

Current close-reason markers are scoped to head SHA:

```text
clearance-close-reason:{thread.id}:{head_sha[:12]}
```

That is correct for current-head verdict evidence, but it means the unsupported
manual-close fallback can repost under the same review thread after every new
commit. The self-resolve and delegated-resolver paths avoid this because they
call `resolveReviewThread`; once GitHub reports `isResolved=true`, later heads
skip the thread. Manual-close does not mutate GitHub state, so it needs its own
idempotency rule.

## Out of Scope

- Changing normal `resolveReviewThread` behavior.
- Adding human or collaborator PR authors as fallback resolver identities.
- Suppressing OPEN or NEEDS_HUMAN_JUDGMENT review feedback.
- Changing PR-level Clearance summary update cadence.
- Removing head SHA from the existing `clearance-close-reason` marker globally.

## Surfaces

| # | Surface | Change |
|---|---------|--------|
| 1 | `voyager/bots/clearance/close_reason.py` | Add a dedicated manual-close marker helper that is distinct from `clearance-close-reason`, and render it in manual-close replies. |
| 2 | `voyager/bots/clearance/pipeline.py` | In the `viewerCanResolve=false` manual-close branch, suppress duplicate thread replies only when the latest relevant Clearance semantic state is still manual-close RESOLVED. Reuse the fresh thread-comment fetch/cache already used by current-head verdict dedupe. |
| 3 | Tests | Add regression coverage for unchanged RESOLVED across heads, RESOLVED -> OPEN -> RESOLVED, and out-of-order comment arrays ordered by `createdAt`. |

## Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Use a dedicated manual-close marker instead of broadening `clearance-close-reason`. | `clearance-close-reason` also appears in true resolve/delegated evidence. Reusing it for cross-head manual-close dedupe risks confusing successful resolution evidence with unsupported-capability guidance. |
| D2 | Dedupe by latest relevant Clearance semantic state, not by "thread ever had a manual-close marker." | A review thread can reopen with new feedback. RESOLVED -> OPEN -> RESOLVED must allow a fresh manual-close reply. |
| D3 | Use GitHub comment chronology such as `createdAt` to determine latest state. | Fixture array order is not a durable contract. The implementation must not pass only because test data happened to arrive sorted. |
| D4 | Manual-close replies should keep the existing current-head `clearance-close-reason` marker and add a second `clearance-manual-close:{thread_id}:{head}` marker. | Keeping the current marker preserves same-head duplicate suppression. The new marker is the only cross-head manual-close dedupe key. |
| D5 | The cross-head scanner must ignore `clearance-close-reason`. | Normal self-resolve and delegated-resolve comments use that marker too. Treating it as manual-close evidence could suppress a later manual-close reminder after a thread reopens. |
| D6 | Do not add backwards-compatible scanning for old manual-close comments that lack `clearance-manual-close`. | A one-time duplicate after deploy is acceptable and safer than interpreting historical true-resolution evidence as manual-close evidence. |

## Implementation Plan

1. Add a manual-close marker helper with the concrete format
   `clearance-manual-close:{thread_id}:{head_sha[:12]}`.
2. Render manual-close replies with both markers:
   `clearance-close-reason:{thread_id}:{head_sha[:12]}` for the existing
   current-head RESOLVED guard, and
   `clearance-manual-close:{thread_id}:{head_sha[:12]}` for cross-head
   manual-close dedupe.
3. Add a helper that scans only Clearance-authored review-thread comments for
   relevant state markers:
   - `clearance-manual-close:{thread_id}:*` means manual-close `RESOLVED`.
   - `clearance-thread-conclusion:{thread_id}:*` plus OPEN or
     NEEDS_HUMAN_JUDGMENT body evidence means a later non-resolved state.
   - `clearance-close-reason:{thread_id}:*` is ignored by this cross-head
     scanner.
4. Determine the latest relevant state by GitHub `createdAt`, not by comment
   array order. Missing or invalid timestamps should make that comment less
   authoritative than timestamped evidence rather than inventing order from the
   array.
5. Reuse the fresh `pull_request_review_threads` fetch/cache that backs
   `_current_head_verdict_reply_skip_reason`; do not add another network fetch
   for the same PR pass.
6. In the manual-close fallback branch, suppress the reply only when the latest
   relevant state is manual-close `RESOLVED`.
7. If the fresh thread fetch or scanner fails, fail open by posting the
   manual-close reply and logging the dedupe failure. This preserves real review
   feedback at the cost of possible noise.
8. Ensure any later OPEN or NEEDS_HUMAN_JUDGMENT Clearance marker resets the
   suppression so a subsequent RESOLVED state can post again.
9. Keep PR-level Clearance summary updates unchanged.

## Testing / Verification

- Add or update unit coverage for marker helpers.
- Add BDD or unit coverage proving that the same thread with unchanged
  manual-close RESOLVED state across two head SHAs posts only once.
- Add coverage for RESOLVED -> OPEN -> RESOLVED proving the second RESOLVED
  manual-close reply is posted.
- Add coverage where comment array order is deliberately scrambled while
  `createdAt` preserves the true chronological order.
- Add coverage proving a normal prior `clearance-close-reason` comment from a
  self-resolve or delegated-resolve path does not suppress a later manual-close
  reply after the thread has reopened.
- Add coverage proving fresh thread-comment fetch/scanner failure fails open and
  posts the manual-close reply.
- Run the focused Clearance test subset.
- Run the project validation stack required by the implementation run.

## Rollback Plan

Revert the implementation commits for this CHG. Existing historical
manual-close comments remain harmless GitHub thread history. No data migration
is required because markers live in comments and the change should be additive
to parser logic.

## Acceptance Criteria

- [x] Existing `clearance-close-reason` semantics remain head-scoped and are not
      repurposed as the manual-close dedupe key.
- [x] Manual-close comments include a dedicated
      `clearance-manual-close:{thread_id}:{head_sha[:12]}` marker while
      preserving same-head `clearance-close-reason` behavior.
- [x] Manual-close fallback replies are deduplicated across heads while the
      latest relevant Clearance state remains manual-close RESOLVED.
- [x] RESOLVED -> OPEN -> RESOLVED emits a second manual-close reply.
- [x] Dedupe uses reliable comment chronology such as `createdAt`, not array
      order.
- [x] Prior normal `clearance-close-reason` evidence from true resolve paths does
      not suppress later manual-close reminders after a thread reopens.
- [x] Cross-head manual-close dedupe failure fails open and does not hide
      feedback.
- [x] OPEN and NEEDS_HUMAN_JUDGMENT review feedback is never hidden by the
      manual-close dedupe.
- [x] Focused tests and project validation pass.

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-06-21 | Initial proposed CHG for issue #197 | Codex |
| 2026-06-21 | Marked CHG completed after PR #198 validation | Codex |
