# Merge-Loop Approval Gate — Implementation Report

**Branch:** `feat/merge-loop-approval-gate` (based on `origin/main` = v0.10.0)
**Worktree:** `/Users/frank/Projects/voyager/.worktrees/merge-loop-approval`
**Motivation:** operator reversed zero-touch — every target repo's ruleset now
requires 1 approving review. The loop must only merge PRs the operator has
actually approved ("once I approve, it merges itself"), gating on GraphQL
`reviewDecision == "APPROVED"` (mirrors GitHub's own semantics: an approval
survives later pushes when "dismiss stale reviews" is off — that knob stays in
the repo ruleset, VOY-1840, not in the loop).

---

## Setup

- `uv sync` — base deps only installed `pytest`/`ruff`/`mypy` etc. are behind
  the `dev` **extra**, not a `dependency-groups` group (`uv sync --group dev`
  fails: "Group `dev` is not defined"). Used `uv sync --extra dev` instead.

## TDD

RED confirmed first: updated `tests/unit/test_merge_loop.py` (new fixtures,
new assertions, the `_apply_time_pr_state` import/rename) before touching
`voyager/core/merge_loop.py`, then ran the suite and got the expected
`ImportError: cannot import name '_apply_time_pr_state'` collection failure.
Implemented the source changes, re-ran → GREEN (129/129), then full
`tests/unit` (1259/1259).

---

## Code changes — `voyager/core/merge_loop.py`

1. **`_AGENT_PR_PAGE_QUERY`** (line ~239): added `reviewDecision` to the PR
   node selection, alongside `baseRefName`.

2. **`PrSnapshot`** (line ~98): new field
   `review_decision: str | None  # GraphQL reviewDecision; None if missing (fail closed)`.
   `snapshots_for_repo` (line ~562, ~586) populates it via
   `node.get("reviewDecision")` — same fail-closed-by-`None` style already
   used for `checks_state` (no type coercion; a present value is trusted as
   the GraphQL enum string, a missing key or explicit `null` becomes `None`).

3. **`should_merge`** (line ~183, guard at ~217-218): new guard placed
   immediately after the `base_ref` guard and before the `is_draft` guard,
   exactly as specified:
   ```python
   if s.review_decision != "APPROVED":
       return "not_approved"
   ```
   `None`, `"REVIEW_REQUIRED"`, and `"CHANGES_REQUESTED"` all fail closed to
   `"not_approved"` via the same `!=` comparison — no special-casing needed.
   Docstring's reason enumeration and body extended with the VOY-1840
   cross-reference: a repo with no required-review ruleset returns a null
   `reviewDecision` and is deliberately unmergeable by the loop until that
   ruleset requires an approving review.

