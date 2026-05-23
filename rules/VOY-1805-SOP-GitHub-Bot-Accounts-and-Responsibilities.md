# SOP-1805: GitHub Bot Accounts and Responsibilities

**Applies to:** VOY project
**Last updated:** 2026-05-09
**Last reviewed:** 2026-05-09
**Status:** Active
**Related:** VOY-1802, VOY-1804

---

## What Is It?

This SOP records the first public GitHub bot account roster for Iterwheel's
Voyager automation pipeline: account handles, display names, stage
responsibilities, and permission boundaries.


## Why

GitHub bot accounts are publicly visible through organization membership, issue
comments, pull request reviews, status checks, and audit trails. Their names
must therefore be readable as public product surface, not just internal utility
labels.

The account handles use an organization-owned `iterwheel-` prefix for GitHub
ergonomics while keeping the canonical aerospace display names from VOY-1802
and VOY-1804.

---

## When to Use

- Creating or inviting Iterwheel-owned GitHub bot accounts.
- Assigning bot accounts to GitHub teams or repositories.
- Explaining which bot should comment on an issue, review a pull request, or
  publish a gate verdict.
- Reviewing whether a proposed new bot overlaps with an existing stage.


## When NOT to Use

- Naming non-GitHub services, internal processes, or local-only agents that do
  not appear publicly on GitHub.
- Granting production deploy or repository administration authority. Those
  permissions require a separate ADR and explicit approval.
- Renaming the canonical aerospace stage names from VOY-1802. Use a new ADR for
  any naming-system change.


## Steps

1. **Use the canonical first-batch roster**

   | GitHub handle | Display name | Primary responsibility |
   |---------------|--------------|------------------------|
   | `iterwheel-blueprint` | Blueprint | Issue intake: validate issue title format, issue templates, completeness, Blueprint labels, priority hints, missing context, and ready-state rocket reactions. |
   | `iterwheel-stack` | Stack | Issue classification: infer and maintain type, area, size, risk, and routing labels from issue title and body. Stack is classification-only; it does not write code. |
   | `iterwheel-assembly` | Assembly | Code implementation: create branches, edit code, run tests, push commits, open/update pull requests, and request review. Assembly must not merge, approve its own work, resolve review threads as a reviewer, or substitute for Clearance or Countdown. |
   | `iterwheel-staticfire` | Static Fire | CI and test aggregation: read checks, lint, typecheck, test, and workflow results; summarize failures in human-readable form. Static Fire observes test results; it does not modify code or approve changes. |
   | `iterwheel-clearance` | Clearance | Review readiness: aggregate approvals, requested changes, unresolved review threads, and bot verdicts. Clearance polls; it does not write code or evaluate code correctness. |
   | `iterwheel-countdown` | Countdown | Final merge gate: publish a GO or HOLD verdict after checking PR title/body conventions, CI, review state, branch protection, conflicts, and release constraints. |

2. **Use the Blueprint label standard**

   Blueprint owns exactly three issue-state labels. Keep these names stable
   across every repository where `iterwheel-blueprint` is installed:

   | Label | Meaning |
   |-------|---------|
   | `blueprint-needed` | The issue has not yet entered or completed an initial Blueprint pass. This is an entry/backlog marker, not a failed-check state. |
   | `blueprint-ready` | The issue has passed Blueprint issue title and intake checks and can move into agent work. |
   | `blueprint-requests-revision` | Blueprint is asking the author to revise the issue before work starts. This is the human-response filter for failed Blueprint checks. |

   These labels are mutually exclusive as state labels. An issue should have at
   most one of them at a time.

   When an issue is ready, Blueprint keeps `blueprint-ready` and removes
   `blueprint-needed` and `blueprint-requests-revision`. When an issue fails a
   Blueprint check, Blueprint keeps `blueprint-requests-revision` and removes
   `blueprint-needed` and `blueprint-ready`.

   Do not revive the older `needs-blueprint` label name.

