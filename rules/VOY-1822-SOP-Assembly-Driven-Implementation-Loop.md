# SOP-1822: Assembly-Driven Implementation Loop

**Applies to:** Voyager Assembly bot and managed repositories
**Last updated:** 2026-05-24
**Last reviewed:** 2026-05-24
**Status:** Active
**Date:** 2026-05-24
**Requested by:** Frank Xu (via issue #94)
**Priority:** P2
**Related:** VOY-1811, VOY-1817, VOY-1818, VOY-1821, #92, #93

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

Assembly upserts one issue progress comment. The first pass should show one of
these states:

| State | Meaning | Next action |
|-------|---------|-------------|
| `refused` | Preconditions failed | Fix the issue/labels/allow-list/actor state, then retry |
| `dry_run` | Contract recorded, no GitHub mutation | Inspect contract; switch runtime if ready |
| `no_changes` | Backend completed without repo changes | Decide whether the issue is already satisfied or needs clearer AC |
| `failed` | Backend or writeback failed | Follow the failure path below |
| `applied` | Branch and PR work succeeded | Move to PR verification |

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

Assembly's own validation is evidence, not a substitute for operator
acceptance. The operator is still responsible for checking semantic fit before
approval.

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
- [ ] Local verification was run when needed.
- [ ] Codex has no unresolved actionable findings.
- [ ] Clearance is Stage 3 before approval handoff.
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