4. **Apply-time recheck** — chose the **combined-helper** option over a
   parallel one:
   - `_PR_BASE_QUERY` extended with `reviewDecision` alongside `baseRefName`
     (single node, single round trip).
   - `_current_base_ref(gql, repo, number) -> str | None` renamed to
     `_apply_time_pr_state(gql, repo, number) -> tuple[str | None, str | None]`
     returning `(baseRefName, reviewDecision)`. Each field fails closed to
     `None` **independently** (a non-str `baseRefName` doesn't blank out a
     valid `reviewDecision` and vice versa); a full read fault or missing
     `pullRequest` still returns `(None, None)`, preserving the existing
     `base_freshness_unreadable` contract for that case.
   - `run_merge_loop`'s live-path block (line ~845) now calls
     `_apply_time_pr_state` once and checks, in order: `current_base is None`
     → `base_freshness_unreadable`; retarget → `base_retargeted_at_apply`;
     **then** `current_review_decision != "APPROVED"` →
     `("skipped", "approval_revoked_at_apply")`, before the `_base_behind_by`
     freshness re-read — exactly the placement the spec asked for ("in the
     same block, before the freshness re-read"). Zero mutations, no intent
     audit line, no cap slot consumed (the skip happens before
     `approved += 1`).

   **Why combined over parallel:** `_PR_BASE_QUERY` already returns a single
   `pullRequest` node; a "parallel helper" would mean two functions each
   issuing their own GraphQL round trip against the same node just to read
   different fields off it — doubling network cost and doubling the places a
   transient read fault has to be handled identically. One helper, one query,
   two fields, one fail-closed contract per field. All four call sites
   (`tests/unit/test_merge_loop.py` import + 5 direct calls in
   `TestApplyTimePrState`, plus the one production call site in
   `run_merge_loop`) were updated to the new name/shape.

5. **Docstrings**: `should_merge`'s docstring documents the `not_approved`
   reason (with the VOY-1840 ruleset note) and `run_merge_loop`'s docstring
   now (a) describes the combined apply-time re-read, (b) documents
   `approval_revoked_at_apply` ordering relative to the other apply-time
   skips, and (c) adds an explicit gate-list paragraph ending with the
   operator end-state: *"approve once, and the loop completes the merge."*

## Tests — `tests/unit/test_merge_loop.py`

- Import swap: `_current_base_ref` → `_apply_time_pr_state`.
- `snap()` default now includes `"review_decision": "APPROVED"`; `_pr_node()`
  gained a `review_decision="APPROVED"` param feeding a new `reviewDecision`
  key; `_fake_gql()` gained `current_review_decision="APPROVED"` and now
  returns it on the `_PR_BASE_QUERY` branch — all existing green-path tests
  stayed green with zero other changes.
- `TestShouldMerge`: 3 new parametrized truth-table rows
  (`None`/`"REVIEW_REQUIRED"`/`"CHANGES_REQUESTED"` → `"not_approved"`) plus
  `test_not_approved_fires_before_draft_check` (ordering: unapproved **and**
  draft → `"not_approved"`, not `"draft"`).
- `TestSnapshotsForRepo`: asserts `s.review_decision == "APPROVED"` on the
  green fixture; new `test_review_decision_none_when_missing` (node lacking
  the `reviewDecision` key → `None`).
- `TestCurrentBaseRef` → renamed `TestApplyTimePrState`, tests ported to the
  tuple return, plus 2 new cases for independent field fail-closing
  (non-str `reviewDecision`, missing `reviewDecision` key).
- New `TestApplyTimeApprovalRevoked` class: approve-then-revoke race (snapshot
  `APPROVED`, apply-time re-read `"REVIEW_REQUIRED"` → `("skipped",
  "approval_revoked_at_apply")`, zero mutations, no `merge_intent` audit
  line) and the unchanged-`APPROVED` merge-proceeds case.
- `TestRunMergeLoop::test_green_but_unapproved_pr_is_skipped`: a fully-green
  PR with `review_decision="REVIEW_REQUIRED"` → exactly one decision,
  `("skipped", "not_approved")`.
- Existing `test_dry_run_never_calls_pr_base_query` (dry-run issues no
  apply-time reads at all, including the new combined query) was **not**
  modified and still passes — it already asserted zero calls to
  `_PR_BASE_QUERY`, which now also carries `reviewDecision`.

---

## Docs

- **`rules/VOY-1839-PRP-Countdown-Merge-Loop-Autonomous-Agent-PR-Merge.md`**:
  merge-predicate list gains item 2, "Human approval" (GraphQL
  `reviewDecision == "APPROVED"`, snapshot + apply-time re-verified, null-ruleset
  repos deliberately unmergeable, approve-then-revoke race handled); remaining
  items renumbered 3–7. Change History row added, dated today, referencing
  PR #303. `Last updated`/`Last reviewed` bumped to today.
- **`rules/VOY-1840-SOP-Countdown-Merge-Loop-Launchd-Deployment.md`**:
  - Added a note directly above the Target-repo GitHub Configuration table:
    zero-touch is retired, `required_approving_review_count` must stay at (or
    be raised to) `1`, not lowered to `0`.
  - Edited that table row accordingly (was "1 → 0 / removes the human-approve
    gate"; now "stays at/raised to 1 / the loop's gate needs a real ruleset
    behind it").
  - Corrected the Pitfalls bullet that claimed "no human terminal gate" (now
    false) and added a new bullet documenting `not_approved` /
    `approval_revoked_at_apply` semantics.
  - Change History row added, referencing PR #303. `Last updated`/`Last
    reviewed` bumped to today.
  - Left the `## Steps` prose (Step 1's "There is no LLM gate..." line, `##
    What Is It?` predicate summary) untouched — out of the requested scope
    (table note + pitfalls line + Change History row) and not directly
    contradicted by the change (Step 1's sentence is specifically about the
    *LLM* gate, which is still absent).
- **`CHANGELOG.md`**: new `[Unreleased]` → "Changed — Merge-loop approval gate
  (BREAKING for zero-touch flows)" entry, referencing PR #303, documenting the
  new `not_approved` / `approval_revoked_at_apply` skip reasons and the
  operator-facing ruleset requirement.
- `af index --root .` regenerated `rules/VOY-0000-REF-Document-Index.md`
  (date bump only, no structural change).
- `af validate --root .`: **149 documents checked, 0 issues** (1 pre-existing,
  unrelated warning: ~90 out-of-vocabulary tag instances tracked under
  FXA-2315, present before this change).

---

## Verification

| Command | Result |
|---|---|
| `.venv/bin/pytest tests/unit/test_merge_loop.py -v` | **129 passed** |
| `.venv/bin/pytest tests/unit -q` | **1259 passed** (6 pre-existing unrelated warnings) |
| `uvx ruff@latest format --check .` | clean (ran `format` once to fix 2 files' line-wrapping, then check passed) |
| `.venv/bin/ruff check` | All checks passed! |
| `.venv/bin/mypy voyager/core/merge_loop.py` | Success: no issues found in 1 source file |
| `af validate --root .` | 149 documents, 0 issues, 1 pre-existing warning |

No `--no-verify` was used — pre-commit hooks (ruff, ruff format, mypy, trailing
whitespace, etc.) passed clean on all three commits.

---

## Commits (not pushed)

```
4e10fe4 docs(changelog): unreleased entry for merge-loop approval gate (#303)
6e3cb6e docs(rules): document merge-loop approval gate (zero-touch retired)
8a6982f feat(merge-loop): gate merges on operator approval (reviewDecision)
```

## Files touched

- `/Users/frank/Projects/voyager/.worktrees/merge-loop-approval/voyager/core/merge_loop.py`
- `/Users/frank/Projects/voyager/.worktrees/merge-loop-approval/tests/unit/test_merge_loop.py`
- `/Users/frank/Projects/voyager/.worktrees/merge-loop-approval/rules/VOY-1839-PRP-Countdown-Merge-Loop-Autonomous-Agent-PR-Merge.md`
- `/Users/frank/Projects/voyager/.worktrees/merge-loop-approval/rules/VOY-1840-SOP-Countdown-Merge-Loop-Launchd-Deployment.md`
- `/Users/frank/Projects/voyager/.worktrees/merge-loop-approval/rules/VOY-0000-REF-Document-Index.md` (auto-generated)
- `/Users/frank/Projects/voyager/.worktrees/merge-loop-approval/CHANGELOG.md`

## Fix note — review round 2 (precedence pin)

Coordinator review approved with one Important finding: the apply-time block
checks `base_retargeted_at_apply` before `approval_revoked_at_apply`, and the
order needed to read as deliberate, not accidental.

- Added a comment directly above the `_apply_time_pr_state` call in
  `run_merge_loop` (`voyager/core/merge_loop.py`, ~line 844): both fields come
  from one snapshot read, and base safety is checked first on purpose — when a
  re-read shows both a moved base and a lost approval, the recorded reason is
  the base one.
- Added `TestApplyTimeApprovalRevoked::test_retarget_and_revoked_approval_together_reports_retarget`
  in `tests/unit/test_merge_loop.py`: apply-time re-read returns both
  `current_base_ref="release/1.x"` and `current_review_decision="REVIEW_REQUIRED"`
  → asserts `("skipped", "base_retargeted_at_apply")`, zero `merge_pr` calls,
  no `merge_intent` audit line.
- Re-ran `.venv/bin/pytest tests/unit/test_merge_loop.py -v`: **130/130
  passed** (was 129; +1 new test). `uvx ruff@latest format --check .`,
  `.venv/bin/ruff check`, `.venv/bin/mypy voyager/core/merge_loop.py` all
  clean.
- Committed as `test(merge-loop): pin apply-time recheck precedence`
  (`309f14b`) — no logic change, comment + test only. No `--no-verify` used.

## Concerns / follow-ups

- PR #303 is referenced in docs/changelog per the task's instruction; no PR
  was actually opened by this task (branch not pushed, per instructions).
  Whoever pushes/opens the PR should confirm the number lands as #303 or
  update the three references if GitHub assigns a different one.
- `VOY-1840`'s `## Steps` §1 and the top-of-doc `## What Is It?` prose still
  describe the predicate without mentioning approval — left alone as out of
  the requested scope (only the ruleset table note + pitfalls + Change
  History were asked for). Worth a follow-up doc pass if a reader relying
  only on those sections would be misled.
- Deployment-side work (actually flipping `required_approving_review_count`
  to 1 on real target repos, e.g. `frankyxhl/fx_bin`) is an operator action
  outside this code/docs change, per VOY-1840's Rollout Gate — not performed
  here.
