# REF-1811: Multi-Agent Loop Configuration

**Applies to:** VOY project (`iterwheel/voyager`)
**Last updated:** 2026-05-18
**Last reviewed:** 2026-05-18
**Status:** Active
**Related:** COR-1617 (Multi-Agent Workflow Loop), COR-1618 (Out-of-Band Consent Auto-Pick), COR-1622 (Multi-Agent Loop Project Configuration), VOY-1805 (GitHub Bot Accounts), VOY-1807 (GitHub App Registry), VOY-1810 (Release Process)

---

## What Is It?

Voyager's project-layer instantiation of the COR-1622 parameter schema for the
COR-1617 Multi-Agent Workflow Loop. This REF supplies the concrete repository,
identity, review-panel, worker, bot, and runtime values that an orchestrator must
use when running the loop for `iterwheel/voyager`.

This document is forward-looking. Voyager has the Blueprint, Stack, and Clearance
automation stack installed, but the full COR-1617 loop has not been exercised in
this repo yet.

**ACID assignment note:** issue #32 requested `VOY-1810`, but `VOY-1810` is
already assigned to the Voyager Release Process SOP. This REF uses the next
available Foundation ACID, `VOY-1811`, to preserve one document per ACID.

---

## Why

Without a project-local COR-1622 instantiation, every session must re-derive
Voyager's loop values from `CLAUDE.md`, git remotes, bot docs, and recent PR
practice. A durable REF makes those substitutions explicit and gives future loop
runs one stable document to cite.

---

## Parameter Values

### Identity & Repository

| Key | Voyager value | Notes |
|-----|---------------|-------|
| `<repo>` | `iterwheel/voyager` | Current GitHub repository. |
| `<repo-owner>` | `iterwheel` | Owner segment of `<repo>`. |
| `<repo-trusted-reactor-list>` | `[frankyxhl, ryosaeba1985]` | Trusted human/operator identities for issue-body consent reactions. |
| `<gh-write-identity>` | `ryosaeba1985` | Required GitHub-visible write identity per WUK-2100 and `CLAUDE.md`; verify with `gh auth status`. |
| `<pr-push-remote>` | `fork` | Feature branches are pushed to `ryosaeba1985/voyager`; PRs target `iterwheel/voyager:main`. |

### Consent Gate (COR-1618)

| Key | Voyager value | Notes |
|-----|---------------|-------|
| `<consent-signal>` | `rocket` | Issue-body rocket reaction. |
| `<intake-quality-mode>` | `2FA` | Consent requires the reaction plus trusted intake-quality labeling. |
| `<intake-quality-label>` | `blueprint-ready` | Blueprint readiness label. |
| `<intake-quality-applier-set>` | `[iterwheel-blueprint[bot], frankyxhl, ryosaeba1985]` | Normal path is bot-applied; human identities are explicit manual overrides. |

### Review Panel (COR-1602 Binding)

| Key | Voyager value | Notes |
|-----|---------------|-------|
| `<panel-providers>` | `[glm, deepseek, minimax]` | Default three-provider quorum for plan-review and code-review. This exactly meets COR-1617's minimum three viable verdicts. |
| `<escalation-panel-providers>` | `[glm, deepseek, minimax, gemini, codex]` (extension) | Voyager-local extension, not a COR-1622 key. Use for high-risk artifacts, architecture docs, or explicit operator request. If `codex` is only available as the GitHub App review lane, record that limitation before treating the five-provider gate as satisfied. |
| `<weights-doc>` | `{CHG: COR-1609, ADR: COR-1609, RFC: COR-1608, inline-PR-body: COR-1609}` | Map form using valid COR-1622 `<spec-format>` keys. Code review uses COR-1610 by review phase and is not a map key. |
| `<spec-format>` | `CHG` | Voyager's default plan artifact is CHG-shaped; this REF itself is a PRJ REF. |
| `<panel-pass-threshold>` | `9.0` | All viable reviewers must meet the threshold with no blockers. |

### Worker Dispatch (COR-1619)

