# CHG-1838: Suppress Stale Clearance Verdict Writeback

**Applies to:** VOY project
**Last updated:** 2026-08-30
**Last reviewed:** 2026-08-30
**Status:** Completed
**Date:** 2026-08-02
**Requested by:** Frank Xu
**Priority:** High
**Change Type:** Normal
**Scheduled:** ASAP
**Related:** VOY-1809, VOY-1826, VOY-1837, `frankyxhl/order_system_django#50`
**Reviewed by:** VOY-1838-plan-r1 and VOY-1838-code-r2 — PASS (glm, deepseek, minimax)

---

## What

Prevent Clearance from publishing an `OPEN` or `NEEDS_HUMAN_JUDGMENT`
thread-conclusion reply when a newer decision-relevant in-thread comment appears
after the verdict snapshot was classified but before the reply is written.

Every snapshot, including `RESOLVED` snapshots, will retain the database IDs of
the thread comments it observed.
Immediately before non-resolved verdict writeback, Clearance will use its
existing fresh-thread fetch and suppress the stale reply when it finds an
unobserved comment from either the pull-request author or Codex.

## Why

On 2026-08-02, `frankyxhl/order_system_django#50` exposed a snapshot-to-write
race on thread `PRRT_kwDOCSw1n86Vwmwl`:

1. At `12:03:00Z`, Clearance classified the thread `OPEN`; no author reply was
   present.
2. At `12:03:05Z`, author reply `3698938968` arrived.
3. In the same second, the first run wrote stale `OPEN` reply `3698938994`.
4. The serialized reply-triggered run then classified `RESOLVED` and wrote
   reply `3698939339` at `12:03:11Z`.

VOY-1837 serialization worked: the runs were sequential and the duplicate
fan-out defect did not recur. The remaining defect is time-of-check/time-of-use
staleness inside one run. The lower database ID establishes that the author
reply was created before the stale Clearance reply, but GitHub does not expose
the exact timestamp of the intervening fresh-thread query. The guard therefore
closes the verified missing check whenever the new evidence is visible to that
query; it deliberately does not claim atomicity across GitHub's
refresh-to-create window or indexing latency.

The current pre-writeback refresh checks the head SHA and Clearance markers but
does not invalidate a verdict after new author or Codex evidence becomes visible.

## Impact Analysis

- **Systems affected:** `voyager/bots/clearance/models.py`,
  `voyager/bots/clearance/pipeline.py`, focused Clearance unit/BDD coverage,
  and the Unreleased changelog.
- **User impact:** a queued webhook may still recompute a later verdict, but
  the older run will no longer leave a contradictory stale non-resolved reply
  immediately before it.
- **API impact:** no new GitHub API request is required; the guard reuses the
  `verdict_reply_dedupe_cache["fresh_threads"]` response populated through the
  existing `_current_head_verdict_reply_skip_reason` writeback path.
- **Persistence impact:** new snapshots add an optional list of observed thread
  comment database IDs. Existing JSONL remains readable through Pydantic
  defaults and `extra="allow"` compatibility. The field is populated uniformly
  for every verdict to keep one serializer path; a GraphQL page contributes at
  most 100 integer IDs and typical review threads contain far fewer than 20.
- **Security/privacy:** only GitHub comment database IDs already present in the
  fetched thread are persisted; comment bodies are not newly persisted.
- **Downtime required:** No.
- **Rollback plan:** revert the snapshot evidence field and stale-evidence
  guard. Existing JSONL entries containing the optional field remain readable.
  For every affected repository/PR named by a `stale_thread_evidence_skip` log
  during the rollback window, post `/clearance` on the PR with
  `gh pr comment <number> --repo <owner/repo> --body '/clearance'`, then verify a
  subsequent Clearance poll/reply recomputed the thread before closing rollback.

## Decision-Relevant Actors

- **PR author:** compare the fresh comment's `author.login` with
  `pull_request.user.login` via the existing `logins_equivalent` helper, which
  normalizes GitHub App bare and `[bot]` forms without relying on
  `authorAssociation`.
- **Codex:** use the existing `is_codex_login` helper, including configured test
  bot identities.
- **Clearance:** exclude comments recognized by `_is_clearance_comment`.
- **Other reviewers/maintainers:** not decision-relevant for this guard because
  `latest_author_reply(..., author_login=...)` does not use them during
  classification.