3. **Use the Stack label standard**

   Stack owns classification labels only. It must not use its labels as
   pass/fail gates, and it must not create labels outside this allow-list.

   Each classified issue should have exactly one label from each axis:

   | Axis | Labels |
   |------|--------|
   | Type | `stack-type-task`, `stack-type-bug`, `stack-type-feature`, `stack-type-docs`, `stack-type-refactor`, `stack-type-chore`, `stack-type-ci`, `stack-type-test`, `stack-type-spike` |
   | Area | `stack-area-github`, `stack-area-automation`, `stack-area-docs`, `stack-area-ci`, `stack-area-tests`, `stack-area-frontend`, `stack-area-backend`, `stack-area-infra`, `stack-area-unknown` |
   | Size | `stack-size-xs`, `stack-size-s`, `stack-size-m`, `stack-size-l`, `stack-size-xl` |
   | Risk | `stack-risk-low`, `stack-risk-medium`, `stack-risk-high` |
   | Review | `stack-needs-review` |

   Stack v2 classifies GitHub issues from issue title and body. It first trusts
   explicit body fields such as `Stack Type`, `Work Type`, and `Stack Area`,
   then falls back to weighted keyword signals. Generic words such as `issue`,
   `PR`, `label`, and `test` must not dominate a long issue body by themselves.
   It must ignore pull requests, including `/stack` comments on pull request
   conversations. PR title/body convention checks belong to Countdown. When
   Stack has enough confidence, it should apply one label per classification
   axis, remove `stack-needs-review`, upsert a Stack classification comment,
   remove its own `eyes` reaction, and add a `rocket` reaction.

   When Stack cannot classify confidently, it should apply only
   `stack-needs-review`, remove existing `stack-type-*`, `stack-area-*`,
   `stack-size-*`, and `stack-risk-*` labels, upsert a Stack comment with the
   review reasons and suggested labels, remove its own `rocket` reaction, and
   add an `eyes` reaction. This is still a request for human classification, not
   a pass/fail gate.
    Stack must ignore Assembly-authored PRs and code changes: its responsibility
    ends at classification.

4. **Use the Clearance label standard**

   Clearance owns pull request review-readiness labels only. Its labels are
   mutually exclusive and should be applied only to pull requests:

   | Label | Color | Description |
   |-------|-------|-------------|
   | `clearance-1-pending` | `#FBCA04` (yellow) | Waiting for data, checks, webhook results, or bot review signal. |
   | `clearance-2-blocked` | `#D93F0B` (red) | Explicit blocker: unresolved review threads, changes requested, or failing required checks. |
   | `clearance-3-ready-for-approval` | `#5319E7` (purple) | Automated conditions satisfied; configured human approval still missing. |
   | `clearance-4-ready-for-merge` | `#0E8A16` (green) | Configured human / current-head approval present and automated conditions satisfied. |

   ### Legacy labels (migration)

   The following three labels were used before issue #25 and are removed on
   every Clearance writeback. They must not be applied by new code.

   | Label | Status |
   |-------|--------|
   | `clearance-pending` | Replaced by `clearance-1-pending` |
   | `clearance-blocked` | Replaced by `clearance-2-blocked` |
   | `clearance-ready` | Replaced by `clearance-4-ready-for-merge` |

   Clearance v1 is deterministic. It verifies GitHub review state and review
   thread resolution, upserts a Clearance comment, adds `+1` when
   `clearance-4-ready-for-merge`, and adds `eyes` otherwise. It does not prove
   that every requested semantic code change was truly fixed; AI-assisted
   semantic repair verification is a later Clearance v2 responsibility.
    Clearance must not mark itself as a reviewer on Assembly-authored PRs;
    it aggregates, it does not evaluate code correctness.

5. **Assembly boundaries**

   `iterwheel-assembly` is the implementation bot. Its scope is:

   | Allow | Deny |
   |-------|------|
   | Create feature branches from issue body | Merge pull requests |
   | Write and edit code | Approve its own pull requests |
   | Run tests and lint locally | Resolve review threads as a reviewer |
   | Push commits to a fork or feature branch | Apply `clearance-4-ready-for-merge` or `countdown-go` labels |
   | Open pull requests with closing keywords | Modify branch protection rules |
   | Request review from humans or Clearance | Close issues directly without a linked PR |
   | Comment on its own PR with implementation notes | Override Static Fire, Clearance, or Countdown verdicts |

   Assembly writes code; it does not gate, approve, or merge. Its trigger model
   starts with a manual slash command such as `/assembly` or `/implement` on a
   `blueprint-ready` issue. Rollout is allow-list first: install Assembly only on
   selected sandbox repositories before expanding.

   Assembly operates after Stack classification and before Static Fire testing
   in the rocket factory pipeline.

6. **Keep handle rules stable**

   - Use `iterwheel-` as the GitHub account prefix.
   - Use lowercase ASCII handles.
   - Prefer exactly one hyphen after `iterwheel`.
   - Do not add extra internal hyphens unless readability requires it.
   - Preserve canonical display names with normal spacing, such as `Static Fire`.

7. **Treat `iterwheel-staticfire` as the handle exception**

   The canonical display name remains `Static Fire`, but the GitHub handle is
   `iterwheel-staticfire` rather than `iterwheel-static-fire` to keep the public
   handle shorter and visually cleaner.