| Key | Voyager value | Notes |
|-----|---------------|-------|
| `<worker-agent>` | `trinity-glm via droid exec` | Default implementation worker for substantial changes. |
| `<worker-min-loc>` | `30` | Orchestrator may edit directly at or below 30 lines in one function; larger changes dispatch to the worker lane. |

### R-Count Cap (COR-1617 Phase 8)

| Key | Voyager value | Notes |
|-----|---------------|-------|
| `<max-r-count>` | `10` | Soft cap. |
| `<max-r-count-extension>` | `3` | Hard-stop evaluation begins at R13. |
| `<convergence-severity>` | `advisory` | Converged when no P0/P1/P2 findings remain. |

### Resilience

| Key | Voyager value | Notes |
|-----|---------------|-------|
| `<cli-retry-attempts>` | `3` | Retry each provider per round up to three times. |
| `<cli-retry-backoff-seconds>` | `600` | Ten-minute backoff between provider retry attempts. |
| `<cli-retry-on-failure>` | `pause-and-ask` | Stop and surface provider outage rather than silently reducing quorum. |

### Bot Polling (COR-1615 Binding)

| Key | Voyager value | Notes |
|-----|---------------|-------|
| `<bot-actors>` | `[chatgpt-codex-connector[bot], iterwheel-clearance[bot]]` | Codex provides GitHub-side review; Clearance provides the PR readiness panel and may edit/update its marker comment. |

### Loop Primitives (COR-1620)

| Key | Voyager value | Notes |
|-----|---------------|-------|
| `<wakeup-tool>` | Runtime-dependent; see Runtime Profile | Per COR-1622's `<wakeup-tool>` runtime escape-hatch language, `ScheduleWakeup` applies only to Claude Code-style runtimes. Other runtimes substitute their own wake or polling primitive. |
| `<idle-cap>` | `12` | Default. |
| `<merge-watch-cap>` | `24` | Default. |

---

## Runtime Profile

Voyager treats runtime as an explicit local convention because COR-1622 has no
`<runtime>` key. Operators must select the runtime row before invoking the loop.

| Runtime | Status for Voyager | Invocation | Panel dispatch | Wakeup primitive |
|---------|--------------------|------------|----------------|------------------|
| Claude Code | Primary documented runtime today | `follow VOY-1811...` in the Claude Code session | Trinity skill / `trinity review` with `<panel-providers>` | `ScheduleWakeup` when available; otherwise the runtime's documented loop primitive |
| DeepSeek TUI | Verified (2026-05-18) | `follow VOY-1811` in the DeepSeek TUI session | Trinity skill / `trinity review` with `<panel-providers>`; sub-agent dispatch via `agent_open` | `task_shell_start` with shell poll loop (`while sleep N; do gh pr view/gh api ...; done`) in bounded-poll mode; no autonomous `ScheduleWakeup` |
| Codex CLI | Supported operator runtime | Codex session prompt using the same invocation phrases | Local shell `trinity review` and `gh` commands | No durable native wakeup assumed; use bounded `sleep`/poll in-session or an external scheduler |
| Droid | Worker/runtime alternative | `droid exec` or `droid exec --mission` with explicit model and cwd | Provider CLIs through Droid or Trinity provider wrappers | External scheduler or Droid/factory session re-entry; do not assume `ScheduleWakeup` |
| Gemini CLI | Reviewer/runtime alternative | `gemini -p` review prompts or project wrapper | Direct Gemini CLI for escalation review | External scheduler/manual re-entry |
| GitHub Actions | CI/runtime alternative | `workflow_dispatch` or scheduled workflow | Workflow steps invoking provider CLIs with repository secrets | Scheduled workflow, job delay, or workflow re-dispatch |

Runtime substitution must preserve COR-1620's stop-marker and branch-guard
semantics. If a runtime cannot preserve those semantics, the operator must keep
the loop in manual/bounded-poll mode rather than claiming full COR-1617 adoption.

---

## Invocation

