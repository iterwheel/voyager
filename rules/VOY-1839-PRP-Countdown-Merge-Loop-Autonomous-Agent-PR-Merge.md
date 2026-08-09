# PRP-1839: Countdown Merge-Loop — Autonomous Agent-PR Merge

**Applies to:** VOY project — Countdown PR automation
**Last updated:** 2026-08-09
**Last reviewed:** 2026-08-09
**Status:** Draft
**Related:** VOY-1831 (resolve-loop PRP), VOY-1835 (resolve-loop launchd deployment), CHG-1829/1830 (machine-account identity lineage)

---

## What Is It?

A multi-repo autonomous merge loop, `vyg countdown merge-loop`, that runs as the
fixed machine account (`iterwheel-countdown-bot`) and **directly rebase-merges
agent-authored PRs when every deterministic condition is green** — no human
approve, no GitHub auto-merge arming. It reuses the resolve-loop skeleton:
allowlist ceiling, open-PR enumeration, single-instance lock, per-run mutation
cap, redacted audit trail, adaptive launchd daemon, fail-closed env kill switch.

End-to-end pipeline once deployed (fx_bin):

```
codex review → agent fixes → clearance verdict → resolve-loop clears threads
→ merge-loop rebase-merges → cd-release semantic-release → PyPI
```

The operator's only remaining controls are the `MERGE_LOOP_ENABLED` kill switch
and the repo allowlist. There is deliberately no per-PR human gate.

---

## Problem

The countdown pipeline automates review-thread resolution but stops at the merge:
a fully-green agent PR (CI green, threads resolved, clearance ready) still waits
for a human to click merge. The operator wants zero-touch: when nothing is wrong,
the PR merges itself. GitHub auto-merge cannot deliver this alone because the
target repo's rulesets require an approving review, and "arm and wait for
approve" still leaves one manual action per PR.

## Proposed Solution

### Merge predicate (all deterministic, fail-closed — any miss ⇒ skip + audit)

A PR is merged only when ALL of the following hold on the **current head**:

1. **Author allowlist:** PR author is `ryosaeba1985` (the agent account) or
   one of the operator-local extras in `VOYAGER_MERGE_EXTRA_AUTHORS`
   (`merge_allowed_authors()`, mirroring the `VOYAGER_MERGE_EXTRA_REPOS`
   repo-ceiling extension). Extras are parsed FAIL-CLOSED (a malformed entry
   raises) and matched case-insensitively, lowercase-normalized. This is the
   mechanism for authorizing dependabot dependency-bump PRs on fx_bin — use
   the GraphQL login form `dependabot`, not the REST/UI renderings
   `app/dependabot` or `dependabot[bot]`. PRs by any other author — including
   the repo owner and external contributors — are never touched.
2. **Human approval:** GraphQL `reviewDecision == "APPROVED"` on the PR,
   checked at snapshot time (`PrSnapshot.review_decision`) and re-verified
   immediately before the merge mutation (apply-time re-read, alongside the
   base-retarget check). Mirrors GitHub's own semantics — an approval
   surviving a later push is the platform behavior when "dismiss stale
   reviews" is off; that knob lives in the target repo's ruleset (VOY-1840),
   not in this loop. `reviewDecision` of `null` (missing), `REVIEW_REQUIRED`,
   and `CHANGES_REQUESTED` all fail closed to `not_approved` alike — a repo
   with NO required-review ruleset configured also returns `reviewDecision:
   null` and is therefore deliberately unmergeable by this loop until the
   repo's ruleset requires at least one approving review (VOY-1840 table).
   An approval revoked between snapshot and apply is caught by the
   apply-time re-read and skips with `approval_revoked_at_apply`, zero
   mutations. This condition retires the original zero-touch design below:
   the operator's end-state is now "approve once, and the loop completes
   the merge."
3. PR is open and not a draft.
4. CI: `statusCheckRollup` for the head commit is entirely `SUCCESS`
   (fail-closed on `null`/pending/missing).