8. **Limit initial authority**

   The first-batch accounts may read repository state, post comments, publish
   check/status conclusions, and participate in review workflows. They must not
   receive broad organization administration, repository administration,
   billing, secret-management, or direct production-deploy authority by default.

   Stack classification may use an LLM when deterministic rules are
   insufficient, but it must write only approved labels from a repo allow-list.
   It should not invent labels or turn classification into a pass/fail gate.

9. **Treat Countdown as advisory until hardened**

   `iterwheel-countdown` is the desired final merge gate, but its first
   operating mode is advisory: it may publish `GO` or `HOLD` conclusions, while
    actual merge authority remains with humans, GitHub branch protection, or a
    later approved automation design.
    Countdown must not merge Assembly-authored PRs until it has satisfied its
    own gate conditions and received a matching human approval. Assembly
    implementation and Countdown gate authority remain separate stages; no bot
    may hold both implementation and final gate keys simultaneously per VOY-1806
    least-privilege matrix.


## Examples

### Incomplete Blueprint issue

An issue with a missing acceptance criteria section should keep
`blueprint-requests-revision`. It should not keep `blueprint-needed` or
`blueprint-ready`, and it should not receive the Blueprint ready-state rocket
reaction.

### Ready Blueprint issue

An issue with a valid Blueprint title and complete intake fields should keep
`blueprint-ready`. Blueprint should remove `blueprint-needed` and
`blueprint-requests-revision`, upsert its intake comment, and add the
ready-state rocket reaction.

### Classified Stack issue

An issue titled `[Feature]: Add GitHub webhook label classifier` should receive
one label per Stack axis, for example `stack-type-feature`,
`stack-area-github`, `stack-size-s`, and `stack-risk-medium`. A later Stack pass
may replace labels inside the same axis, but should not add a second type, area,
size, or risk label. Stack should also upsert a classification comment and add a
`rocket` reaction.

### Ambiguous Stack issue

An issue with a title like `Thing` and a placeholder body like `todo` should get
only `stack-needs-review`. Stack should remove stale classification-axis labels,
upsert a comment explaining why it needs review, and avoid adding a `rocket`
reaction.

### Assembly implementation PR

On a `blueprint-ready` issue, `/assembly` or `/implement` triggers Assembly to
create a branch, write code per the issue plan, run local tests, push commits
to the fork, and open a pull request against `main`. Assembly adds
`Closes #N` to the PR body. It does not approve, merge, close the issue
directly, or apply Clearance labels. Clearance and Countdown remain the
review/gate stages for the Assembly-authored PR.

---

## Change History

| Date       | Change                                                                                                                       | By               |
|------------|------------------------------------------------------------------------------------------------------------------------------|------------------|
| 2026-05-09 | Initial version - recorded first-batch public GitHub bot handles, display names, responsibilities, and permission boundaries | Frank Xu + Codex |
| 2026-05-09 | Replaced short `iw-` handles with organization-owned `iterwheel-` handles after GitHub App name collision                    | Frank Xu + Codex |
| 2026-05-09 | Clarified Blueprint as issue intake/title validation, Stack as classification, and Countdown as PR gate                      | Frank Xu + Codex |
| 2026-05-09 | Standardized Blueprint issue labels as `blueprint-needed`, `blueprint-ready`, and `blueprint-requests-revision`              | Frank Xu + Codex |
| 2026-05-09 | Tightened Blueprint labels to be mutually exclusive state labels                                                             | Frank Xu + Codex |
| 2026-05-09 | Added Stack v1 classification label axes and allow-list                                                                      | Frank Xu + Codex |
| 2026-05-09 | Added Stack low-confidence review flow, status comment, and success `rocket` reaction                                        | Frank Xu + Codex |
| 2026-05-09 | Tightened Stack scope to issue-only classification; pull requests are handled by Countdown                                   | Frank Xu + Codex |
| 2026-05-09 | Added Stack v2 explicit `Work Type` / `Stack Area` parsing and weighted area scoring                                         | Frank Xu + Codex |
| 2026-05-09 | Added Clearance v1 pull request review-readiness label standard                                                             | Frank Xu + Codex |
| 2026-05-16 | Replace 3 unnumbered labels with 4 numbered labels + colors per issue #25; legacy names migrated by writeback               | Claude Code      |
| 2026-05-23 | Added Assembly bot: responsibilities, boundaries, allow/deny table, trigger model, rollout model, pipeline position, and examples (issue #67) | DeepSeek (via VOY-1811) |