Voyager-specific shorthand for starting the COR-1617 loop with this REF's
parameters. The three variants are mutually exclusive.

Only `follow VOY-1811 for #N` qualifies for COR-1618's Normative Bypass Clause
because it names a target issue in live chat. The other variants name only this
configuration document, so the agent-selected issue must still pass the full
consent and intake-quality gate.

| Phrase the operator types | Behavior |
|---------------------------|----------|
| `follow VOY-1811` | Start looping mode. Every issue pick, including the first, must pass COR-1618 consent and `blueprint-ready` intake quality before COR-1617 scope ranking. After a mergeable handoff and retrospective, Phase 12 may restart the loop if the selected runtime supports safe wakeups. |
| `follow VOY-1811 once` | Same gated pick rules as `follow VOY-1811`, but stop after Phase 11 for the selected issue. No Phase 12 autonomous restart. |
| `follow VOY-1811 for #N` | User-directed pick of issue `#N`; bypasses COR-1618 consent per the Normative Bypass Clause and runs phases 2-11 on the named issue. Stop after Phase 11; no Phase 12 autonomous restart. |

`follow VOY-1810` is not an alias for this workflow. `VOY-1810` is the release
process SOP.

---

## Adoption Status by Phase

All phases are aspirational for Voyager as of 2026-05-17. Individual practices
exist in ordinary PR work, but the end-to-end COR-1617 loop has not yet been run
under this REF.

| Phase | Status | Rationale |
|-------|--------|-----------|
| 1 — Auto-pick | ❌ aspirational | Issues are selected by live operator instruction today, not autonomous consent-gated queue drain. |
| 2 — Branch & identity | ❌ aspirational | COR-1505-style hygiene is practiced manually, but not as a VOY-1811 loop phase. |
| 3 — Plan | ❌ aspirational | Voyager issues may carry plans, but no loop-generated CHG sizing phase is active. |
| 4 — Plan-review | ❌ aspirational | Trinity reviews are used ad hoc; not yet a required pre-implementation loop gate. |
| 5 — Dispatch | ❌ aspirational | GLM/Droid worker dispatch is available but not yet the default decision-tree path. |
| 6 — Verify implementation | ❌ aspirational | Local validation exists per PR, but not as an automated COR-1617 phase. |
| 7 — PR open | ❌ aspirational | PRs are opened manually with `gh`, not by an autonomous loop. |
| 8 — Iterate | ❌ aspirational | CI, Codex, and Clearance loops run on PRs, but no VOY-1811-controlled R-loop exists. |
| 9 — Triage | ❌ aspirational | Findings are handled per PR; no durable COR-1621 round triage ledger is active. |
| 10 — Handoff + merge-watch | ❌ aspirational | The repo owner merges manually; no merge-watch wake is armed. |
| 11 — Retrospective | ❌ aspirational | Retrospectives are not generated automatically after merge. |
| 12 — Loop restart | ❌ aspirational | No autonomous restart is configured after handoff. |

---

## Completion Gate (COR-1617 Phase 11 Binding)

