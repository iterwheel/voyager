# CHG-1813: Surface Clearance writeback failures in readiness panel

**Applies to:** VOY project
**Last updated:** 2026-05-18
**Last reviewed:** 2026-05-18
**Status:** Approved
**Date:** 2026-05-17
**Scheduled:** After CHG plan-review approval in the active VOY-1811 loop. If
VOY-1811 full automation is unavailable in the local Codex runtime, proceed via
manual Trinity review dispatch under COR-1602 while preserving the same raw
score gate.
**Requested by:** Frank Xu (via issue #45, 2026-05-17)
**Priority:** P1
**Change Type:** Normal
**Targets:** `voyager/core/github_app.py`, `voyager/bots/clearance/pipeline.py`, `voyager/bots/clearance/enrichment.py`, `voyager/core/writeback.py`, Clearance tests
**Closes:** #45
**Related:** VOY-1805 SOP (GitHub Bot Accounts), VOY-1806 SOP (GitHub App Permission Matrix), VOY-1809 CHG (Wave 7C stale verdict guard), VOY-1811 REF (Multi-Agent Loop Configuration), COR-1617 SOP (Multi-Agent Workflow Loop)

---

## What

Capture GitHub writeback failures from Clearance automation and surface
sanitized operator diagnostics in the PR-level Clearance readiness panel. The
first required path is Stage 1.5 `resolveReviewThread` failure handling; the
implementation must catch HTTP-layer failures (`httpx.HTTPError`, branching on
`httpx.HTTPStatusError` when status is available), timeout failures
(`httpx.TimeoutException` via `httpx.HTTPError`, plus builtin `TimeoutError`
where already used by this codebase), and GraphQL error payload failures via a
typed `GitHubGraphQLError` raised by `GitHubAppClient.graphql()` when
`data.errors` is present. Related
label/comment/reaction writeback failures should be returned and logged in a
structured, secret-safe shape instead of disappearing into server logs.

## Why

Voyager observed `resolveReviewThread` returning
`FORBIDDEN: Resource not accessible by integration` after enabling
`iterwheel/voyager`. That class of failure usually indicates GitHub App
permission or installation mismatch. Operators should see a compact GitHub-side
warning on the PR panel, with enough sanitized context to act, without needing
Wukong log access.

## Out of Scope

- Changing GitHub App permission grants or installation access.
- Adding retry/backoff policy for failed GitHub writes.
- Broad auditing of all managed repositories.
- Changing Clearance verdict semantics unrelated to writeback failure
  observability.

## Impact Analysis

### Systems affected

- Clearance Stage 1.5 review-thread sync.
- Dynamic Clearance readiness comment rendering.
- Generic route writeback result shape.
- Server `/e2e/recent_writebacks` records.
- State JSONL records containing `PollRecord.stage15_actions`.

### Channels affected

- GitHub PR-level Clearance marker comments.
- Wukong structured logs.
- Local e2e debug endpoint records.

### Downtime required

None. This is an application-code change and does not require service restart
until the normal deployment step.

### External dependencies

The behavior still depends on GitHub REST and GraphQL APIs and the installed
permissions of `iterwheel-clearance[bot]`. This CHG observes permission/API
failures; it does not grant permissions.

### Rollback plan

Revert the implementation commits for this CHG in reverse order. Existing
successful label/reaction/comment/review-thread writes should return to the
prior behavior because no API contract migration is required. Persisted JSONL
may contain mixed `Stage15Action.result` shapes after rollback; readers must
already tolerate dict extras because `voyager/bots/clearance/models.py` defines
`Stage15Action.result` as an unconstrained `dict`; rollback must not require
state-file rewrites.

Post-rollback verification:

1. Run the successful writeback regression tests named in `## Testing / Verification`.
2. Confirm Clearance panel rendering works without `writeback_failures` keys.
3. Verify `/e2e/recent_writebacks` still returns the pre-CHG expected shapes
   after rollback or omits stale additive failure fields safely.
4. Check recent Wukong logs for reader failures around optional
   `writeback_failure_*` keys.

## Surfaces

| # | Surface | Change |
|---|---------|--------|
| 1 | `voyager/core/github_app.py` | Add `GitHubGraphQLError` and raise it from `GitHubAppClient.graphql()` for GraphQL `data.errors`. |
| 2 | `voyager/bots/clearance/pipeline.py` | Convert Stage 1.5 `resolveReviewThread` API failures into structured automation failure metadata instead of an opaque exception path. |
| 3 | `voyager/bots/clearance/enrichment.py` | Add an explicit warning slot in `build_clearance_comment()` and a helper that formats `automation.writeback_failures` into compact operator text. Stage 1.5 failures and generic writeback failures use the same canonical key name: `writeback_failures`. |
| 4 | `voyager/core/writeback.py` | Add sanitized writeback failure capture for generic label/reaction/comment operations and include failure metadata in returned writeback results/logs. |
| 5 | Tests | Update `tests/bdd/features/swm_pipeline.feature`, `tests/bdd/step_defs/test_swm_pipeline_steps.py`, `tests/clearance/test_comment_renderer.py`, `tests/clearance/test_review_request.py`, `tests/bdd/features/writeback.feature`, and `tests/bdd/step_defs/test_writeback_steps.py`. |

## Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Use one canonical key, `writeback_failures`, for both Stage 1.5 and generic route writeback failures. | The readiness panel should not care which lower-level write path produced the failure. |
| D2 | Stage 1.5 write failures set automation `status=Status.ERROR.value` while still preserving per-thread verdict evidence. | Existing `apply_swm_overlay()` already handles `"error"`; this avoids a new status value and avoids hiding failed intended writes behind a ready verdict. |
| D3 | Shared helper lives in `voyager/core/writeback.py` as `build_writeback_failure(...)`; compact line formatting lives beside it as `format_writeback_failure_warning(...)`. | Both Stage 1.5 and generic writeback need the same schema and warning text without duplicating sanitization rules. |
| D4 | Add and catch a typed `GitHubGraphQLError` at GitHub write boundaries and convert it to visible metadata with `error_class: "GraphQLError"`. | A typed exception is the durable contract for GraphQL `data.errors` and benefits all GraphQL callers. |

## Failure Metadata Schema

Each captured failure is a plain dict:

```text
{
  "operation": "resolveReviewThread" | "addLabels" | "removeLabel" |
               "addReaction" | "removeReaction" | "upsertComment" |
               "createComment",
  "error_class": "<exception class name>",
  "status": <HTTP status int or null>,
  "repo": "<owner/name>",
  "pr": <pull request number or null>,
  "issue": <issue number or null>,
  "thread_id": "<GraphQL thread id or null>",
  "suggested_action": "<one sentence>",
}
```

Rules:

- Do not include raw exception strings in GitHub-visible comments.
- `status` comes from `exc.response.status_code` for
  `httpx.HTTPStatusError`; otherwise `null`.
- Non-status `httpx.HTTPError` subclasses such as `ConnectError` or `ReadError`
  map to their concrete exception class name with `status: null`.
- `TimeoutError` maps to `error_class: "TimeoutError"` and `status: null`.
- `GitHubGraphQLError` from `GitHubAppClient.graphql()` maps to
  `error_class: "GraphQLError"` and `status: null` unless a status is available.
- The suggested action should be deterministic and keyed by failure family:
  - HTTP 401/403/404, `GraphQLError`, and other permission/API-denial shapes:
    `Verify the GitHub App permissions, repository installation, and installation access for this operation.`
  - HTTP 429 and rate-limit GraphQL responses: `Check GitHub API rate-limit status and retry after the limit resets.`
  - `TimeoutError`, `httpx.TimeoutException`, `ConnectError`, `ReadError`, and
    other transient transport failures: `Check GitHub API reachability and retry the operation after service health recovers.`
  - Unknown classes: `Review the structured writeback failure fields and retry after correcting the operation target.`
- For thread operations, `pr` is populated and `issue` is null. For generic
  issue operations, `issue` is populated and `pr` is null.
- `writeback_failures` is the canonical list name everywhere. Stage 1.5
  failures are collected from failed `Stage15Action.result` values into
  `automation.writeback_failures`; generic route writeback failures are returned
  from `apply_route_writeback()` under the same key.

Automation-level aggregates are emitted only when one or more failures are
captured. Successful zero-failure automation and generic writeback results must
omit `writeback_failures`, `writeback_failure_count`, and
`writeback_failure_reason` to preserve strict existing output shapes.
The Stage 1.5 aggregator may return an internal empty list for successful
actions, but the caller must only update the returned automation dict when that
list is non-empty.

- `writeback_failure_count`: count of captured failures.
- `writeback_failures`: list of failure dicts.
- `writeback_failure_reason`: first failure summary in singular/plural form:
  - One failure: `"1 writeback operation failed; first: <operation> (<error_class>[, HTTP <status>])"`
  - Multiple failures when `N > 1`: `"<N> writeback operations failed; first: <operation> (<error_class>[, HTTP <status>])"`
  `compute_clearance_automation()` computes Stage 1.5 aggregates by iterating
  `Stage15Action.result` entries after `_maybe_sync_stage_15()` returns.

Stage 1.5 status rule:

- If `resolveReviewThread` was an intended write and any Stage 1.5 write failed,
  `compute_clearance_automation()` returns `status=Status.ERROR.value` with the
  aggregate failure fields above. This preserves existing
  `apply_swm_overlay()` `"error"` handling and prevents a verdict-ready panel
  from hiding an automation write failure.

Helper contract:

```text
build_writeback_failure(
    *,
    operation: str,
    exc: BaseException,
    repository: str,
    pr: int | None = None,
    issue: int | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]

format_writeback_failure_warning(failure: dict[str, Any]) -> str
```

`format_writeback_failure_warning()` returns:

```text
Thread operation:
⚠️ Automation writeback: <operation> failed (<error_class>[, HTTP <status>]) on <repo>#<pr> thread <thread_id>. <suggested_action>

Generic issue operation:
⚠️ Automation writeback: <operation> failed (<error_class>[, HTTP <status>]) on <repo>#<issue>. <suggested_action>

Missing target fields:
⚠️ Automation writeback: <operation> failed (<error_class>[, HTTP <status>]) on <repo> unknown target. <suggested_action>
```

The helper never includes `str(exc)` in GitHub-visible text. Logs may include
the same structured fields plus sanitized context, but not raw exception strings
or request URLs.

## Sanitization Rules

Implement the helper in `voyager/core/writeback.py` so both generic writeback
and Clearance Stage 1.5 can use the same shape. The helper must redact or avoid:

- `Authorization` header values and bearer tokens.
- GitHub token prefixes such as `ghp_`, `gho_`, `github_pat_`.
- `token=...`, `access_token=...`, and similar query parameter values.
- Raw private-key paths or private-key material.
- Full request URLs when they contain query strings or credentials.

Primary enforcement is omission: do not interpolate `str(exc)`, request URLs,
headers, or exception messages into GitHub-visible comments, returned
automation reason fields, debug records, or structured log fields. Redaction is
only a defensive fallback for caller-provided context that is already expected
to be non-secret.

## Acceptance Criteria

- A1: `resolveReviewThread` `HTTPStatusError`/GraphQL permission failures are
  captured as structured metadata with operation, sanitized error class/status,
  repository, PR, and thread id. Catch `httpx.HTTPError` with status-aware
  handling for `httpx.HTTPStatusError`, plus `GitHubGraphQLError` from
  `GitHubAppClient.graphql()`.
- A2: The Clearance readiness panel includes a compact warning when automation
  failed due to GitHub permission/API errors.
- A3: Diagnostics never include tokens, private keys, Authorization headers, or
  raw secret-bearing URLs.
- A4: Existing successful label/comment/reaction/review-thread writeback
  behavior remains unchanged.
- A5: Tests cover `resolveReviewThread` forbidden handling, generic writeback
  failure handling, panel rendering, and sanitized logs/results.
- A6: Failed `resolveReviewThread` does not mutate `Thread.github_isResolved`,
  `ThreadSnapshot.github_state`, or post the in-thread close-reason reply.
- A7: Existing and older JSONL `Stage15Action.result` dict shapes remain
  readable; new failure fields are additive.
- A8: When no writeback failures occur, the Clearance panel renders with no
  warning line, `writeback_failure_*`/`writeback_failures` keys are omitted
  from returned automation and generic writeback dicts, and successful behavior
  remains byte-for-byte compatible where current tests assert exact snippets or
  strict dict shapes.
- A9: Warning insertion is safe when `comment_body` is `None` or empty; no
  failure-warning insertion path raises because the body is absent.
- A10: `format_writeback_failure_warning()` handles malformed failure dicts
  defensively: if both `pr` and `issue` are absent, it renders `unknown target`
  instead of raising or selecting the wrong format.
- A11: Existing fallback error paths touched by this CHG, including
  `compute_clearance_automation()` fetch failures and
  `dispatch_route_writeback()` fallback reasons, do not return or log raw
  `str(exc)` after implementation.

## Known Current-State Remediation

The following current-state behaviors are explicit implementation targets, not
accepted behavior:

| Current file/path | Current behavior | Required remediation |
|-------------------|------------------|----------------------|
| `voyager/core/github_app.py::GitHubAppClient.graphql()` | Raises a bare `RuntimeError` containing the GraphQL `data.errors` payload. | Step 2 adds `GitHubGraphQLError(Exception)` and raises it with sanitized display behavior for `data.errors`. |
| `voyager/bots/clearance/pipeline.py::_maybe_sync_stage_15()` | Lets `resolve_review_thread()` exceptions propagate and abort automation. | Step 4 catches writeback exceptions and records structured metadata without mutating GitHub state. |
| `tests/bdd/features/swm_pipeline.feature` scenario `resolveReviewThread mutation failure suppresses the in-thread reply` | Asserts `Then the pipeline raised an exception`. | Step 3 replaces that assertion with automation metadata assertions in the same reviewed slice as Step 4. No commit, push, PR, or CI-visible handoff may occur with only the RED rewrite. |
| `voyager/bots/clearance/pipeline.py::compute_clearance_automation()` fetch failure | Builds a `reason` by interpolating `{exc}`. | Step 4 replaces the reason with class-name/status-only sanitized fields and no exception message. |
| `voyager/core/writeback.py::dispatch_route_writeback()` compute fallback | Builds an automation `reason` by interpolating `{exc}`. | Step 8 replaces the reason with class-name/status-only sanitized fields and no exception message. |
| `voyager/core/writeback.py::dispatch_route_writeback()` stale-guard REST failure | Logs `error: str(exc)`. | Step 8 replaces the log field with sanitized exception fields such as `error_class` and optional HTTP `status`, never the message. |
| `voyager/core/writeback.py::dispatch_route_writeback()` enrichment fallback | Builds an automation `reason` by interpolating `{exc}`. | Step 8 replaces the reason with class-name/status-only sanitized fields and no exception message. |

Fallback error paths that are not direct GitHub writeback operations may use a
small internal sanitizer instead of `build_writeback_failure(...)`, but the
resulting fields must be limited to deterministic class/status context. They
must not include `str(exc)`, request URLs, headers, token-bearing messages, or
private-key paths.

Expected fallback shape:

```text
_safe_exception_fields(exc) -> {"error_class": "<class>", "status": <int|null>}

reason = "pipeline failed: <error_class>"
reason = "clearance enrichment failed: <error_class>"
structured log fields = {"error_class": "<class>", "status": <int|null>}
```

No fallback reason or log field may append the exception message after the class
name.

## Implementation Order

1. RED: add helper-focused tests in `tests/bdd/features/writeback.feature` and
   `tests/bdd/step_defs/test_writeback_steps.py` for non-status
   `httpx.HTTPError`, `TimeoutError`, `GitHubGraphQLError`, and token-bearing
   message redaction. Add a direct unit/BDD assertion that
   `GitHubAppClient.graphql()` raises `GitHubGraphQLError` when a GraphQL
   response contains `errors`. Name the test
   `test_graphql_errors_raise_typed_exception`.
2. GREEN: in `voyager/core/writeback.py`, add the reusable helper that converts
   exceptions into the failure metadata schema and logs a structured
   `writeback_failure` event without raw secrets. In
   `voyager/core/github_app.py`, add `class GitHubGraphQLError(Exception)` and
   raise it instead of the current generic exception for GraphQL `data.errors`.
   Its string/display behavior must be sanitized; callers should use typed
   fields rather than raw payload text.
3. RED: rewrite the existing BDD scenario
   `resolveReviewThread mutation failure suppresses the in-thread reply` in
   `tests/bdd/features/swm_pipeline.feature` so it expects automation error
   metadata instead of `Then the pipeline raised an exception`; update
   `tests/bdd/step_defs/test_swm_pipeline_steps.py` to assert no in-thread
   reply and no GitHub state mutation. Steps 3 and 4 are a single reviewed
   implementation slice and must be committed together. The orchestrator must
   not commit, push, open a PR, or trigger CI-visible handoff between the RED
   scenario rewrite and the matching GREEN implementation. This step is the
   explicit remediation of the current exception-raising assertion; the current
   feature-file text must not be treated as accepted behavior after this CHG
   starts implementation. The intended behavior change is exception capture
   into metadata; reply suppression and no state mutation are existing safety
   properties that must be preserved. Extend
   `_StubGitHubAppClient` with distinct failure modes:
   `fail_resolve_http_error`, `fail_resolve_status_error`,
   `fail_resolve_graphql_error`, and `fail_resolve_timeout`. The rewritten
   scenario must assert `Stage15Action.result.applied == false`, operation,
   error class/status, repo, PR, thread id, `writeback_failure_count`, and
   `writeback_failure_reason`. Add step definitions named
   `then_automation_has_writeback_failure`, `then_stage15_action_failed`, and
   `then_thread_github_state_was_not_mutated`.
   Mechanical preflight before any commit, push, PR, or CI-visible handoff:
   inspect `git diff --cached --name-only` and fail the handoff if
   `tests/bdd/features/swm_pipeline.feature` or
   `tests/bdd/step_defs/test_swm_pipeline_steps.py` is staged without
   `voyager/bots/clearance/pipeline.py` in the same staged set. If work is
   abandoned after only the RED rewrite, the orchestrator must either complete
   Step 4 before handoff or restore the RED-only working-tree changes; no
   RED-only staged or committed state is allowed.
4. GREEN: in `voyager/bots/clearance/pipeline.py`, wrap
   `client.resolve_review_thread()` in `_maybe_sync_stage_15()` for
   `httpx.HTTPError`, `GitHubGraphQLError`, and builtin
   `TimeoutError`. Because `httpx.HTTPStatusError` and `httpx.TimeoutException`
   are both `httpx.HTTPError` subclasses, the helper must branch with
   `isinstance(exc, httpx.HTTPStatusError)` to populate `status`; non-status
   HTTP errors keep `status: null`. On failure, append a
   `Stage15Action` whose `result` carries `applied: false` plus failure
   metadata; keep `snap.github_state`, `thread.github_isResolved`, and the
   in-thread reply unchanged. On success, preserve the current state mutation
   and reply-posting behavior. In `compute_clearance_automation()`, add a small
   private aggregator (for example `_stage15_writeback_failures(sync_actions)`)
   that collects failed `Stage15Action.result` entries into
   `writeback_failures`, `writeback_failure_count`, and
   `writeback_failure_reason` before building the returned automation dict; if
   any intended Stage 1.5 write failed, return `status=Status.ERROR.value`.
   "Intended write" means the current loop reached the existing mutation branch:
   verdict is `RESOLVED`, a `ThreadSnapshot.github_state` exists, and
   `snap.github_state.isResolved` is false. Also replace the existing
   `compute_clearance_automation()` fetch-failure `reason` that interpolates
   `{exc}` with a sanitized class-name/status-only reason and structured fields;
   this remediation is required before Step 4 is complete. If
   `snap.github_state.isResolved` is already true, treat the thread as an
   existing no-op skip, not as an intended write failure.
5. RED: add comment-rendering tests in
   `tests/clearance/test_comment_renderer.py` for the compact warning line and
   in `tests/clearance/test_review_request.py` for the end-to-end enrichment
   panel path. Include a defensive-format test for a malformed failure dict
   with neither `pr` nor `issue`.
6. GREEN: in `voyager/bots/clearance/enrichment.py`, add the warning slot to
   `build_clearance_comment()`. Use a separate line appended to the `lines`
   list immediately after `_automation_status_line(...)` and before the
   optional author-only warning. The line is produced by
   `format_writeback_failure_warning(...)`. Concrete insertion shape:

   ```text
   writeback_warning = None
   failures = (automation or {}).get("writeback_failures") or []
   if failures:
       writeback_warning = format_writeback_failure_warning(failures[0])

   lines = [..., _automation_status_line(automation)]
   if writeback_warning:
       lines.append(writeback_warning)
   if warning:
       lines.append(warning)
   ```

   This preserves the existing `_author_only_deadlock_warning()` behavior and
   leaves the details block insertion unchanged.
7. RED: add generic writeback failure tests in
   `tests/bdd/features/writeback.feature` and
   `tests/bdd/step_defs/test_writeback_steps.py` for label/reaction failures
   that still allow comment upsert, plus comment-upsert failure that returns
   metadata but cannot update the panel. Add Gherkin scenarios named:
   `Label failure is captured and warning is inserted before comment upsert`,
   `Reaction failure is captured and warning is inserted before comment upsert`,
   `Comment upsert failure returns metadata without panel update`, and
   `Missing comment body skips warning insertion without raising`.
8. GREEN: in `voyager/core/writeback.py`, wrap label/reaction/comment
   writeback calls so safe partial failures are captured and returned as
   `writeback_failures` only when at least one failure occurs. If
   label/reaction failure occurs before comment upsert
   and a comment body is available, insert the compact warning after the marker
   line when the body begins with an HTML marker comment. For Clearance comments
   this marker is `CLEARANCE_COMMENT_MARKER` (`<!-- iterwheel:clearance-readiness -->`);
   for generic comments, treat any first line matching `<!-- ... -->` as the
   marker line. Otherwise prepend the warning above the existing body. This
   insertion logic is owned by `writeback.py` and operates on the final
   `writeback["comment_body"]` immediately before create/upsert; it does not
   feed back into `build_clearance_comment()`. If the comment write itself
   fails, return the failure metadata and rely on structured logs/debug records
   because the panel cannot update itself. In the same step, remediate existing
   `dispatch_route_writeback()` fallback paths that currently interpolate
   `{exc}` or `str(exc)`: compute fallback reason, stale-guard REST failure log
   field, and enrichment fallback reason. These replacements must use
   class-name/status-only sanitized fields and no exception message. Concrete
   fallback examples:

   ```text
   automation["reason"] = "pipeline failed: <error_class>"
   return {"applied": False, "reason": "clearance enrichment failed: <error_class>", ...}
   stale_guard_log = {"error_class": "<error_class>", "status": <int|null>, ...}
   ```
9. VERIFY: confirm `voyager/bots/clearance/overlay.py` requires no edit because
   the implementation keeps `Status.ERROR.value` for Stage 1.5 failure metadata,
   which the existing overlay already handles. Add or keep an automated
   assertion that an automation payload with `status: "error"` and
   `writeback_failures` still surfaces as a blocked Clearance panel state.
10. Add or update automated assertions for secret-safe fallback paths before
    the manual audit. These assertions must cover token-bearing exception
    messages in `compute_clearance_automation()` fetch failure handling and in
    `dispatch_route_writeback()` fallback reason paths, including both compute
    and enrichment exceptions. Then audit changed surfaces for accidental
    `str(exc)` leakage into comments, returned reason fields, debug records, and
    structured log fields, and verify downstream readers use `.get()` for
    optional `writeback_failure_*` automation keys. Run:
    `rg -n 'str\(exc\)|\bexc\b' voyager/core/github_app.py voyager/core/writeback.py voyager/bots/clearance/pipeline.py voyager/bots/clearance/enrichment.py`
    and inspect every hit. Replace raw exception interpolation in changed
    writeback paths and existing fallback paths touched by this CHG with
    structured/sanitized failure fields. Existing `github_app.py` catch/log
    paths outside GraphQL writeback, such as `branch_protected()` fail-safe
    logging, must be inspected and either kept explicitly out of scope with a
    token-safety rationale or remediated if they can surface secret-bearing
    exception text.
11. Update `CHANGELOG.md`, run `ruff`, targeted tests, and
    `af validate --root .`.

## Review Gates

- Plan review: COR-1602/COR-1609 with `[glm, deepseek, minimax]`, all viable
  reviewers `PASS` with weighted score >= 9.0 and no blockers.
- MVE implementation review: after steps 1-4 land together, run targeted tests
  and a focused Trinity review of `voyager/core/writeback.py`,
  `voyager/bots/clearance/pipeline.py`, and the changed BDD test files.
- Final implementation review: after step 10, run the full configured code
  review panel before PR handoff.
- Commit preflight: before any commit/push/PR handoff, verify the Step 3/4
  same-slice guard by checking staged paths; a RED-only BDD rewrite is a failed
  handoff and must be completed or restored first.
- Iteration cap follows VOY-1811: soft cap R10, hard-stop evaluation at R13.

## Execution Log

| Step | Status | Notes |
|------|--------|-------|
| 1-2 | Completed | Added typed `GitHubGraphQLError`, writeback failure helpers, and helper/unit coverage. |
| 3-4 | Completed | Replaced current exception-raising BDD assertion and implemented Stage 1.5 metadata capture in the same reviewed slice. |
| 5-6 | Completed | Added Clearance panel warning rendering and renderer/enrichment coverage. |
| 7-8 | Completed | Added generic writeback failure capture plus dispatch fallback raw-exception remediation. |
| 9 | Completed | Verified existing overlay error handling with targeted coverage. |
| 10 | Completed | Added secret-safe fallback assertions and ran `rg` audit; remaining `str(exc)` hits are pre-existing investigator diagnostics or safe class/status-only references. |
| 11 | Completed | Updated changelog; `ruff`, `ruff format --check`, `git diff --check`, full pytest (`799 passed`), and docs validation are clean. |

## Approval

- [x] Plan review passed (`glm`, `deepseek`, `minimax` all >= 9.0, no blockers).
- [x] Implementation review passed.
- [x] Final PR review loop clear.

## Changelog Draft

```markdown
- Surface Clearance GitHub writeback failures in PR readiness panels with
  sanitized operation/error metadata, including `resolveReviewThread`
  permission/API failures. The implementation adds typed GraphQL error
  handling plus structured Stage 1.5 and generic writeback failure capture.
```

## Testing / Verification

| Scenario | Command / coverage | Expected |
|----------|--------------------|----------|
| Stage 1.5 forbidden | `uv run pytest tests/bdd/step_defs/test_swm_pipeline_steps.py -q` | Automation returns failure metadata; no reply is posted. |
| Panel warning | `uv run pytest tests/clearance/test_comment_renderer.py tests/clearance/test_review_request.py -q` | Clearance marker comment contains compact warning and no secrets. |
| Generic writeback failure | `uv run pytest tests/bdd/step_defs/test_writeback_steps.py -q` | Failure metadata is returned and sanitized. |
| Existing happy paths | `uv run pytest tests/clearance/test_comment_renderer.py tests/clearance/test_review_request.py tests/bdd/step_defs/test_swm_pipeline_steps.py tests/bdd/step_defs/test_writeback_steps.py -q` | Current successful behavior remains green. |
| Old JSONL compatibility | Existing state-file parse regression or fixture covering old `Stage15Action.result` shapes | Old dict shapes parse without error and old keys are preserved. |
| Overlay error status | Targeted overlay assertion for `automation.status == "error"` with `writeback_failures` | Clearance panel remains blocked/error-visible without overlay edits. |
| Secret-safe fallback audit | Targeted assertions plus `rg -n 'str\(exc\)|\bexc\b' voyager/core/github_app.py voyager/core/writeback.py voyager/bots/clearance/pipeline.py voyager/bots/clearance/enrichment.py` | Token-bearing exception messages are absent from comments, return reasons, debug records, and structured logs; out-of-scope `github_app.py` hits have documented token-safety rationale. |
| Style/docs | `uv run ruff check ...` and `af validate --root .` | Clean. |

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-05-17 | Initial plan for issue #45. | Codex |
| 2026-05-17 | Strict plan gate held after latest raw review because MiniMax scored below 9.0 despite a PASS label; revised same-slice enforcement, fallback secret-safety, pluralized failure reasons, and approval status. | Codex |
| 2026-05-18 | Plan approved at hard-stop review round R13: GLM 9.6 PASS, DeepSeek 9.125 PASS, MiniMax 9.8 PASS, no blockers. | Codex |
| 2026-05-18 | Implemented approved CHG with worker dispatch plus local cleanup; targeted suite passed (`162 passed`), unit suite passed (`146 passed`), full pytest passed (`799 passed`), lint/format/diff/docs clean. | Codex |
| 2026-05-18 | Final implementation review passed: GLM 9.4 PASS, DeepSeek 9.25 PASS, MiniMax 9.12 PASS; MiniMax syntax concern was verified false with `ast.parse`. | Codex |