If both a new author reply and a new Codex follow-up are present, either one is
sufficient to suppress the stale verdict. A comment matching both actor rules
(for example, a Codex-authored PR) is deduplicated by database ID and produces
one skip. The action/log deterministically selects the lowest database ID among
all unobserved relevant comments, regardless of actor; this is a stable witness,
not a claim about `createdAt` chronology or actor precedence.

## Decision Matrix

| Verdict path | Fresh evidence after snapshot | Expected action |
|---|---|---|
| `OPEN` | PR-author reply | Skip stale thread conclusion |
| `OPEN` | Codex follow-up | Skip stale thread conclusion |
| `NEEDS_HUMAN_JUDGMENT` | PR-author reply | Skip stale thread conclusion |
| `NEEDS_HUMAN_JUDGMENT` | Codex follow-up | Skip stale thread conclusion |
| `OPEN` / `NEEDS_HUMAN_JUDGMENT` | Clearance marker only | Existing marker-dedupe behavior applies |
| `OPEN` / `NEEDS_HUMAN_JUDGMENT` | unrelated reviewer reply | Continue; current classifier does not treat it as decision evidence |
| `OPEN` / `NEEDS_HUMAN_JUDGMENT` | no new evidence | Continue existing writeback |
| `RESOLVED` | any | N/A for this slice; Stage 1.5 owns resolved comments and mutations |
| Any non-resolved verdict | fresh-thread fetch fails | Fail open with the existing warning behavior; do not invent evidence |
| Any non-resolved verdict | new author reply and Codex follow-up | Skip once; record the lowest new relevant comment ID |

## Implementation Plan

1. **RED — evidence race regression.** Extend
   `tests/clearance/test_pipeline_thread_verdict_writeback.py` with a
   parameterized matrix proving that an unobserved PR-author reply or Codex
   follow-up suppresses both `OPEN` and `NEEDS_HUMAN_JUDGMENT`, while no-new,
   unrelated-reviewer, Clearance-only, and refresh-failure cases preserve the
   current behavior. The authoritative unit matrix adds six parameterized rows;
   run the focused tests and record the exact command plus failure summary in
   this CHG's Execution Log without committing a generated transcript.
2. **RED — pipeline wiring.** Extend `_StubGitHubAppClient` in
   `tests/bdd/step_defs/test_swm_pipeline_steps.py` so its second thread fetch
   can expose an independent deep-copied thread containing an author reply only
   on the second fetch, tracked by an explicit fetch counter. Add one wiring
   smoke scenario in
   `tests/bdd/features/swm_pipeline.feature` proving the first run posts no
   non-resolved reply and records a stale-evidence skip; the unit matrix remains
   authoritative for both actor and verdict axes. This RED also confirms the
   existing fresh-thread response is available at the verdict writeback call
   site.
3. **GREEN — snapshot evidence.** Add an optional
   `observed_thread_comment_ids` field to `Evidence` and populate it from the
   exact thread comment nodes used for classification.
4. **GREEN — stale-evidence guard.** Before rendering a non-resolved verdict
   reply, compare the fresh thread against the observed ID set using the actor
   rules above. First call the unchanged
   `_current_head_verdict_reply_skip_reason`, then inspect the populated
   `verdict_reply_dedupe_cache["fresh_threads"]` with a pure lookup helper; do
   not change the skip-reason helper's signature or add another GitHub API call.
5. **GREEN — observability.** Return a skipped writeback action under
   `automation.thread_verdict_comment_actions` with
   `skip_reason="new thread decision evidence after snapshot"`, the new comment
   lowest new relevant comment ID, and emit a structured
   `stale_thread_evidence_skip` event through the
   existing pipeline logger without comment body content.
6. **REFACTOR.** Keep the implementation in existing helpers and models; add no
   dependency, retry loop, debounce, sleep, or speculative abstraction.
7. **Documentation.** Add an Unreleased `Fixed` entry to `CHANGELOG.md` naming
   the stale-evidence suppression and its scope.
8. **Validation.** Run focused unit and BDD tests, the full pytest suite, Ruff
   lint/format checks, mypy, `git diff --check`, and `af validate --root .`.
9. **Review and delivery.** Run COR-1602/COR-1610 implementation review, clean
   the worktree, commit and push to `fork`, create a ready-for-review PR as
   `ryosaeba1985`, and run the COR-1615 current-head review loop.

