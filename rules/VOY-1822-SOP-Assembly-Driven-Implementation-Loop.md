# SOP-1822: Assembly-Driven Implementation Loop

**Applies to:** Voyager Assembly bot and managed repositories
**Last updated:** 2026-05-24
**Last reviewed:** 2026-05-24
**Status:** Active
**Date:** 2026-05-24
**Requested by:** Frank Xu (via issue #94)
**Priority:** P2
**Related:** VOY-1811, VOY-1817, VOY-1818, VOY-1821, #92, #93, #98, #99

---

## What Is It?

This SOP is the Assembly-specific implementation loop derived from
VOY-1811. It does not replace VOY-1811. VOY-1811 remains the broad
multi-agent loop configuration for Voyager; this document specializes the
implementation path for work that is delegated to `iterwheel-assembly`.

Assembly now has a concrete runtime shape:

1. A GitHub issue passes Blueprint and Stack gates.
2. An authorized actor triggers `/assembly` or `/implement`.
3. The bridge builds an Assembly job contract.
4. The configured backend implements in an isolated checkout.
5. Assembly pushes a branch, opens or updates a PR, and triggers Codex.
6. CI, Codex, and Clearance converge.
7. A human reviewer approves and merges.

## Why

The goal is to make that loop direct, repeatable, and discoverable by future
agents and operators without requiring them to infer Assembly behavior from
the generic VOY-1811 phase table.

---

## When to Use

Use Assembly when all of the following are true:

- The task is represented by a GitHub issue with concrete acceptance criteria.
- The repository is explicitly allow-listed for Assembly.
- The requested change can be implemented as a normal branch and PR.
- The repo has known verification commands that Assembly can run before
  pushing.
- The expected implementation does not require operator-only credentials,
  UI-only actions, private manual judgment, or branch-protection changes.
- The issue is ready for an autonomous implementation attempt, not merely a
  planning discussion.

---

## When NOT to Use

Use direct human/Codex implementation instead when any of the following is true:

- The issue is still ambiguous or lacks a testable outcome.
- The change is primarily policy design, architecture negotiation, or product
  judgment.
- The implementation requires editing secrets, granting permissions, changing
  GitHub App installation scope, or modifying branch protection.
- The repository is not yet allow-listed or does not have safe verification
  commands.
- A failed Assembly run needs manual debugging before retry.
- The task is small enough that direct implementation is lower risk than
  invoking a real backend.

Assembly is an implementation worker. It never approves, merges, resolves
review threads, overrides Clearance, or closes issues except through a linked
merged PR.

---

## Preconditions

Before triggering Assembly, verify every precondition in this section.

| Gate | Required state | Operator check |
|------|----------------|----------------|
| Issue shape | Target is an issue, not a PR | `gh issue view <N> --repo <owner/repo>` |
| Blueprint | Issue has `blueprint-ready` | Labels or Blueprint comment |
| Stack | Issue has at least one `stack-type-*` label, unless using an explicit safe override | Labels or Stack comment |
| Actor | Triggering actor is authorized | `BRIDGE_ASSEMBLY_AUTHORIZED_ACTORS` or trusted association policy |
| Repository | Repo is installed for `iterwheel-assembly` and bridge allow-listed | VOY-1807 plus `BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_ASSEMBLY` |
| Bridge | Bridge is healthy and `dry_run` state is intentional | `/healthz` local or public endpoint |
| PR Source | Managed PRs must satisfy headRepository == baseRepository. Fork PRs are forbidden for managed Assembly/Codex implementation loops unless a human explicitly grants an exception. | `gh pr view <N> --repo <owner/repo> --json headRepository,baseRepository` |
| Backend | `ASSEMBLY_EXECUTION_BACKEND` is intentional | Normally `dry-run`, `fake-subprocess`, or `pi-oh-my-pi-deepseek` |
| Verification | Repo-specific verification commands are configured when defaults do not apply | `ASSEMBLY_VERIFICATION_COMMANDS_<encoded-repo>` or global override |
| Credentials | Model/API credentials are available only to the backend process, not to prompts/comments | Local env/config audit |
| Privacy | Operators know where private local traces live and what may appear on GitHub | See Privacy Boundary |

For non-Voyager repositories, do not rely on Voyager default verification
commands. Configure repository-specific commands before a real backend run.

---

## Steps

### 1. Select The Issue

Confirm the issue is `blueprint-ready`, Stack-classified, and scoped for a
single PR. If the issue body is stale, ask the requester to update the issue
instead of encoding hidden requirements in the `/assembly` trigger comment.

For operator-directed work, naming the issue in the live conversation is
sufficient consent. For queue-driven work, keep using the VOY-1811/COR-1618
consent gate.

### 2. Preflight The Runtime

Record the intended runtime state before triggering:

```bash
curl -fsS http://127.0.0.1:8787/healthz
rg -n '^(DRY_RUN|BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_ASSEMBLY|ASSEMBLY_EXECUTION_BACKEND|ASSEMBLY_PI_|ASSEMBLY_VERIFICATION_COMMANDS)' ~/.voyager/bridge.env
```

Do not print secrets. If the env file contains credential values, inspect only
variable names and non-secret configuration.

For real OMP runs, also verify:

```bash
command -v omp
omp --version
```

If the repository uses custom commands, confirm the encoded repository key
matches Voyager's current encoding rules. For example, `frankyxhl/trinity`
uses `ASSEMBLY_VERIFICATION_COMMANDS_FRANKYXHL__TRINITY`.

### 3. Trigger Assembly

Post one issue comment:

```text
/assembly
```

Use `/implement` only as an alias when the operator explicitly prefers it.
Use `--dry-run` for contract inspection without writes. Use
`--allow-missing-stack` only when the missing Stack label is understood and the
operator accepts the weaker gate.

### 4. Monitor The Issue Progress Comment

For normal non-dry-run runs, Assembly upserts one issue progress comment. The
first pass should show one of these states:

| State | Meaning | Next action |
|-------|---------|-------------|
| `refused` | Preconditions failed | Fix the issue/labels/allow-list/actor state, then retry |
| `no_changes` | Backend completed without repo changes | Decide whether the issue is already satisfied or needs clearer AC |
| `failed` | Backend or writeback failed | Follow the failure path below |
| `applied` | Branch and PR work succeeded | Move to PR verification |

Dry-run contract inspection is a no-write observability path. When bridge
`DRY_RUN` is enabled or the trigger uses `/assembly --dry-run`, do not wait for
a new issue or PR progress comment. Confirm that the trigger was accepted,
inspect bridge logs or the dry-run contract output available to the operator,
and switch to a non-dry-run runtime only after the contract is correct.

Do not trigger duplicate `/assembly` comments while a real backend process is
running. If a duplicate run is needed, wait until the prior progress comment is
terminal.

### 5. Verify The PR

When Assembly opens or updates a PR:

1. Confirm the PR body includes `Closes #<issue>`.
2. Confirm the changed files match the issue scope.
3. Confirm Assembly posted `@codex review`.
4. Confirm CI starts, unless the PR is intentionally docs-only and the repo
   skips docs paths.
5. Run the repo's verification commands locally or in a clean clone when the
   blast radius warrants it.
6. Confirm `headRepository` matches `baseRepository` on the PR. Fork PRs are
   forbidden for managed Assembly loops (see §5.1 for why). Verify with:
   ```bash
   gh pr view <N> --repo <owner/repo> --json headRepository,isCrossRepository | jq -e '(.isCrossRepository | not) and (.headRepository.nameWithOwner == "<owner>/<repo>")' > /dev/null
   ```
   When the check passes (`isCrossRepository` is `false` and
   `headRepository.nameWithOwner` matches the expected owner/repo) the exit
   code is 0.  A non-zero exit indicates a fork PR.

Assembly's own validation is evidence, not a substitute for operator
acceptance. The operator is still responsible for checking semantic fit before
approval.
### 5.1. Fork PR Caveats

Fork PRs are forbidden for managed Assembly/Codex implementation loops unless
a human explicitly grants an exception. The code-level gate in
`_ensure_pull_request` refuses to open or update a PR whose head repository
differs from its base repository.

If a fork PR exists for an Assembly-managed branch for any reason (e.g. it was
created before this SOP was in effect, or before the code gate was deployed),
the following consequences apply:

1. **Clearance auto-resolve is blocked.** The `iterwheel-clearance` GitHub App
   is installed on the target repository (`iterwheel/voyager`, `frankyxhl/trinity`,
   etc.) but is NOT installed on the fork head repository. Clearance cannot
   auto-resolve review threads on a fork PR because the App cannot access the
   fork's head ref.

2. **Manual thread resolution required.** Every review thread on a fork PR must be
   manually resolved by a human with write access to the fork. Alternatively,
   install the `iterwheel-clearance` App on the fork repository and re-trigger
   Clearance.

3. **Close and replace.** When a fork PR is identified during the managed loop,
   close the fork PR and open a same-repo replacement. Push the feature branch
   to the target repository remote, not a personal fork remote:
   ```bash
   git push <target-remote> <branch-name>
   ```
   Where `<target-remote>` points to `https://github.com/<owner>/<repo>.git` and
   the owner is the target repository owner (e.g. `iterwheel` or `frankyxhl`),
   not a personal fork owner.


### 6. Iterate On Findings

Use the VOY-1811 Phase 8 pattern on the PR:

1. Wait for CI, Codex, and Clearance.
2. If Codex posts actionable P0/P1/P2 findings, fix them in the PR branch or
   rerun Assembly only when the issue contract remains valid.
3. After every push, post a fresh `@codex review` comment.
4. Continue until CI is green and no actionable review threads remain.
5. Run `/clearance` if a relevant event did not refresh Clearance.

Assembly may open/update the branch, but it is not the reviewer. Do not let
Assembly approve, merge, resolve review threads, or apply readiness labels.

Codex review is asynchronous. A reaction on the `@codex review` trigger means
the request was seen; it does not mean the review is complete. Treat
`clearance-3-ready-for-approval` as provisional until the Codex review settle
gate below has passed for the current PR head SHA.

### 6.1. Codex Review Settle Gate

Run this gate after every `@codex review` trigger and after every push that
changes the PR head SHA.

Record the current head SHA:

```bash
gh pr view <pr> --repo <owner/repo> --json headRefOid,mergeable,labels,reviews,comments,statusCheckRollup
```

Then inspect review threads with GraphQL, not only flat PR comments. GitHub
GraphQL connections are paginated; a single page is not authoritative.

```bash
# First request: use null.
# Subsequent requests: use the previous reviewThreads.pageInfo.endCursor.
gh api graphql \
  -F owner=<owner> \
  -F repo=<repo> \
  -F number=<pr> \
  -F threadsCursor=<threads-cursor-or-null> \
  -f query='query($owner:String!, $repo:String!, $number:Int!, $threadsCursor:String) {
    repository(owner:$owner, name:$repo) {
      pullRequest(number:$number) {
        reviewThreads(first:100, after:$threadsCursor) {
          pageInfo { hasNextPage endCursor }
          nodes {
            id
            isResolved
            isOutdated
            path
            line
            comments(first:100) {
              pageInfo { hasNextPage endCursor }
              nodes {
                author { login }
                body
                createdAt
                url
              }
            }
          }
        }
      }
    }
  }'
```

Repeat the `reviewThreads` query until `reviewThreads.pageInfo.hasNextPage` is
false. When `hasNextPage` is true, the next request must replace
`threadsCursor` with the previous response's
`reviewThreads.pageInfo.endCursor`; do not keep querying with a null cursor. For
any thread where `comments.pageInfo.hasNextPage` is true, fetch that thread by
GraphQL node id and paginate its comments with a thread-local comment cursor
until all comments are loaded. Treat any unexhausted thread or comment
connection as non-terminal.

```bash
# First request: use null.
# Subsequent requests: use the previous comments.pageInfo.endCursor.
gh api graphql \
  -F threadId=<thread-id> \
  -F commentsCursor=<comments-cursor-or-null> \
  -f query='query($threadId:ID!, $commentsCursor:String) {
    node(id:$threadId) {
      ... on PullRequestReviewThread {
        comments(first:100, after:$commentsCursor) {
          pageInfo { hasNextPage endCursor }
          nodes {
            author { login }
            body
            createdAt
            url
          }
        }
      }
    }
  }'
```

Repeat the thread-comment query until `comments.pageInfo.hasNextPage` is false.
When `hasNextPage` is true, the next request must replace `commentsCursor` with
the previous response's `comments.pageInfo.endCursor`; do not keep querying with
a null cursor.

A Codex review is terminal only when one of these signals is observed for the
current head SHA:

- Codex posts a no-major-issues result, such as `Didn't find any major issues`.
- Codex submits a completed review for the current head SHA and thread-aware
  inspection shows no new actionable P0/P1/P2 review threads.
- A human operator explicitly classifies all new Codex comments as
  non-actionable and records the rationale in the PR.

These are not terminal signals:

- An `eyes` reaction on `@codex review`.
- Green CI by itself.
- `clearance-3-ready-for-approval` by itself.
- A single `gh pr view` response with no review body yet.
- Absence of new comments before the configured wait window has elapsed.

If Codex posts actionable feedback, fix it, push, post a fresh `@codex review`,
and restart this settle gate for the new head SHA. Do not report the PR as ready
for human approval while a requested Codex review is still settling.

### 6.2. Ready-For-Approval Declaration Checklist

Before telling the requester that a PR is ready for approval, verify all of the
following for the current head SHA:

- [ ] CI checks are green or intentionally skipped with a recorded reason.
- [ ] The latest requested Codex review reached a terminal signal.
- [ ] GraphQL `reviewThreads` inspection shows no unresolved actionable
      P0/P1/P2 threads.
- [ ] Any outdated or already-fixed review threads are either manually resolved
      by an authorized actor or explicitly called out as open-but-non-actionable
      with rationale.
- [ ] Clearance is `clearance-3-ready-for-approval` after the latest Codex
      terminal signal, not before it.
- [ ] The final status message names the PR number, head SHA, CI result, Codex
      terminal signal, and Clearance label.
- [ ] PR source confirmed — headRepository matches baseRepository (no fork PR).

### 7. Clearance And Human Approval

Clearance is the readiness gate for the PR.

| Clearance state | Meaning | Continue? |
|-----------------|---------|-----------|
| `clearance-1-pending` | Automation or review request state is incomplete | No |
| `clearance-2-blocked` | Unresolved threads, changes requested, or automation error | No |
| `clearance-3-ready-for-approval` | Automation is ready; configured human approval missing | Human may review and approve |
| `clearance-4-ready-for-merge` | Current-head approval and automation are ready | Human may merge |

The Assembly loop must not report "ready to merge" at Stage 1 or Stage 2. The
normal handoff point is Stage 3. Stage 4 is the final merge-ready state after
human approval.

Stage 3 is not sufficient if a requested Codex review has not reached a
terminal signal for the current head SHA. In that case, continue monitoring
Codex and review threads before handing the PR to a human for approval.

### 8. Merge And Completion Gate

Only a human/operator merges. After merge:

1. Confirm the PR state is `MERGED`.
2. Confirm the target issue closed through the linked PR.
3. Confirm CI checks on the merged PR were green or intentionally skipped.
4. Perform the VOY-1811 completion gate for related PRs when the task had
   previous PR attempts, superseded branches, or review threads.
5. Delete the remote branch if GitHub did not do it automatically and cleanup
   is safe.

---

## Failure Paths

### Backend Failure

If the progress comment shows `Adapter: failed`, do not guess. Determine the
failing phase and retry only after the cause is understood.

Until #93 lands, diagnostics may be sparse. Use bridge logs, issue/PR comments,
and any retained local watcher/debug output. After #93 lands, follow the
failure-diagnostics SOP or runbook linked from the Assembly failure comment.

Common backend failures:

- Missing OMP command or model credential.
- Verification command mismatch for the target repository.
- Git clone/push authentication failure.
- OMP timeout or malformed/no-output behavior.
- Generated change fails tests.

### No Changes

`no_changes` is valid only when no implementation is needed. If the issue is
still open and acceptance criteria are unmet, clarify the issue and retry. Do
not merge an empty PR or close the issue manually to make the run look
successful.

### Failed CI

Treat CI failures like normal PR findings. Fix in the branch, push, trigger
`@codex review`, and wait for Clearance. If the failure is environmental,
record that evidence in the PR before asking for human approval.

### Missing Verification Command

Stop before a real backend run if the target repository does not have safe
verification commands. Configure a repository-specific
`ASSEMBLY_VERIFICATION_COMMANDS_<encoded-repo>` value or an explicit global
override, restart/reload the bridge if required, and retry only after the
operator can name the commands that will run.

### Codex Requested Changes

Actionable Codex findings must be fixed or explicitly classified as
non-actionable with rationale. Do not rely on Clearance Stage 3 if a newer
Codex review is still pending or if review threads remain unresolved.

### Clearance Blocked

When Clearance is Stage 2, inspect the Clearance comment details. Resolve
unresolved review threads, address changes requested, fix automation failure,
or rerun `/clearance` only after the underlying condition changes.

### Unsafe Scope

Stop immediately if Assembly attempts or proposes any forbidden operation:
merge, approve, resolve review threads, apply Clearance/Countdown labels,
modify branch protection, close issues directly, or override another bot's
verdict. Capture evidence and file a Voyager issue before retrying.

---

## Examples

Normal Assembly run:

1. Issue `frankyxhl/trinity#79` is `blueprint-ready`, Stack-classified, and
   allow-listed.
2. Operator confirms bridge health, backend selection, and
   `ASSEMBLY_VERIFICATION_COMMANDS_FRANKYXHL__TRINITY`.
3. Operator comments `/assembly`.
4. Assembly opens a PR with `Closes #79` and posts `@codex review`.
5. Operator waits for CI, Codex, and Clearance Stage 3.
6. Human approval moves Clearance to Stage 4; a human merges.

Direct implementation instead:

1. A Voyager issue asks whether Assembly should store bridge env in TOML.
2. The issue needs policy/design agreement before implementation.
3. Operator files or refines the issue, then implements directly through
   Codex or a human PR instead of triggering Assembly.

---

## Privacy Boundary

Public GitHub comments may contain:

- Repository, issue number, branch name, PR number.
- Adapter status and concise non-secret summary.
- Verification command names.
- Future audit/run IDs from #92.
- Future bounded, redacted failure excerpts from #93.

Public GitHub comments must not contain:

- GitHub installation tokens.
- API keys, private keys, or credential-bearing URLs.
- Full OMP transcripts.
- Raw environment dumps.
- Local private checkout paths unless a future SOP explicitly declares them
  safe to expose as lookup hints.

Private Wukong storage may contain OMP transcripts, checkout paths, and richer
debug artifacts. #92 will add public audit IDs mapped to private manifests.
#93 will add SOP-backed failure diagnostics and debug retention.

---

## Operator Checklist

Before triggering:

- [ ] Issue is `blueprint-ready`.
- [ ] Issue has Stack classification.
- [ ] Repo is installed and allow-listed for Assembly.
- [ ] Actor is authorized.
- [ ] Backend selection is intentional.
- [ ] Verification commands are correct for the target repo.
- [ ] Bridge health shows expected build commit and `dry_run` state.

After PR opens:

- [ ] PR body links/closes the issue.
- [ ] Diff matches the issue scope.
- [ ] Assembly posted `@codex review`.
- [ ] CI is green or intentionally skipped.
- [ ] PR source confirmed — headRepository matches baseRepository (no fork PR).
- [ ] Local verification was run when needed.
- [ ] Codex reached a terminal review signal for the current head SHA.
- [ ] GraphQL review-thread inspection found no unresolved actionable findings.
- [ ] Clearance is Stage 3 after the latest Codex terminal signal, before
      approval handoff.
- [ ] Clearance is Stage 4 before merge.

After merge:

- [ ] PR is merged by a human/operator.
- [ ] Issue is closed by the merged PR.
- [ ] Related PR/review-thread completion gate is clean.
- [ ] Any follow-up observability, audit, or rollout issue is filed.

---

## Integration Points

- **VOY-1811:** broad multi-agent loop configuration and completion gate.
- **VOY-1817:** Assembly MVP routing, contract, writeback, and boundaries.
- **VOY-1818:** actor authorization gate.
- **VOY-1821:** fake subprocess backend and real OMP canary history.
- **#92:** future private audit manifests and public trace IDs.
- **#93:** future failure diagnostics, debug retention, and failure-inspection
  SOP.

Future Assembly comments, audit manifests, and operator handoffs should cite
this SOP by name: `VOY-1822 Assembly-Driven Implementation Loop`.

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-05-24 | Initial SOP for issue #94, derived from VOY-1811 and specialized for Assembly's issue-to-PR implementation loop. | Codex |
| 2026-05-24 | Added Codex review settle gate and final ready-for-approval checklist for issue #98. | Codex |
| 2026-05-24 | Added PR source precondition, fork PR caveats, and code-level `headRepository == baseRepository` gate for issue #99. | Codex |
