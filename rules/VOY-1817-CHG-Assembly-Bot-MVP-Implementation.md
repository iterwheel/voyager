# CHG-1817: Assembly Bot MVP Implementation

**Applies to:** VOY project
**Last updated:** 2026-05-23
**Last reviewed:** 2026-05-23
**Status:** Proposed
**Date:** 2026-05-23
**Scheduled:** After CHG plan-review approval in the active VOY-1811 loop.
**Requested by:** Frank Xu (via issue #69, 2026-05-23)
**Priority:** P1
**Change Type:** Normal
**Targets:** `voyager/bots/assembly/`, `voyager/core/github_app.py`, `voyager/core/writeback.py`, `voyager/server.py`, `tests/unit/`, `tests/bdd/features/`, `tests/bdd/step_defs/`
**Closes:** #69
**Related:** VOY-1805 SOP (Assembly boundaries §5), VOY-1806 SOP (Assembly permission row), VOY-1807 REF (Assembly registry placeholder row), VOY-1811 REF (Multi-Agent Loop Configuration §Codex Review Trigger), CHG-1813 (writeback failure handling pattern)

---

## What

Implement the first safe version of the `iterwheel-assembly` bot. The bot reacts
to `/assembly` or `/implement` issue comments on `blueprint-ready`,
allow-listed issues, builds a structured **Assembly Job Contract**, hands it to
a pluggable execution adapter, and (when the adapter produces commits) creates
a feature branch, opens or updates a pull request linked to the source issue,
posts an `@codex review` trigger comment, and upserts an Assembly progress
comment on both the issue and the PR.

This CHG ships the routing, preconditions, contract, adapter interface, branch
and PR writeback, comment writeback, `@codex review` trigger, dry-run plan
recording, unit tests, and BDD scenarios. The real `pi -> oh-my-pi ->
DeepSeek V4 Pro` execution backend ships as an explicit stub
(`PiOhMyPiDeepSeekAdapter`) that raises `NotImplementedError` unless a
follow-up issue wires it. The default backend is the `DryRunAdapter`, which
records the planned contract without spawning any subprocess.

## Why

Assembly is the first code-writing bot in the Rocket Factory. Wiring the full
execution backend and the GitHub mutation surface in one PR is high-risk: the
backend depends on external CLIs (`pi`, `oh-my-pi`) that are not yet installed
on Wukong or in CI, and the GitHub-side mutations (branch create, PR open,
push) require careful permission boundary review per VOY-1805 §5. Splitting
the work along the adapter seam lets this CHG ship a fully reviewable bot with
deterministic tests today, while keeping a clear follow-up for the real
execution backend.

The bot must satisfy issue #69's acceptance criteria:
deterministic routing, hard refusal on non-ready issues and non-allow-listed
repositories, structured job contracts, deterministic branch naming, PR
open/update, `@codex review` after each push (per VOY-1811 Phase 8), no
merge/approve/thread-resolve authority, dry-run plan recording, and unit + BDD
test coverage.

## Out of Scope

- Wiring the real `pi -> oh-my-pi -> DeepSeek V4 Pro` subprocess pipeline; the
  adapter interface and the `PiOhMyPiDeepSeekAdapter` placeholder ship here,
  but the production backend is a follow-up issue.
- Granting Assembly any new GitHub App permissions beyond the VOY-1806 matrix
  row.
- Changing branch protection rules; the SOP-level prohibition on Assembly
  self-merging is enforced by branch protection plus boundary tests, not by
  new repo settings in this CHG.
- Static Fire, Clearance, and Countdown handoff orchestration; this CHG opens
  the PR and trusts the existing bots to run their own stages.
- Production rollout to `iterwheel/voyager` itself. The
  `iterwheel-assembly` App is already **installed** on `iterwheel/voyager`,
  `iterwheel/voyager-sandbox`, `frankyxhl/alfred`, and `frankyxhl/trinity`
  per VOY-1807 — installation grants the App technical access; it does not
  grant the bridge permission to act. The bridge gate is the
  `BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_ASSEMBLY` env var per D6, which
  ships empty. Initial production allow-list is exactly
  `iterwheel/voyager-sandbox`; expansion to `iterwheel/voyager` is a
  separate operator step deliberately not taken in this CHG.

## Impact Analysis

### Systems affected

- Webhook router (`voyager/server.py`): adds a new `route_assembly_event`
  branch to the candidate-routes list.
- Writeback dispatcher (`voyager/core/writeback.py`): adds a new
  `dynamic == "assembly_implementation"` branch alongside the existing
  `clearance_readiness` branch.
- GitHub App client (`voyager/core/github_app.py`): adds the minimum REST
  surface needed by Assembly — `create_branch_ref`, `branch_ref_exists`,
  `create_pull_request`, `update_pull_request`, `find_pull_request_by_head`.
- Bot package: adds `voyager/bots/assembly/` with the parsing, validation,
  contract, adapter, and writeback shape code.

### Channels affected

- GitHub PR-level Assembly progress comments (issue and PR).
- GitHub `@codex review` trigger comment after PR push.
- Bridge `_recent_writebacks` in-memory ring for `/e2e/recent_writebacks`.

### Downtime required

None. The bridge can be restarted normally; webhook delivery is idempotent
because the upsert marker is unique per Assembly invocation surface and the
branch-create / PR-open paths short-circuit on existing branches and PRs.

### External dependencies

- GitHub REST API: `git/refs`, `pulls`, `issues/comments` (already used).
- No new external services; the real `pi -> oh-my-pi -> DeepSeek V4 Pro`
  subprocess stack is deferred to the follow-up adapter PR.

### Rollback plan

Revert the implementation commits for this CHG. Existing Blueprint, Stack, and
Clearance routes are untouched. Because Assembly defaults to `DryRunAdapter`
and the default allow-list is empty for production repos, no GitHub mutations
are written until an operator opts in by both setting
`BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_ASSEMBLY` and turning off
`DRY_RUN`. Rollback does not require state-file changes; the writeback
dispatcher is additive.

Post-rollback verification:

1. Confirm webhook ingestion still produces Blueprint, Stack, and Clearance
   routes per `tests/unit/test_routing_test_bot_logins.py` and the existing
   BDD `server.feature` scenarios.
2. Confirm `/e2e/recent_writebacks` returns expected shapes for the three
   prior agents.
3. Confirm the `iterwheel-assembly` App installation is unchanged in
   `gh api /repos/iterwheel/voyager/installation`.

## Surfaces

| # | Surface | Change |
|---|---------|--------|
| 1 | `voyager/bots/assembly/__init__.py` | New module with `route_assembly_event(event, payload)`, the public entry the server calls. |
| 2 | `voyager/bots/assembly/constants.py` | `ASSEMBLY_AGENT_SLUG = "iterwheel-assembly"`, `ASSEMBLY_AGENT_ID`, `ASSEMBLY_COMMENT_MARKER`, `ASSEMBLY_COMMANDS = ("/assembly", "/implement")`, refusal reason enums. |
| 3 | `voyager/bots/assembly/commands.py` | `parse_assembly_command(body)` — matches `/assembly` or `/implement` at the start of a comment line, returns command + optional `--dry-run`, `--allow-missing-stack` flags or `None`. |
| 4 | `voyager/bots/assembly/preconditions.py` | `validate_preconditions(issue, repository, payload)` — checks issue is not a PR, has `blueprint-ready`, has `stack-type-*` (unless `--allow-missing-stack`), is on the allow-list. Returns `ok` or refusal record. |
| 5 | `voyager/bots/assembly/branch.py` | `make_branch_name(issue_number, issue_title)` — `<issue_number>-<short-kebab-slug>` matching the existing convention. |
| 6 | `voyager/bots/assembly/job_contract.py` | `AssemblyJobContract` dataclass + `build_job_contract(issue, repository, branch, delivery_id)`. Includes repo, issue number/URL/title/body, branch name, base branch, extracted task summary + acceptance criteria, **forbidden operations** (canonical list from VOY-1805 §5 Deny column), and **verification commands** (`pytest`, `ruff check`, `mypy voyager`). |
| 7 | `voyager/bots/assembly/adapters.py` | `ExecutionAdapter` Protocol with `async def execute(contract) -> AdapterResult`. `DryRunAdapter` records the contract and returns `{commits: [], status: "dry_run"}`. `PiOhMyPiDeepSeekAdapter` placeholder raises `NotImplementedError("execution backend deferred; see follow-up issue")`. Backend chosen by `ASSEMBLY_EXECUTION_BACKEND` env (default `dry-run`). |
| 8 | `voyager/bots/assembly/comment.py` | `build_assembly_comment(status, contract, result, refusal=None)` — upsert body for the assembly progress comment, with marker. |
| 9 | `voyager/bots/assembly/routing.py` | `should_run_assembly(event, payload)` (true for `issue_comment.created` with a command match) and the `route_assembly_event` body building the writeback shape `{dynamic: "assembly_implementation", contract, refusal, comment_*}`. |
| 10 | `voyager/core/github_app.py` | Add `branch_ref_exists`, `create_branch_ref`, `find_pull_request_by_head`, `create_pull_request`, `update_pull_request`. All use existing `request()` helper; no new auth surface. |
| 11 | `voyager/core/writeback.py` | Add `dynamic == "assembly_implementation"` branch in `dispatch_route_writeback`. Implements: (a) re-validate preconditions on live issue (defense in depth), (b) call `adapter.execute(contract)`, (c) when commits produced, ensure branch ref + create/update PR with `Closes #N`, (d) post `@codex review` comment, (e) upsert Assembly progress comment on issue and PR. Honors `dry_run_enabled()` like the existing path. Uses `build_writeback_failure` per CHG-1813. |
| 12 | `voyager/server.py` | Import `route_assembly_event` and append it to `candidate_routes`. |
| 13 | `tests/unit/test_assembly_commands.py` | Command parsing: `/assembly`, `/implement`, flags, non-matching, comment with prefix `text /assembly` (should not match — must start the line). |
| 14 | `tests/unit/test_assembly_preconditions.py` | Refusal flows: missing `blueprint-ready`, missing stack labels, repo not allow-listed, payload is a PR not an issue, `--allow-missing-stack` override. |
| 15 | `tests/unit/test_assembly_branch.py` | Deterministic branch naming including unicode normalization, length cap, idempotence. |
| 16 | `tests/unit/test_assembly_job_contract.py` | Contract field correctness, forbidden-ops canonical list matches VOY-1805 §5 Deny column, verification commands present. |
| 17 | `tests/unit/test_assembly_routing.py` | Full route shape for matching and non-matching events. |
| 18 | `tests/unit/test_assembly_adapters.py` | `DryRunAdapter` returns no commits; `PiOhMyPiDeepSeekAdapter.execute` raises `NotImplementedError`. |
| 19 | `tests/unit/test_assembly_writeback_dispatcher.py` | Direct test of the new `dynamic == "assembly_implementation"` branch in `dispatch_route_writeback` covering all five gate-corner-table rows (allow-list deny, AL+/DR+/BE=dry, AL+/DR+/BE=pi, AL+/DR−/BE=dry, AL+/DR−/BE=pi). Mocks `GitHubAppClient` and `ExecutionAdapter`. |
| 20 | `tests/unit/test_assembly_writeback_partial_failure.py` | Per D11: branch create succeeds + PR open fails records `writeback_failures` and continues to comment; PR exists + codex trigger fails records failure and continues to progress comment; all-fail records four entries and still upserts the progress comment. |
| 21 | `tests/bdd/features/assembly.feature` | Five scenarios: (1) `/assembly` on ready, allow-listed issue runs dry-run plan and records full intended actions; (2) refusal on non-`blueprint-ready` issue posts a refusal comment, no GitHub writes; (3) refusal on a non-allow-listed repo produces no comment and no writes; (4) `/assembly --allow-missing-stack` on a `blueprint-ready` issue without `stack-type-*` builds the contract; (5) adapter raises `NotImplementedError` (BE=pi corner): progress comment upserts the failure, no branch / PR / codex writes. |
| 22 | `tests/bdd/step_defs/test_assembly_steps.py` | Step definitions for the five scenarios. |
| 23 | `tests/fixtures/webhooks/` | Add `assembly_command_ready.json`, `assembly_command_not_ready.json`, `assembly_command_missing_stack.json`. |
| 24 | `rules/VOY-1807-REF-GitHub-App-Registry.md` | Rewrite the `iterwheel-assembly` bridge write-back row from the current "_(pending implementation: `/assembly` or `/implement` issue comment per VOY-1805)_" placeholder to: "**Trigger:** `/assembly` or `/implement` issue comment on a `blueprint-ready` allow-listed issue. **Write-back:** When `ASSEMBLY_EXECUTION_BACKEND` produces commits, creates a `<issue#>-<slug>` branch ref on the source repo, opens or updates a PR with `Closes #N`, posts `@codex review` after each push, and upserts an Assembly progress comment on both the issue and the PR. Never merges, approves, resolves review threads, or applies Clearance/Countdown labels. Initial allow-list ships empty; `iterwheel/voyager-sandbox` is the intended first production target via `BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_ASSEMBLY`." Plus a Change History row. |
| 25 | `config.example.toml` | Add commented (default-off) `# BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_ASSEMBLY="iterwheel/voyager-sandbox"` and `# ASSEMBLY_EXECUTION_BACKEND=dry-run` hints in the Assembly App block. Both stay commented so the default-deny posture (D6) is preserved — uncommenting either is an explicit operator action. |

## Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Default execution backend is `DryRunAdapter`; `PiOhMyPiDeepSeekAdapter` is a `NotImplementedError` stub. | Keeps this PR safe and reviewable, avoids depending on `pi`/`oh-my-pi` in CI, lets the GitHub-side wiring land independently. The adapter seam is the explicit extension point for the follow-up. |
| D2 | Trigger surface is `issue_comment.created` only, never `issues.*`. | VOY-1805 §5: "Its trigger model starts with a manual slash command." Listening to `issues.*` would let Assembly auto-run on label changes, which violates the manual-trigger requirement. |
| D3 | Two orthogonal gates: `DRY_RUN` env (GitHub mutations) and `ASSEMBLY_EXECUTION_BACKEND` env (subprocess execution). | Lets a sandbox run real GitHub mutations against a stub adapter, or a future canary run real adapter against dry-run GitHub. Both default to safe. |
| D4 | Preconditions are checked twice: once when building the route, once when the writeback dispatcher fires. | Defense in depth: the issue may be edited between event ingestion and dispatcher run; the live issue is authoritative for writes. Mirrors how Blueprint and Stack both re-validate. |
| D5 | New writeback uses `dynamic` dispatch (`dynamic == "assembly_implementation"`) instead of extending `apply_route_writeback`. | Assembly's writeback shape (branch create + PR open + comment upsert + reaction trigger) doesn't fit the issue-label-comment-reaction shape `apply_route_writeback` was built for. Mirrors the Clearance dynamic path. |
| D6 | Initial allow-list is empty in code; operators must explicitly set `BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_ASSEMBLY`. | Matches the existing per-agent allow-list pattern. Production rollout requires an explicit operator step, not just a deploy. |
| D7 | `@codex review` is posted as a fresh PR comment on **every push** Assembly makes to the PR branch within an invocation (not upserted, not once-per-invocation). | Matches VOY-1811 §Codex Review Trigger Phase 8 verbatim ("after each push... post `@codex review`"). Codex re-engages reliably only on a fresh trigger comment per push, not per invocation. Supersedes the earlier Open Question #2 which is now closed. |
| D8 | Branch naming uses `<issue_number>-<slug>` (slug from issue title, kebab-cased, length-capped at 50, ASCII-folded). Empty-slug fallback: when slug-after-folding is empty, use `<issue_number>-issue`. | Matches the existing convention from recent PRs #70 / #71 / #72 (`67-assembly-bot-sop-permissions`, `68-assembly-github-app-setup`, `68-record-assembly-app-installation`). Empty-slug fallback prevents `git update-ref` failures on titles like `[Bug]: 🚨🚨🚨`. |
| D9 | Forbidden operations and verification commands are canonical constants in `voyager/bots/assembly/constants.py`. | The pi/oh-my-pi adapter must not be allowed to invent its own constraint list. Centralizing makes future audit trivial. |
| D10 | Assembly never writes labels. | VOY-1805 §5 reserves Clearance/Countdown labels for those bots; Stack labels for Stack; Blueprint labels for Blueprint. Assembly's only GitHub-visible writes are the branch ref, the PR, the upserted comment, and the `@codex review` trigger comment. |
| D11 | Writeback dispatcher writes are sequenced **branch → PR → codex-trigger → progress-comment**. Each step records its own failure to `writeback_failures` (per CHG-1813). **Dependencies**: PR open depends on branch ref existing; codex-trigger depends on PR open succeeding (or already existing). **The progress-comment step is independent of every preceding step and always runs, including when branch/PR/codex steps fail and including when the adapter raises before any write attempt.** **Idempotency**: branch ref creation is conditional on `branch_ref_exists`; PR open is conditional on `find_pull_request_by_head` returning none, otherwise the existing PR is updated. **No automatic cleanup**: orphaned branches from mid-pipeline failure are left in place because retry is the recovery path; the progress comment surfaces the failure to the operator. | Mirrors CHG-1813's "capture failures, keep going where safe" pattern. Cleanup-on-fail would re-introduce the destructive-action risk that branch idempotency is meant to avoid. The always-runs progress-comment guarantee is what makes adapter failures (BE=pi `NotImplementedError`) operator-visible without polling. |
| D12 | The codex trigger comment targets the bot identity `chatgpt-codex-connector[bot]` as named in VOY-1811 §Bot Polling Binding. Polling for Codex's review response is **out of scope** for this CHG; Assembly fires the trigger and exits. | Pinning the bot identity prevents accidental drift if a different reviewer-app slug appears later. Polling is Phase 8 of the loop, not Assembly's responsibility. |
| D13 | **Installation scope ≠ allow-list scope.** Per VOY-1807 the App is technically installed on four repos including `iterwheel/voyager`; per D6 the bridge env-var allow-list defaults empty. The bridge gate fires first (allow-list deny → no route), so install-scope expansion alone never grants Assembly the ability to write. Routing logs the deny via the existing `repository_allowlist_denied` warning. | Eliminates the ambiguity that two reviewers (MiniMax P0, DeepSeek P0) flagged. The two gates are deliberately independent: install scope = "can this App auth", allow-list = "will the bridge route". |
| D14 | Acceptance-criteria extraction falls back gracefully: prefer `## Acceptance Criteria` bullets; if absent, fall back to a one-element list `[issue.title]` and tag the contract with `acceptance_criteria_source = "title_fallback"`. Same for `task_summary`: prefer `## Problem / Goal`, else `issue.title`. The Blueprint gate already enforces `Acceptance Criteria` for `blueprint-ready`, so the fallback is defensive only. | Prevents the contract builder from raising on issues that pass Blueprint but later edit-out the section between Blueprint check and `/assembly` invocation. |

## Gate Corner Table

The bridge writes nothing without the **allow-list** gate; the adapter
attempts nothing without the **backend** gate; the dispatcher records
nothing without `DRY_RUN=false`. Each gate is independent.

Legend: `AL+` = repo in `BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_ASSEMBLY`,
`AL−` = repo absent. `DR+` = `DRY_RUN=true` (default), `DR−` = `DRY_RUN=false`.
`BE=dry` = `ASSEMBLY_EXECUTION_BACKEND=dry-run` (default),
`BE=pi` = `ASSEMBLY_EXECUTION_BACKEND=pi-oh-my-pi-deepseek`.

| AL | DR | BE | Route built? | Adapter runs? | GitHub writes? | Result shape |
|----|----|----|--------------|---------------|----------------|--------------|
| AL− | * | * | No (denied at `_repository_allowed_for_agent`) | No | No | Bridge response includes `filtered.routes`; no record in `_recent_writebacks`. |
| AL+ | DR+ | BE=dry | Yes | Yes (records plan) | No | `applied: false, dry_run: true, planned: {...}`; `adapter_result.status = "dry_run"`. |
| AL+ | DR+ | BE=pi | Yes | **Raises `NotImplementedError`** | No | Caught; `applied: false, dry_run: true, adapter_result.status = "failed", adapter_result.summary: "execution backend deferred"`. |
| AL+ | DR− | BE=dry | Yes | Yes (records plan) | **Comment-only** — branch/PR/codex steps skipped because no commits to push | `applied: true, branch: null, pull_request: {action: "skipped_no_changes"}, assembly_comment_id: <int>`. |
| AL+ | DR− | BE=pi | Yes | **Raises `NotImplementedError`** | **Progress comment only** — branch/PR/codex steps skipped on adapter failure per D11 | `applied: true, adapter_result.status: "failed", branch: null, pull_request: {action: "skipped_no_changes"}, assembly_comment_id: <int>` with failure surfaced in comment body. Rows 4 and 5 share the same `pull_request.action` because both reach the "no commits to push" code path; the operator-visible distinction lives in `adapter_result.status` (`dry_run` vs `failed`) and the progress-comment body. |

Install scope (per VOY-1807) is upstream of all corners: a repo not in the
App's installed list never produces a webhook delivery to the bridge in the
first place (GitHub does not deliver webhooks for repositories where the App
is not installed), so allow-list evaluation never runs. That case is
operationally indistinguishable from AL− at the bridge — no record appears
in `_recent_writebacks`, but the cause is upstream of routing, not a
routing decision.

## Assembly Job Contract Schema

```python
@dataclass(frozen=True)
class AssemblyJobContract:
    repository: str                  # "iterwheel/voyager-sandbox"
    issue_number: int                # 69
    issue_url: str                   # html_url from payload
    issue_title: str                 # "[Feature]: Implement Assembly..."
    issue_body: str                  # full markdown body
    branch_name: str                 # "69-implement-assembly-coding-bot"
    base_branch: str                 # "main"
    task_summary: str                # extracted from "Problem / Goal"
    acceptance_criteria: list[str]   # extracted bullets
    forbidden_operations: tuple[str, ...]   # canonical list from VOY-1805 §5
    verification_commands: tuple[str, ...]  # ("pytest tests/", "ruff check .", "mypy voyager")
    delivery_id: str                 # X-GitHub-Delivery
    requested_at: str                # iso UTC
```

The `forbidden_operations` tuple is fixed by VOY-1805 §5 Deny column and is
not customizable per invocation.

## Writeback Result Schema

```python
{
    "applied": bool,                # true when at least one mutation attempted
    "dry_run": bool,                # echo of DRY_RUN
    "execution_backend": str,       # "dry-run" | "pi-oh-my-pi-deepseek"
    "refusal": {                    # only when preconditions failed
        "reason": str,              # see refusal enum below
        "missing_labels": list[str],
        "outside_allow_list": bool,
    } | None,
    "contract": dict | None,        # serialized AssemblyJobContract or None on refusal
    "adapter_result": {
        "status": "dry_run" | "executed" | "no_changes" | "failed",
        "commit_shas": list[str],
        "summary": str,
    } | None,
    "branch": {
        "name": str,
        "created": bool,
        "sha": str | None,
    } | None,
    "pull_request": {
        "number": int,
        "url": str,
        "action": "opened" | "updated" | "skipped_no_changes",
    } | None,
    "codex_review_comment_id": int | None,
    "assembly_comment_id": int | None,
    "writeback_failures": [...]     # per CHG-1813
}
```

Refusal reasons (`refusal.reason`) — order matches the routing/precondition
gates in `voyager/bots/assembly/`:

```
"pr_not_issue"
"issue_closed"                    # added per CHG-1819 F6
"missing_blueprint_ready_label"
"missing_stack_type_label"
"repository_not_allowed"
"unauthorized_actor"              # from VOY-1818
```

Order is implementation-stable: it mirrors the constants in
`voyager/bots/assembly/constants.py` and the routing order (PR-shape check
→ closed-state check → label checks → repository allow-list → actor
authorization). Not alphabetical.

## Testing / Verification

Unit:

- `tests/unit/test_assembly_commands.py`
- `tests/unit/test_assembly_preconditions.py`
- `tests/unit/test_assembly_branch.py`
- `tests/unit/test_assembly_job_contract.py`
- `tests/unit/test_assembly_routing.py`
- `tests/unit/test_assembly_adapters.py`

BDD:

- `tests/bdd/features/assembly.feature` — five scenarios (per Surface 21):
  - successful sandbox flow (allow-listed, ready issue, dry-run adapter, full plan recorded)
  - refusal on non-ready issue (clear comment, no GitHub mutations)
  - refusal on non-allow-listed repository (no comment, no mutations — matches `_repository_allowed_for_agent` deny semantics)
  - `/assembly --allow-missing-stack` on a `blueprint-ready` issue without `stack-type-*` (contract still built)
  - adapter raises `NotImplementedError` (BE=pi corner): progress comment upserts the failure; no branch / PR / codex writes

Tooling:

- `ruff check .`
- `mypy voyager`
- `pytest tests/unit tests/bdd -k assembly`

The full suite (`pytest`) must remain green; no existing tests should change
unless required by a wiring rename.

## Open Questions for Reviewers

1. **Branch base.** Default is `main`; should operators be able to override
   via a `--base <branch>` command flag? Current proposal: no, defer to follow-up.
2. **Issue progress comment vs PR comment.** VOY-1805 §5 allows both; the
   contract here upserts on both surfaces. Should the issue-side comment be
   write-once (initial acknowledgement) and the PR-side comment be the live
   progress board? Current proposal: both upserted; same marker, different
   per-surface text.

(Open Question #2 from the first review round — `@codex review` cadence —
is now resolved by D7. Open Question #3 from the first round on partial
failure semantics is now resolved by D11.)

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-05-23 | Initial CHG draft for issue #69 — first safe Assembly bot MVP with pluggable execution adapter. | Claude (via VOY-1811) |
| 2026-05-23 | Round 1 plan-review remediation (GLM 9.0 PASS, MiniMax 8.8 FIX, DeepSeek 7.7 FIX): added §Gate Corner Table (P0-A/P0-C); resolved D7 vs OQ#2 contradiction in favor of per-push trigger per VOY-1811 (P0-B); added D11 partial-failure semantics; D12 codex bot identity pinning; D13 install vs allow-list scope split; D14 acceptance-criteria extraction fallback; D8 empty-slug fallback; split Surface 11 unit test into dedicated Surface 19/20; expanded Testing to five BDD scenarios; concrete VOY-1807 row text in Surface 24; clarified Surface 25 stays commented out. | Claude (via VOY-1811) |
| 2026-05-23 | Round 2 plan-review remediation (GLM 9.2 PASS, DeepSeek 9.2 PASS, MiniMax 9.83 PASS): P2 only — corrected Gate Corner Table install-scope wording (no webhook delivery rather than "401"); explicitly stated progress-comment-always-runs in D11; fixed Testing § "three scenarios" typo to "five scenarios". | Claude (via VOY-1811) |
| 2026-05-23 | Phase 6 cross-test divergence fix (DeepSeek finding): Gate Corner Table Row 5 amended to match implementation — `pull_request: {action: "skipped_no_changes"}` instead of `null`. Rows 4 and 5 share the "no commits → no PR" code path; the BE=dry / BE=pi distinction is visible via `adapter_result.status` and the progress comment body, not the `pull_request` field. | Claude (via VOY-1811) |
| 2026-05-23 | CHG-1819 amendment: added `issue_closed` to the refusal enum list (implementation-added during VOY-1817 Phase 5). | Claude (via CHG-1819) |