## Implementation Order

1. **RED — tests-only worker.** A dedicated test-writer agent may modify only
   tests, fixtures, and test helpers. It must run the focused regression against
   the unchanged production code and preserve the failing output.
2. **RED quality gate — orchestrator.** Confirm each failure is caused by the
   missing stale-evidence behavior, inspect for vacuous assertions, and verify
   that no production file changed.
3. **GREEN — implementation-only worker.** A distinct implementer may read this
   CHG, the committed RED test files, and production source, but not the
   test-writer's commentary. It may modify production/supporting files and
   `CHANGELOG.md`, must not weaken the RED tests, and must implement only enough
   to make them pass.
   The shared worktree is the handoff: the orchestrator provides only the CHG,
   failing test paths, and allowed production surfaces; test-writer commentary
   is not forwarded.
4. **GREEN verification — orchestrator.** Re-run the focused tests, inspect the
   actor/evidence matrix directly, and then run the full validation stack.
5. **REFACTOR — implementation worker if needed.** Make only
   behavior-preserving simplifications justified by the diff; rerun focused
   tests after any refactor.
6. **REVIEW — Trinity panel.** Review the final diff under COR-1610. Every
   viable reviewer must score at least 9.0 with no blocker before commit and PR
   publication.

## Acceptance Criteria

- [x] A snapshot records the IDs of the exact thread comments used to classify
      its verdict.
- [x] A new PR-author reply suppresses stale `OPEN` and
      `NEEDS_HUMAN_JUDGMENT` replies.
- [x] A new Codex follow-up suppresses stale `OPEN` and
      `NEEDS_HUMAN_JUDGMENT` replies.
- [x] When both actor types add new comments, one deterministic skip is
      recorded using the lowest new relevant database ID.
- [x] Unrelated reviewer replies and Clearance's own marker comments do not
      become decision evidence.
- [x] The skip action and structured log identify repository, PR, thread, head,
      verdict, and new comment ID without copying comment-body content.
- [x] A fresh-thread fetch failure fails open, preserves existing writeback
      behavior, and emits the existing warning without a false stale-evidence
      claim.
- [x] Existing same-head marker dedupe, valid `OPEN -> RESOLVED` progression,
      and Stage 1.5 resolved behavior remain unchanged.
- [x] No model, dependency, configuration, migration, or deployment change is
      introduced.
- [x] All declared validation commands pass.

## Out of Scope

- Editing or deleting previously published GitHub review comments.
- Replacing append-only verdict history with in-place comment updates.
- Debounce windows or cross-process event coalescing.
- Changing Stage 1.5 `RESOLVED` comment or thread-resolution semantics.
- Paginating review threads with more than 100 comments.
- Eliminating GitHub GraphQL indexing lag or the non-atomic interval between the
  final fresh-thread read and `createReviewThreadReply`; this guard narrows that
  interval but cannot make two GitHub API calls transactional.
- Version bump, release, or production deployment.

## Validation Commands

```bash
uv run pytest -q tests/clearance/test_pipeline_thread_verdict_writeback.py
uv run pytest -q tests/bdd/step_defs/test_swm_pipeline_steps.py
uv run pytest -q
uv run ruff check voyager tests
uv run ruff format --check voyager tests
uv run mypy voyager
git diff --check
af validate --root .
```

## Approval

- [x] Operator approved the recommended stale-evidence guard on 2026-08-02.
- [x] COR-1602 plan-review panel: GLM 9.1, DeepSeek 9.2, and MiniMax 9.1,
      all PASS with no blocker in Round 2.
- [x] Implementation review passed under COR-1610.

## Execution Log