**Status:** Active (2026-05-18)
**Motivating incident:** PR [#49](https://github.com/iterwheel/voyager/pull/49)
(merged) with unresolved Codex P2 review thread; follow-up
[PR #55](https://github.com/iterwheel/voyager/pull/55) required to close the
finding.

### Why a Completion Gate?

VOY-1811 Phase 11 (Retrospective) must not report completion while any related
PR carries unresolved actionable review feedback. The 2026-05-17 open-issue batch
run closed target issues and merged the integration PR, but missed a Codex P2
thread on already-merged PR #49. That thread was actionable and required
follow-up PR #55 before it could be resolved.

Without a hard gate, an agent can declare the task done while review feedback
survives on merged or superseded PRs that are no longer in the agent's active
working set.

### Related PR Set

Before declaring Phase 11 complete, the agent MUST assemble the **Related PR
Set** for the current task. The set includes every PR that is linked to the
target issue(s) or was created during the VOY-1811 run:

| Source | Scope | Rationale |
|--------|-------|-----------|
| Current PR(s) | Open PRs created by this run | Primary work artifact |
| Integration PR | The PR that closes the target issue(s) | If distinct from current PR |
| Superseded PR(s) | PRs closed/superseded by the current work | May carry unresolved threads that were never addressed |
| Merged PR(s) | PRs merged during this run or linked to the same issue(s) | Review threads survive merge; GitHub does not auto-resolve them |
| Referenced PR(s) | PRs cited in PR bodies, comments, or closing references of any PR in the set | Transitive sweep to catch collateral unresolved feedback |

The Related PR Set is **transitive**: if a PR in the set references another PR,
the referenced PR joins the set. The agent must expand the set until no new
cross-references are discovered.

### Review-Thread Sweep

For every PR in the Related PR Set, regardless of whether the PR is open,
merged, or closed, the agent MUST:

1. Fetch all review threads (`gh api /repos/{owner}/{repo}/pulls/{number}/comments` or equivalent).
2. For each thread, determine its resolution state.
3. Classify every unresolved thread as actionable or non-actionable (see below).
4. For actionable threads: fix + resolve, or create a linked follow-up issue/PR.
5. For non-actionable threads: document the rationale for non-action.

The sweep applies to **all** PRs in the set — merged PRs are not excluded.
GitHub preserves review threads after merge; a merged PR with unresolved
threads is still carrying actionable feedback.

### Actionable Classification

An unresolved review thread is **actionable** when:

- Severity P0/P1/P2 (blocker, major, minor per COR-1609/COR-1610), OR
- The reviewer explicitly requested a change that was not applied, OR
- The thread asks a question that was never answered.

A thread is **non-actionable** only when:

- The feedback was applied in a different commit/PR (cite the commit or PR), OR
- The thread is purely conversational/emojis with no change request, OR
- The thread was superseded by a later review round that explicitly reversed
  the request.

If classification is ambiguous, the thread is actionable by default.

### Delayed-Review Sweep

After final push, approval, or merge of the current PR(s), the agent MUST
perform a **delayed-review sweep** before reporting completion:

1. Wait for bot polling windows to complete (Clearance re-evaluates, Codex
   may post delayed follow-ups).
2. Re-fetch review threads for all PRs in the Related PR Set.
3. If any new unresolved actionable threads appear, restart from the
   resolution step — do not report completion.

The delayed sweep catches review feedback that arrives after the agent's
last push — for example, a Codex review that was triggered by the final
commit and completed after the agent moved on.

### Completion Criteria

Phase 11 completion requires **both** conditions to be true:

| Condition | Check |
|-----------|-------|
| **Target issue closure** | All target issues are closed, OR a linked PR with closing keywords is merged. |
| **Review-thread closure** | For every PR in the Related PR Set, zero unresolved actionable threads remain. Non-actionable threads are documented with rationale. |

The agent MUST NOT report completion when target issues are closed but
actionable review threads exist on any PR in the Related PR Set.
Issue closure and review-thread closure are distinct gates; both must pass.

### Concrete Checks

The agent MUST perform these checks (or equivalents for non-GitHub-API
runtimes) before reporting Phase 11 complete:

```bash
# 1. Assemble the Related PR Set from issue cross-references (GraphQL)
gh api graphql -F owner="iterwheel" -F repo="voyager" -F issue=<issue_number> \
  -f query='
    query($owner:String!, $repo:String!, $issue:Int!, $endCursor:String) {
      repository(owner:$owner, name:$repo) {
        issue(number:$issue) {
          timelineItems(first:50, after: $endCursor, itemTypes:[CROSS_REFERENCED_EVENT, CLOSED_EVENT]) {
            pageInfo { hasNextPage endCursor }
            nodes {
              ... on CrossReferencedEvent {
                source { ... on PullRequest { number title state url } }
              }
              ... on ClosedEvent {
                closer { ... on PullRequest { number title state url } }
              }
            }
          }
        }
      }
    }'

# 2. For each PR in the set: fetch review threads (REST for discovery)
gh api "/repos/iterwheel/voyager/pulls/{pr_number}/comments" \
  --jq '.[] | select(.in_reply_to_id == null) | {id, path, body, created_at, html_url}'

# 3. For each thread: check resolved state (GraphQL isResolved)
gh api graphql -F owner="iterwheel" -F repo="voyager" -F pr=<pr_number> \
  -f query='
    query($owner:String!, $repo:String!, $pr:Int!, $endCursor:String) {
      repository(owner:$owner, name:$repo) {
        pullRequest(number:$pr) {
          reviewThreads(first:100, after: $endCursor) {
            pageInfo { hasNextPage endCursor }
            nodes {
              isResolved
              path
              comments(first:100) {
                pageInfo { hasNextPage endCursor }
                nodes { body }
              }
            }
          }
        }
      }
    }' --jq '.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved == false)'

# 4. Cross-reference: check PR bodies/comments for mentions of other PRs
gh pr view {pr_number} --json body,comments
```

> **Note:** The REST API's `in_reply_to_id` field signals reply linkage, not
> resolution state. A thread with replies may still be unresolved, and a thread
> resolved via the GitHub UI (Resolve button) may have zero replies. Use check
> #3 (GraphQL `isResolved`) as the authoritative resolution gate.
>
> **Pagination:** GitHub GraphQL caps `first`/`last` at 100. Both check #1
> (`timelineItems`, capped at 50 here) and check #3 (`reviewThreads`, capped
> at 100) return paged connections. For any issue with more cross-references
> or any PR with more review threads than the page size, the agent MUST
> paginate through `pageInfo.hasNextPage` and `endCursor` to ensure every
> entry is inspected before declaring completion. Voyager's
> `voyager/core/github_app.py` already implements cursor-based pagination for
> `reviewThreads`; the checks above include `pageInfo { hasNextPage endCursor }`
> so agents can extend with `gh api --paginate` (which requires these fields)
> when the total count is unknown. On the first page, omit `endCursor` (no `-F endCursor` flag). On subsequent
> pages, pass `$endCursor` via `-F endCursor="$cursor"` and loop until
> `hasNextPage` is false. The `comments` connection inside `reviewThreads` (check #3)
> also requires pagination when a thread has more than 100 replies; follow the same
> `pageInfo`/`endCursor` pattern per thread.

If a runtime cannot execute these checks (e.g., no GitHub CLI access), the
agent MUST explicitly record the limitation and report it as an open
completion-gate blocker rather than proceeding.

---

## Known Limitations

1. **No COR-1622 `<runtime>` key.** Voyager records runtime substitution in
   `§Runtime Profile`. If non-Claude-Code orchestration becomes routine, file an
   upstream CHG against COR-1622 to formalize a `<runtime>` enum or map.
2. **ACID correction from issue #32.** The issue requested `VOY-1810`, but that
   ACID is already assigned. Operators should use `VOY-1811` for this loop
   configuration and keep `VOY-1810` reserved for releases.
3. **Codex escalation surface.** `codex` may be available as a GitHub App review
   lane rather than a Trinity provider CLI. Escalated five-provider reviews must
   record the actual mechanism used for Codex before counting it as a viable
   verdict.

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-05-18 | Added DeepSeek TUI row to Runtime Profile (verified via issue #56 loop). Wakeup uses `task_shell_start` with shell poll loop in bounded-poll mode. | DeepSeek (via VOY-1811) |
| 2026-05-18 | Added Completion Gate (COR-1617 Phase 11 Binding): Related PR Set, review-thread sweep, actionable classification, delayed-review sweep, completion criteria with distinct issue/thread closure, and concrete `gh` checks. Motivated by PR #49 P2 thread missed during VOY-1811 open-issue batch. | DeepSeek (via VOY-1811) |
| 2026-05-17 | Initial Voyager instantiation of COR-1622 for the COR-1617 multi-agent workflow loop. Uses VOY-1811 because VOY-1810 is already the Release Process SOP. | Codex |
