# SOP-1822: Assembly-Driven Implementation Loop

**Applies to:** Voyager Assembly bot and managed repositories
**Last updated:** 2026-05-24
**Last reviewed:** 2026-05-24
**Status:** Active
**Date:** 2026-05-24
**Requested by:** Frank Xu (via issue #94)
**Priority:** P2
**Related:** VOY-1811, VOY-1817, VOY-1818, VOY-1821, VOY-1823, #92, #93, #98, #99, #109

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
| PR Source | Managed PRs must satisfy headRepository == baseRepository. Fork PRs are forbidden for managed Assembly/Codex implementation loops unless a human explicitly grants an exception. | `gh pr view <N> --repo <owner/repo> --json isCrossRepository,headRepository | jq -e '(.isCrossRepository | not) and (.headRepository.nameWithOwner == "<owner>/<repo>")' > /dev/null` |
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

### 4.1. Live No-Diff Observation Window

Start the observation clock when the progress comment, bridge log, or audit
manifest shows that a real backend process has started. A "no-diff" interval
means all of the following are true:

- no PR was opened or updated;
- no new commit appeared on the Assembly branch;
- the progress comment did not move to a terminal state;
- the private checkout has no modified files or staged changes visible to
  `git status --short`.

The maximum no-diff wait without operator inspection is 10 minutes. At
T+10 minutes, inspect private evidence before waiting longer:

```bash
python -m json.tool ~/.voyager/state/assembly/audit/<owner>/<repo>/<issue>/<audit-id>.json
git -C <checkout_dir> status --short
git -C <checkout_dir> diff --stat
```

If the manifest has an `omp_session_jsonl_path`, inspect only the local file.
Do not paste transcript contents, local paths, environment dumps, or token-like
values into GitHub comments.

At T+20 minutes, a run that still has no PR, no commit, and no checkout diff is
stale for operator purposes unless it is visibly running verification against
already-created changes. Stop waiting, record the stale-run evidence privately,
and retry only after narrowing the issue or retry contract. Do not post a
duplicate `/assembly` comment merely to "wake up" the backend.

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

### 6.1. Retry Discipline

Each retry must target exactly one concrete blocker or one tightly related
blocker cluster. Examples of valid retry scopes:

- one failing verification command;
- one Codex review finding, or multiple findings in the same file/behavior;
- one publish/writeback failure;
- one `no_changes` result caused by missing or stale acceptance criteria;
- one docs-only correction with explicit target sections.

Do not use broad instructions such as "try again" or "fix the reviews" without
mapping them to file-level acceptance criteria when that information is
available.

Before rerunning Assembly, write a retry contract that the backend can see. If
the current Assembly contract does not include later issue comments, promote
the relevant retry instructions into the issue body or another contract source
that is known to be included. Plain issue comments are operator notes unless a
future implementation explicitly includes them in the Assembly contract.

Use this minimal retry contract shape:

```text
Retry scope:
- Blocker:
- Evidence:
- Target files/sections:
- Acceptance criteria:
- Verification:
- Stop condition:
```

After every retry push, trigger a fresh `@codex review` and restart the Codex
review settle gate for the new head SHA.

### 6.2. Codex Review Settle Gate

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

### 6.3. Ready-For-Approval Declaration Checklist

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

Pushed code, a green branch, and green CI are not approval-ready by themselves.
Approval-ready means the current head SHA has passed verification, Codex has
settled, thread-aware review inspection has no unresolved actionable blockers,
and Clearance has reached Stage 3 after those facts are true.

### 6.4. Follow-Up Issue Boundary

Create a follow-up issue instead of broadening the current implementation PR
when the new work is not required to satisfy the current issue's acceptance
criteria. Common boundaries:

- new Assembly product capability, such as including bounded issue comments in
  the backend contract;
- GitHub App permission, installation, token, or branch-protection changes;
- bridge deployment, runtime configuration, or release-process work;
- audit retention, observability, or transcript-export improvements beyond the
  current failure evidence;
- review feedback that identifies a valid but unrelated defect;
- changes that would require a second PR description, a different owner, or a
  different verification plan.

The current PR may document the follow-up and link the issue, but it should not
silently absorb a second problem just because it was discovered during the
Assembly loop.

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
## App Token Publish Path

### What Is It

`voyager/core/publish.py::assembly_app_publish()` is a reusable function
that pushes `HEAD` to a same-repository branch using the
`iterwheel-assembly` GitHub App installation token, creates or updates a
pull request as `iterwheel-assembly`, and posts a fresh `@codex review`
comment on the PR.

### When To Use Instead Of Personal `gh`/SSH Auth

Use the App token publish path when:

- Your local `gh` or SSH identity lacks write access to the target
  repository (e.g. `ryosaeba1985` cannot push to `iterwheel/voyager`).
- The branch targets a same-repository PR. Fork PRs are forbidden for
  managed Assembly/Codex implementation loops per VOY-1822 §5.1.
- You have network access to `api.github.com`, git is installed, and
  the `iterwheel-assembly` App private key is configured.
- The repository is installed for `iterwheel-assembly` per VOY-1807.

Use personal `gh`/SSH auth when:

- Your local identity has write access to the target repository.
- You are pushing to a personal fork, not the target repository.
- You need to push branches that are not managed by the Assembly loop.
- The `iterwheel-assembly` App is not installed on the repository.

### How It Works

1. **Token minting.** `GitHubAppClient.installation_token()` signs a JWT
   with the App private key and exchanges it for a short-lived installation
   token at `api.github.com/app/installations/{id}/access_tokens`.
2. **Askpass helper.** A temporary `git-askpass.sh` script is written to a
   temp directory with `0o700` permissions. The script reads the token from
   the `ASSEMBLY_GITHUB_TOKEN` env var — a password prompt never reaches a
   TTY or terminal credential helper.
3. **Git push.** `git push --force-with-lease --no-verify origin HEAD:refs/heads/<branch>`.
   `--force-with-lease` refuses if the remote ref has moved since the last
   known state (non-destructive even on amends). `--no-verify` bypasses
   local pre-push hooks after the configured verification has already run.
4. **PR create/update.** The function finds an existing PR by head branch,
   or uses an explicit `pr_number` if provided. If the head branch has no
   open PR, a new PR is created. Otherwise the existing PR body/title is
   updated.
5. **Codex trigger.** A fresh `@codex review` comment is posted on the PR
   as `iterwheel-assembly`.
6. **Cleanup.** The temp directory and askpass script are removed. The
   token is never written to logs, stdout, stderr, or publish results.

### Safety Guarantees

- **Secret-safe.** The installation token is bounded to a subprocess env
  var (`ASSEMBLY_GITHUB_TOKEN`) which is never logged or persisted. The
  askpass file is removed immediately after each call. Any `gh*_` pattern
  in subprocess output is redacted.
- **Non-destructive push.** `--force-with-lease` refuses to overwrite the
  remote ref if someone else has pushed to the branch since the last known
  state. An initial push to a new branch works without error because there
  is no prior remote ref to lease-check against.
- **No hook interference.** `--no-verify` bypasses local pre-push hooks.
  The configured verification commands (`pytest`, `ruff`, `mypy`) run
  *before* the push, so a false-positive local hook cannot block a trusted
  automated publish.

### Usage (Operator Script / REPL)

```python
import asyncio
from voyager.core.config import load_config
from voyager.core.github_app import GitHubAppClient
from voyager.core.publish import assembly_app_publish

config = load_config()
apps = config.apps
client = GitHubAppClient(apps)

result = asyncio.run(
    assembly_app_publish(
        repository="iterwheel/voyager",
        branch="102-my-feature",
        base="main",
        pr_title="My Feature (Closes #102)",
        pr_body="Implements #102.\n\nCloses #102.",
        client=client,
        cwd="/path/to/checkout",
    )
)
print(f"Pushed: {result.pushed}, PR #{result.pr_number}")
```

When `pr_number` is provided (e.g. from a previous run), the function
updates that PR instead of creating a new one. When omitted, it
auto-discovers an open PR for the same head branch.

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

When inspecting private audit or temp checkout state:

- [ ] Use the VOY-1823 manifest lookup path or audit ID; do not guess from
      unrelated local directories.
- [ ] Inspect the manifest locally with `python -m json.tool`.
- [ ] Inspect the checkout locally with `git -C <checkout_dir> status --short`
      and `git -C <checkout_dir> diff --stat`.
- [ ] Inspect OMP transcripts only on the operator machine.
- [ ] Public summaries include only non-secret status, issue/PR IDs, branch
      names, audit IDs, and verification command names.
- [ ] Public summaries exclude local paths, transcript excerpts, raw env dumps,
      credentials, and token-like values.

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

### High-Token Loop Retrospective Template

Use this template in the issue or operator notes when an Assembly-managed loop
spends repeated cycles on waits, no-op retries, review-settle ambiguity, or
manual takeover:

```text
Retrospective:
- Issue / PR:
- Loop type: stale-contract | no-diff-wait | review-settle-delay | publish-bootstrap | clearance-state-mismatch | ambiguous-acceptance | other
- Concrete cause:
- Wasted cycle:
- Evidence checked:
- Prevention rule:
- Follow-up issue:
```

Keep the retrospective public only when it contains no secrets, local private
paths, transcript excerpts, or credential-bearing environment values. Put
private evidence in the VOY-1823 audit record or local operator notes and link
only the public issue/PR IDs.

---

## Integration Points

- **VOY-1811:** broad multi-agent loop configuration and completion gate.
- **VOY-1817:** Assembly MVP routing, contract, writeback, and boundaries.
- **VOY-1818:** actor authorization gate.
- **VOY-1821:** fake subprocess backend and real OMP canary history.
- **VOY-1823:** private Assembly OMP audit lookup procedure.
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
| 2026-05-24 | Added Assembly retry discipline, no-diff observation windows, VOY-1823 audit checklist, follow-up boundary, and retrospective template for issue #109. | Codex |