| Date | Action | Result |
|---|---|---|
| 2026-08-02 | Reproduced production timeline and traced writeback path | Sequential stale-snapshot race confirmed; VOY-1837 lock remained effective |
| 2026-08-02 | Plan review R1 | DeepSeek 9.5 PASS; MiniMax 9.1 PASS; GLM timed out at 360s, so the frozen three-seat quorum was inconclusive. Folded all actionable advisories into this revision. |
| 2026-08-02 | Plan review R2 | GLM 9.1, DeepSeek 9.2, and MiniMax 9.1 all PASS with no blocker. GLM's R1 timeout was retried after the configured 600-second backoff; no seat was treated as an abstention or removed. |
| 2026-08-02 | RED unit matrix | `uv run pytest -q tests/clearance/test_pipeline_thread_verdict_writeback.py`: 6 failed, 22 passed. All six rows reached the intentionally missing `pr_author_login` boundary. |
| 2026-08-02 | RED BDD wiring scenario | `uv run pytest -q tests/bdd/step_defs/test_swm_pipeline_steps.py`: 1 failed, 86 passed. The second fresh-thread fetch observed the new author reply, but unchanged production still posted one stale `OPEN` reply. |
| 2026-08-02 | Focused GREEN | Unit writeback suite: 28 passed. Pipeline BDD suite: 87 passed. The orchestrator independently reproduced both results after implementation. |
| 2026-08-02 | COR-1610 implementation review R1 | GLM 9.4, DeepSeek 9.4, and MiniMax 9.75 all PASS with no blocker. The separate read-only audit and GLM identified an explicit mixed-actor coverage gap, so the artifact advanced to test hardening and R2 despite the PASS scores. |
| 2026-08-02 | Test hardening | Expanded the authoritative unit matrix to 10 rows covering no-new, Clearance-only, mixed author/Codex evidence, and legacy snapshots; strengthened action/log body-leak assertions; added known-limitation `RESOLVED` snapshot coverage. |
| 2026-08-02 | Full validation after hardening | Focused suites: 32 unit, 87 BDD, and 43 known-limitation tests passed. Full pytest: 2036 passed with 6 pre-existing warnings; Ruff lint passed; Ruff format checked 198 files; mypy passed across 81 source files; `git diff --check` passed; Alfred checked 147 documents with 0 issues and 1 known OOV warning. |
| 2026-08-02 | COR-1610 implementation review R2 | GLM 9.6, DeepSeek 9.3, and MiniMax 9.9 all PASS with no blocker. Because the installed Trinity adapter does not yet expose a strict COR-1610 template, the frozen panel received the complete rubric through an explicit review prompt; the raw decision matrices, rather than the adapter's legacy synthesis label, are authoritative. |

## Post-Change Review

- **Goal achieved:** The stale non-resolved writeback is suppressed whenever the
  existing refresh exposes new PR-author or Codex evidence; implementation,
  validation, and review are complete.
- **Unexpected side effects:** None observed. Existing fail-open, same-head
  transition, and Stage 1.5 behavior remained green.
- **Follow-up actions:** None for this CHG. PR #295 merged as `d4d18958`, and
  the change is included in the current Wukong wheel.

## Review Unit

```yaml
review_id: VOY-1838-plan-r1
target: rules/VOY-1838-CHG-Suppress-Stale-Clearance-Verdict-Writeback.md
mechanism: decision_matrix
rubric: COR-1609
threshold: weighted_avg >= 9.0 for every viable reviewer, with no blockers
reviewers: [glm, deepseek, minimax]
quorum: 3
abstention_rule: abstain_blocks
tie_break: re_review
disagreement_threshold: any
blind: true
```

### Implementation Review Unit

```yaml
review_id: VOY-1838-code-r2
target: working-tree diff for VOY-1838
mechanism: decision_matrix
rubric: COR-1610
threshold: weighted_avg >= 9.0 for every viable reviewer, with no blockers
reviewers: [glm, deepseek, minimax]
quorum: 3
abstention_rule: abstain_blocks
tie_break: re_review
disagreement_threshold: any
blind: true
```

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-08-02 | Completed local implementation, full validation, test hardening, and COR-1610 implementation review R2; PR publication remains in progress | Codex |
| 2026-08-02 | Marked In Progress after the independent RED worker and orchestrator reproduced the missing stale-evidence guard | Codex |
| 2026-08-02 | Marked Approved after plan-review R2 passed 3/3; recorded the refresh-to-write/eventual-consistency boundary and folded remaining advisories into the contract | Codex |
| 2026-08-02 | Plan-review R1 remediation: pinned actor/cache/log rules, simultaneous evidence, fail-open acceptance, BDD targets, distinct RED/GREEN workers, changelog work, and rollback refresh | Codex |
| 2026-08-02 | Initial contract from the verified `order_system_django#50` stale-snapshot incident | Codex |
| 2026-08-30 | Lifecycle closeout: implementation PR #295 merged as `d4d18958`; the stale-verdict writeback guard shipped with its reviewed tests. Status changed from In Progress to Completed. | Codex |