5. Review threads: zero unresolved threads (paginated reviewThreads read,
   same as the resolve-loop's TRN-3044-style read).
6. Clearance readiness: the machine-written clearance readiness marker reports
   Stage 3 (Ready for approval) **for the current head SHA**. A stale marker
   from an older head does not count.
7. Not already merged/closed between enumeration and apply (re-check at apply;
   benign skip on race).

When the predicate passes, the loop executes **one mutation**: a rebase merge
as `iterwheel-countdown-bot` (`gh pr merge --rebase` / `mergePullRequest`).
GitHub rulesets re-validate at merge time (thread resolution, CodeQL, required
checks); a ruleset rejection is recorded as `merge_failed` in the audit and the
loop moves on — it never retries within a run and never escalates privileges.

### What this loop deliberately does NOT do

- No LLM gate — the deterministic predicate plus GitHub's merge-time ruleset
  enforcement are the safety layers; there is no human terminal gate to protect.
- No disarm/rollback logic — a merge is final; anything not yet merged is
  simply skipped next round if conditions regress.
- No approve-writing, no auto-merge arming, no thread resolution (that is the
  resolve-loop's mutation), no release actions (cd-release owns that).
- No touching non-agent PRs, ever.

### Deployment shape (mirrors VOY-1835)

- CLI: `vyg countdown merge-loop --repos <file> [--max-merges N] [--dry-run] [--json]`
- Allowlist: `~/.voyager/merge-loop.repos` (initial content: `frankyxhl/fx_bin`
  only) gated by the same ceiling mechanism as the resolve-loop
  (`resolve_allowed_repos()`-equivalent with `VOYAGER_MERGE_EXTRA_REPOS`).
- Env: `~/.voyager/merge-loop.env` with `MERGE_LOOP_ENABLED` (fail-closed:
  absent/false ⇒ loop sleeps), `MERGE_MAX_MERGES` (default 3).
- Daemon: launchd job `com.iterwheel.voyager.merge-loop` running an adaptive
  wrapper identical in structure to `countdown-resolve-loop-adaptive.sh`
  (self-scheduling, KeepAlive, fail-closed env re-source each iteration).
- Identity: fixed to `iterwheel-countdown-bot` via gh credential store; the
  token never enters config files.
- Audit: redacted JSONL, same format/location conventions as the resolve-loop.

### Target-repo GitHub configuration (one-time, operator-run as `frankyxhl`)

Required on `frankyxhl/fx_bin` before enabling live mode:

| Ruleset | Change | Why |
|---------|--------|-----|
| `main-pr-gates` | `required_approving_review_count` 1 → 0 | Removes the human-approve gate |
| `protect main` | `require_code_owner_review` true → false | Same — bot cannot satisfy code-owner review |
| `main-pr-gates` | **Add** `required_status_checks` for the CI workflows, **with** `strict_required_status_checks_policy: true` ("Require branches to be up to date before merging") | Merge-time CI enforcement must live in GitHub, not only in the loop's predicate; the strict/up-to-date flag is REQUIRED — `mergePullRequest` has no `expectedBaseOid`, so the loop's apply-time base re-read cannot fully close the base-advance race and this server-side gate is the only complete guarantee |
| `main-owner-merge-only` | Add `iterwheel-countdown-bot` to `bypass_actors` | The `update` rule otherwise blocks bot-initiated merges (canary-verify first; skip if the merge succeeds without it) |
| (keep) | `required_review_thread_resolution: true`, CodeQL gate | The remaining machine gates in zero-touch mode |

External-PR safety: after loosening, non-agent PRs still cannot self-merge —
merging requires write access, and the loop's author allowlist never selects
them. They stay open for manual handling.

### Rollout

1. Implement behind `--dry-run`; run against fx_bin; operator reviews the
   `would_merge` audit output.
2. Operator applies the ruleset changes above.
3. Enable live mode with `MERGE_MAX_MERGES=1`.
4. Canary: next naturally-occurring agent PR runs the full pipeline
   (review → clearance → resolve → merge → release). Verify the merge author,
   rebase linearity, and that cd-release fired exactly once.
5. Raise cap to 3; leave fx_bin as the only allowlisted repo until the operator
   explicitly extends the allowlist.

## Testing

- Unit: predicate truth table (each condition independently false ⇒ skip with
  the right audit reason; all true ⇒ merge attempted once), author-allowlist
  exclusion, stale-clearance-marker rejection, apply-time race (merged/closed
  between scan and apply ⇒ benign skip), cap enforcement, kill-switch off ⇒
  zero API mutations.
- Integration (sandbox): dry-run against `iterwheel/voyager-sandbox` fixtures
  before fx_bin, matching the resolve-loop's sandbox-first convention.

## Risks

- **Zero-touch merges to a published package.** Accepted by the operator by
  design; mitigations are the deterministic predicate, GitHub merge-time
  gates (threads, CodeQL, required checks), the author allowlist, the per-run
  cap, and the kill switch.
- **Clearance marker trust.** The predicate trusts the clearance bot's Stage 3
  marker; a clearance bug becomes a merge-loop bug. Mitigated by requiring the
  marker to be head-anchored and by the independent CI/thread conditions.
- **Ruleset drift.** If the operator later re-adds an approve requirement,
  merges fail closed (`merge_failed` audit entries, no retries) — safe, visible.

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-08-08 | Initial draft after operator design session (scope: fx_bin only; zero-touch agent-PR merge; rebase method) | Claude Code |
| 2026-08-08 | Mirror VOY-1840: `required_status_checks` row now mandates `strict_required_status_checks_policy: true` — the up-to-date gate is required, not optional (Codex round-11 review) | Claude Code |
| 2026-08-08 | Author allowlist bullet documents the `VOYAGER_MERGE_EXTRA_AUTHORS` operator-local extension (`merge_allowed_authors()`), enabling dependabot dependency-bump PRs on fx_bin | Claude Code |
| 2026-08-09 | Operator reversed zero-touch: target-repo rulesets now require an approving review. Added merge-predicate condition 2, "Human approval" (GraphQL `reviewDecision == "APPROVED"`, snapshot + apply-time re-verified), renumbered the remaining conditions ([#304](https://github.com/iterwheel/voyager/pull/304)) | Claude Code |
