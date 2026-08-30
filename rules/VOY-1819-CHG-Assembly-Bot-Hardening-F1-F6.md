# CHG-1819: Assembly Bot Hardening — F1-F6 Closure

**Applies to:** VOY project
**Last updated:** 2026-08-30
**Last reviewed:** 2026-08-30
**Status:** Completed
**Date:** 2026-05-23
**Scheduled:** After CHG plan-review approval in the active VOY-1811 loop for #73.
**Requested by:** Voyager maintainers (via #73, surfaced in VOY-1811 Phase 6 review of #69)
**Priority:** P2
**Change Type:** Normal
**Targets:** `voyager/bots/assembly/writeback.py`, `voyager/bots/assembly/job_contract.py`, `voyager/bots/assembly/adapters.py`, `rules/VOY-1817-CHG-Assembly-Bot-MVP-Implementation.md`, `tests/unit/`
**Closes:** #73
**Related:** VOY-1817 CHG (Assembly MVP), VOY-1818 CHG (Actor authorization gate — supersedes F1), VOY-1811 REF (Multi-Agent Loop §Gate Corner Table)

---

## What

Close the six follow-ups (F1-F6) surfaced during VOY-1811 Phase 6 review of #69
(CHG-1817) before Assembly is enabled on a production allow-list. The six
findings break down by disposition:

| ID | Disposition | Scope |
|----|-------------|-------|
| F1 (P2 security) | **Close, already addressed** | Sender allow-list — superseded by VOY-1818 actor authorization gate (merged in #77). Documented closure with a per-finding rationale block; no code change. |
| F2 (P2 cleanup) | **Remove dead lookup** | `command_flags.get("backend")` in `writeback.py:158` is dead — `parse_assembly_command` never emits a `backend` key. Remove the lookup and let `select_execution_adapter(None)` consult the env (existing behavior). Wiring `--backend` as a real command flag is deferred — it is a feature, not a cleanup, and would expand the public command surface. |
| F3 (P2 idempotency) | **In-process asyncio lock** | Add a per-`(repository, branch_name)` `asyncio.Lock` held across the branch → PR → codex sequence in `dispatch_assembly_writeback`. Serializes duplicate webhook deliveries within the bridge process. Cross-process dedup (e.g., delivery-id table) is deferred — the bridge is single-process today per VOY-1814. |
| F4 (P3 edge) | **Trivial bugfix** | `_extract_acceptance_criteria` returns `[""]` when both body section and title are empty. Return `[]` instead. Blueprint already blocks empty-title issues, so this is defensive only. |
| F5 (P3 docs) | **Docstring update** | `ExecutionAdapter` Protocol docstring is silent on the SHA contract. Add: "Adapters MUST push commits to the source repository before returning `commit_shas`; the dispatcher passes `commit_shas[-1]` to `create_branch_ref` and assumes the SHA exists on the remote." No behavior change. |
| F6 (P3 docs) | **CHG-1817 amendment** | `REFUSAL_ISSUE_CLOSED = "issue_closed"` was added during implementation and not in CHG-1817's Writeback Result Schema enum. Amend CHG-1817 §Writeback Result Schema to include it, with a Change History row. No code change. |

All six are landed in **one PR with one atomic commit per finding** (the issue's
acceptance criterion allows "rolled into another PR" with a documented
rationale; F2-F4 share file context so reviewers see them together; F1/F6 are
docs-only). This avoids running six rounds of Phase 4 plan-review + Phase 8
Codex review on near-trivial changes.

**Surface arithmetic.** The 6 findings break into 10 §Surfaces rows: 4 code
edits (Surfaces 1-4, covering F2/F3/F4/F5), 1 doc-CHG amendment (Surface 5
covering F6), 1 PR-description entry (Surface 6 covering F1 closure), and 4
test surfaces (Surfaces 7-10 covering F2/F3/F4 unit + xtest tests). F5 and F6
need no test code (Surface 10 is the docs-only verification entry).

**Stale "871 test count" in issue #73.** The issue body's acceptance criterion
cites "no regression in the existing 871 Assembly test pass count". That count
predates VOY-1818 (#77) and is now stale — the current full-suite count is
**1127 passed** (281 assembly-scoped). This CHG honors the spirit of the AC
("no regression in the assembly suite") by requiring `pytest tests/` to remain
GREEN at the post-1818 baseline; §Testing makes this explicit. The PR
description will note the corrected baseline.

## Why

CHG-1817 shipped Assembly with empty-allow-list + DryRunAdapter defaults, so
none of F1-F6 is exploitable today. But the issue's acceptance criterion is
explicit: all six must close before Assembly is enabled on a production
allow-list (beyond `iterwheel/voyager-sandbox`). VOY-1818 closed F1 in spirit
by adding an upstream actor gate; documenting that closure here makes the
relationship explicit so a future operator reading #73 sees why no F1 code
change shipped.

The remaining five are cleanup, one idempotency hardening, two docstrings,
and one CHG amendment — together they consume the "P2/P3 hardening" debt
recorded against CHG-1817 without expanding scope.

## Out of Scope

- Wiring `--backend pi-oh-my-pi-deepseek` as a real command-line flag (F2's
  alternative). The command surface is already public; adding a new flag is
  feature work, not hardening. Defer to a follow-up issue if needed.
- Cross-process delivery-id dedup (F3's alternative). The bridge is
  single-process per VOY-1814 (Wukong Bridge launchd); the in-process lock
  covers the realistic race. Cross-process dedup would require a SQLite or
  Redis backend and a new SOP for cleanup; deferred.
- Wiring the real `pi -> oh-my-pi -> DeepSeek V4 Pro` backend (per VOY-1817
  Out of Scope).
- Expanding the bridge production allow-list — that is the explicit operator
  step gated by this CHG's closure.
- Lock-dict TTL eviction / `weakref.WeakValueDictionary` migration for F3 —
  flagged in D6 as the migration trigger if Voyager later processes
  ≥10000 issues/year. The current ~50 issues/year × 64 bytes ceiling does not
  warrant the added complexity.

## Impact Analysis

### Systems affected

- Writeback dispatcher (`voyager/bots/assembly/writeback.py`): F2 line removal;
  F3 lock acquisition.
- Job contract builder (`voyager/bots/assembly/job_contract.py`): F4 fallback
  fix.
- Adapter Protocol docstring (`voyager/bots/assembly/adapters.py`): F5.
- CHG-1817 (`rules/VOY-1817-CHG-Assembly-Bot-MVP-Implementation.md`): F6.

### Channels affected

- No external surface changes. F3 lock is internal; F4 affects the contract
  payload's `acceptance_criteria` list shape (`[]` vs `[""]`), but no consumer
  reads `acceptance_criteria` to determine writeback action — only to render
  the progress comment.

### Downtime required

None. F3's lock dict starts empty and refills on demand; no migration. Bridge
restart picks up the new behavior atomically.

### External dependencies

None. No new packages, no new GitHub App permissions.

### Rollback plan

Revert the implementation commits per-finding (they are atomic). Each finding's
revert leaves the others intact. **Bridge restart required for F3 revert** —
the `_assembly_writeback_locks` module-level dict and the held `asyncio.Lock`
references live for the lifetime of the FastAPI process. A live revert without
restart would deploy code that no longer references the dict, but any
already-acquired locks in flight would still be held by completing tasks;
restart drains them cleanly. Per-finding rollback verification:

- **F2**: confirm `voyager/bots/assembly/writeback.py` no longer reads
  `command_flags["backend"]` — `git grep -n 'command_flags.get("backend")'`
  prints nothing.
- **F3**: confirm the module-level lock table is removed —
  `git grep -nE "_assembly_writeback_locks|_get_lock" voyager/bots/assembly/`
  prints nothing. **Then restart the bridge** (`launchctl kickstart -k
  gui/$UID/com.iterwheel.voyager.bridge` or equivalent per VOY-1814) to drain
  in-flight lock-holders.
- **F4**: confirm `_extract_acceptance_criteria(body="", title="")` returns
  `[]` (test assertion).
- **F5**: confirm the `ExecutionAdapter.execute` docstring no longer carries
  the SHA-contract sentence.
- **F6**: confirm CHG-1817 §Writeback Result Schema's refusal enum no longer
  lists `issue_closed`.

Bundle revert: a single revert commit reverts the merge commit; the same
bridge-restart requirement applies for F3.

## Surfaces

| # | Surface | Finding | Change |
|---|---------|---------|--------|
| 1 | `voyager/bots/assembly/writeback.py:158` | F2 | Remove `backend_env = command_flags.get("backend") or None` and the line passing it to `select_execution_adapter`. Call `select_execution_adapter()` (no argument — falls through to env). Replace the existing single-line comment with: `# Backend selection is env-only (`ASSEMBLY_EXECUTION_BACKEND`) per VOY-1817 D3. # `command_flags` carries `dry_run` / `allow_missing_stack` only; there is # no `--backend` command flag (closed by CHG-1819 F2; see VOY-1819).` Pre-existing grep for `command_flags["backend"]` across `voyager/bots/assembly/` returns no other consumers, so this is a complete dead-code removal. |
| 2 | `voyager/bots/assembly/writeback.py` (new module-level dict + `dispatch_assembly_writeback` wrap) | F3 | Add `_assembly_writeback_locks: dict[tuple[str, str], asyncio.Lock] = {}` at module scope. In `dispatch_assembly_writeback`, **after** the actor-gate / precondition / live-issue refetch but **before** `_ensure_branch`, acquire `_get_lock(repository, contract.branch_name)`. Hold the lock across `_ensure_branch` → `_ensure_pull_request` → `_post_codex_trigger` → `_upsert_progress_comments`. Release on exit (use `async with`). The lock dict grows monotonically with distinct `(repo, branch)` tuples; locks are tiny (~64 bytes each) and a single Voyager-shaped issue creates one lock that survives until bridge restart — acceptable. Document this growth bound in a code comment so reviewers do not flag it as a leak. |
| 3 | `voyager/bots/assembly/job_contract.py:135-141` | F4 | Change `return [(title or "").strip()], "title_fallback"` to: if `title` is non-empty (after strip), return `[title.strip()], "title_fallback"`; otherwise return `[], "empty_fallback"`. Add `empty_fallback` to a comment listing the three sources (`section`, `title_fallback`, `empty_fallback`). **Asymmetry note.** `_extract_task_summary` at `voyager/bots/assembly/job_contract.py:120-132` has the identical `(title or "").strip()` fallback returning `""` with source `"title_fallback"`. F4 deliberately does NOT touch `_extract_task_summary` because an empty string renders harmlessly in the progress comment (the line "Task summary: " disappears under normal markdown rendering), while `[""]` renders as a visible empty-bullet bug. The asymmetry is intentional and documented here; a future cleanup could unify both fallbacks under one helper, but is out of scope. |
| 4 | `voyager/bots/assembly/adapters.py:37-42` | F5 | Replace the one-line `ExecutionAdapter` docstring with a 4-line block: "Protocol every adapter must satisfy. Adapters MUST push commits to the source repository before returning `commit_shas`; the writeback dispatcher passes `commit_shas[-1]` to `create_branch_ref` and assumes the SHA already exists on the remote. Adapters that produce commits locally only (without pushing) will cause the branch-create step to fail with 422 'Object does not exist'." |
| 5 | `rules/VOY-1817-CHG-Assembly-Bot-MVP-Implementation.md` §Writeback Result Schema | F6 | In the refusal enum sub-block of §Writeback Result Schema, add `"issue_closed"` **positionally** between `"pr_not_issue"` and `"missing_blueprint_ready_label"` (matching the routing order: PR-shape check → closed-state check → label checks; not alphabetical). The §Refusal Enum block at the bottom of THIS CHG already shows the same positional ordering. Add a Change History row to VOY-1817 dated 2026-05-23: "CHG-1819 amendment: added `issue_closed` to the refusal enum list (implementation-added during VOY-1817 Phase 5)." |
| 6 | (PR description + issue comment) | F1 | One-paragraph closure rationale appears in BOTH the PR body and as a comment on #73 itself. The **comment on #73 MUST be posted before the PR merges** so that the closure rationale lands on the still-open issue (a `Closes #73` keyword on PR merge auto-closes #73, and a post-close comment looks like an afterthought). Concretely: in Phase 7 of the loop, immediately after `gh pr create` succeeds and before announcing handoff, post the F1 rationale comment via `gh issue comment 73 --repo iterwheel/voyager --body "..."`. The "rolled into another PR" rationale required by issue #73 AC bullet 4 is satisfied by the same comment. Body: "F1 was the request to gate `/assembly` on `payload.sender.login`. VOY-1818 (PR #77, merged 2026-05-23) introduced the upstream actor authorization gate that gates on `payload.comment.user.login` — for `issue_comment.created` GitHub guarantees `sender == comment.user`, so VOY-1818 is a strict superset of F1's check. The VOY-1818 gate adds: (a) bot exclusion, (b) trusted-association policy, (c) refusal-comment surface, (d) WARNING-log audit trail on `sender != comment.user` divergence. F1 is closed as superseded by VOY-1818. F2-F6 are bundled into PR #<N> per the issue's 'rolled into another PR' clause." No code change. |
| 7 | `tests/unit/test_assembly_writeback_dispatcher.py` | F3 | Add `test_concurrent_deliveries_are_serialized`. **Use `asyncio.Event` gating for determinism — not sleep + call-order list (which is flaky on slow CI).** Pattern: two `dispatch_assembly_writeback` tasks started via `asyncio.gather` for the same `(repo, branch)`. Mock `_ensure_branch` to (a) record the task name into a shared list, (b) set `entered_event` on entry, (c) `await released_event.wait()` before returning. Assertion: while `released_event` is unset, only ONE entry exists in the list (proves serialization). Release `released_event` and `await asyncio.gather(...)` to drain. Add `test_distinct_branches_are_parallel`: two tasks with different branch names; both should set their respective `entered_event` before either releases — assert both events fire before `gather` completes (proves no cross-branch serialization). **Expected RED-phase failure**: without the lock wired in Surface 2, `test_concurrent_deliveries_are_serialized` sees both tasks set `entered_event` before either is released → assertion `len(seen) == 1` fails with `AssertionError: expected 1, got 2`. |
| 8 | `tests/unit/test_assembly_commands.py` (extend) + `tests/unit/test_assembly_writeback_dispatcher.py` (extend) | F2 | **Two complementary tests** (replaces the round-1 synthetic test per DS-R2 advisory): (a) `tests/unit/test_assembly_commands.py::test_parse_command_never_emits_backend_key` — assert that every successful `parse_assembly_command` result's serialized `command_flags` dict contains EXACTLY the keys `{"dry_run", "allow_missing_stack"}` (test across all current flag combinations + a `--backend foo` input that must NOT produce a `backend` key). This is a structural regression gate against future parser changes. (b) `tests/unit/test_assembly_writeback_dispatcher.py::test_dispatcher_does_not_read_backend_from_command_flags` — `mypy`-style structural assertion: `assert "backend" not in inspect.getsource(dispatch_assembly_writeback)` (substring check on the function source). Together (a)+(b) gate both the upstream parser and the downstream consumer against the dead-lookup pattern re-appearing. |
| 9 | `tests/unit/test_assembly_job_contract.py` | F4 | Add `test_acceptance_criteria_empty_when_title_and_body_empty`: build a contract from `{"title": "", "body": ""}`; assert `acceptance_criteria == []` and `acceptance_criteria_source == "empty_fallback"`. Keep the existing `title_fallback` test for non-empty title. |
| 10 | (no new test file for F5/F6) | F5, F6 | Docs-only; no test required. The CHG amendment to VOY-1817 is verified by a `git grep -n "issue_closed" rules/VOY-1817*` check in the PR description. |

## Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **One PR, atomic commits per finding.** | The issue's AC explicitly allows "rolled into another PR" with a documented rationale. Six separate PRs would each consume Phase 4 plan-review + Phase 8 Codex + merge-watch overhead disproportionate to the size of the changes (F4/F5/F6 are one-line edits). Reviewers see each finding as its own commit, so the diff stays surgically reviewable. |
| D2 | **F1 closed without code change.** | VOY-1818 ships an upstream actor gate that subsumes F1. For `issue_comment.created` events GitHub sets `sender.login == comment.user.login`, so a check on either field is operationally identical. The VOY-1818 gate adds capabilities F1 did not specify (bot exclusion, association policy, refusal comment, sender-divergence WARNING) — closing F1 with the rationale prevents an operator from later thinking F1 was forgotten. |
| D3 | **F2 removes the dead lookup; does not wire `--backend` as a real flag.** | `parse_assembly_command` parses only `--dry-run` and `--allow-missing-stack`. Adding `--backend` is a feature: it expands the public command surface, requires a new flag-parsing test, and may interact unpredictably with the env-var precedence (env wins per VOY-1817 D3 → would `--backend` override env, or vice versa?). That decision belongs in a follow-up issue, not this hardening CHG. Removing the dead code is the unambiguous cleanup. |
| D4 | **F3 uses an in-process `asyncio.Lock` table, not a delivery-id dedup database.** | The bridge runs as a single FastAPI process under launchd (VOY-1814). The race the issue describes is two background tasks racing within that process. `asyncio.Lock` keyed by `(repo, branch)` is the minimum sufficient fix; a database-backed dedup table would add a new dependency for a contention pattern that has not been observed in production. If the bridge ever runs as multiple processes (load-balanced or multi-worker), a SQLite-backed delivery-id table is the right follow-up — flagged in §Out of Scope. |
| D5 | **F3 lock scope is `(repository, branch_name)`, not `(repository, issue_number, delivery_id)`.** | The branch is the unique shared resource — `create_branch_ref` fails with 422 if two callers race. The delivery_id is unique per webhook delivery, so locking on it would never block anything (each delivery has its own). The repo + branch tuple matches the GitHub-side resource that can race. **Title-edit corner**: branch names are derived from `(issue_number, slugified_title)` in `voyager/bots/assembly/branch.py`. If an operator edits the issue title between two `/assembly` invocations, the two invocations have different `branch_name` values and therefore different lock keys — they will proceed in parallel, each creating its own distinct branch. This is **intentional**: a renamed issue is operationally a different work unit, and the two branches are independent GitHub-side resources that cannot race on `create_branch_ref`. The lock's job is to prevent racing on the *same* GitHub resource, not to enforce per-issue uniqueness. |
| D6 | **F3 lock dict grows monotonically.** | A Voyager-shaped bot processes maybe 50 issues/year. 50 locks × 64 bytes = 3 KB. Bridge restart clears the dict. Adding TTL eviction would be premature; document the growth bound in a comment so reviewers see the explicit choice. If the bridge later processes ≥10000 issues/year, switch to `weakref.WeakValueDictionary` — flagged in §Out of Scope as a follow-up trigger. |
| D7 | **F4 returns `[]` and tags the source as `empty_fallback`** (not reusing `title_fallback`). | The downstream comment renderer iterates `acceptance_criteria` and bullets each entry. `[""]` renders as a blank bullet, which looks like a Blueprint failure. `[]` renders as no bullets, which is honest. The new `empty_fallback` source string lets the future audit log distinguish "title-derived" from "no-data" cases without re-deriving from the contract body. |
| D8 | **F4 keeps `title_fallback` as a separate branch** (does not collapse). | A contract built from an issue with a 12-word title but no Acceptance Criteria section is meaningfully different from one with neither — for Blueprint coaching and for the operator's understanding of how Assembly extracted the contract. Three distinct source labels (`section` / `title_fallback` / `empty_fallback`) preserve that signal. |
| D9 | **F5 docstring uses MUST (RFC 2119 sense).** | The Protocol is a contract; an adapter that returns un-pushed SHAs causes a runtime 422 from GitHub. "MUST" signals that this is not a recommendation but a precondition. Lower-case "must" would be ambiguous. |
| D10 | **F6 amends CHG-1817, not VOY-1818, and does not introduce a new SOP.** | The omission is in CHG-1817's §Writeback Result Schema; that is the doc that needs the fix. Amending a CHG with a Change History row is the documented pattern (see VOY-1817's own Round 1 / Round 2 / Phase 6 amendment rows). No new SOP because the policy is unchanged — only the documentation. **First post-merge cross-CHG amendment.** VOY-1817's prior Change History rows were all pre-merge within the same PR (#74). F6 is the first time CHG-1817 is amended after its merge by a *different* CHG (CHG-1819 / PR for #73). This sets a precedent: post-merge CHG amendments are acceptable when (a) the original CHG is the authoritative spec for an existing shipped behavior, (b) the amendment is purely documentary (no behavior change), and (c) the amending CHG records the cross-reference in its own §Change History. Future post-merge amendments should follow this pattern. |

## Refusal Enum (after F6)

```
"pr_not_issue"
"issue_closed"            # added per F6
"missing_blueprint_ready_label"
"missing_stack_type_label"
"repository_not_allowed"
"unauthorized_actor"      # from VOY-1818
```

Order is implementation-stable (constants in `voyager/bots/assembly/constants.py`)
and CHG-documented for cross-reference.

## Testing / Verification

**TDD cadence**: per-finding RED → GREEN → REFACTOR cycle. Each finding's test
is committed first (failing because the impl change has not landed) and the
matching impl commit makes it green. F5 and F6 are docs-only — verified by
`git grep` assertion in the PR body, not pytest.

**Env-var test isolation**: F2's regression test reads `ASSEMBLY_EXECUTION_BACKEND`;
use `monkeypatch.setenv` / `monkeypatch.delenv` per VOY-1818's §Testing convention.

Unit:

- `tests/unit/test_assembly_writeback_dispatcher.py` — Surfaces 7 (F3 lock) + 8 (F2 lookup removal).
- `tests/unit/test_assembly_job_contract.py` — Surface 9 (F4 empty-fallback).
- `tests/unit/test_assembly_adapters.py` — no new test for F5 (docstring); `mypy` already verifies the Protocol shape.

Cross-test:

- F3's concurrency test is also added to `tests/unit/test_assembly_xtest_dispatcher_corners.py` as `test_xtest_concurrent_deliveries_serialized` — independent author exercises only `dispatch_assembly_writeback` from the public package surface.

Doc-only verification:

- F5: `python -c "import voyager.bots.assembly.adapters as m; help(m.ExecutionAdapter.execute)"` shows the new docstring.
- F6: `git grep -n "issue_closed" rules/VOY-1817*` prints the new enum row.

Tooling:

- `uv run ruff check .`
- `uv run mypy voyager`
- `uv run pytest tests/`

The full suite must remain green; no existing test changes shape.

## Open Questions for Reviewers

1. **F3 lock granularity.** This CHG locks on `(repo, branch_name)`. Should
   it lock on `(repo, issue_number)` instead? Different `/assembly` invocations
   on the same issue would have the same branch name (deterministic from
   issue number + title) — so the two are equivalent in practice. Going with
   `(repo, branch_name)` because the branch is the GitHub-side resource that
   races. Confirm or override.
2. **F4 fallback name.** `empty_fallback` vs `no_data`. Current proposal:
   `empty_fallback` for symmetry with `title_fallback`. Reviewer override
   welcome.

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-05-23 | Initial CHG for issue #73 F1-F6 closure. | Claude (via VOY-1811 #73) |
| 2026-05-23 | Round 1 plan-review remediation (GLM 9.1 PASS, DeepSeek 9.0 PASS, MiniMax 8.8 FIX): §What — added surface arithmetic explanation and stale-871-test acknowledgement [MM nit 1/2]; Surface 1 — added exact replacement comment text + cross-package grep confirmation [DS]; Surface 3 — documented intentional `_extract_task_summary` asymmetry [DS]; Surface 5 — replaced "alphabetical" with "positionally" wording so the F6 enum order matches the §Refusal Enum block [MM nit 3]; Surface 6 — explicit "post comment on #73 BEFORE merge" ordering rule [MM nit 4]; Surface 7 — replaced sleep + call-order list with `asyncio.Event`-based gating + specified expected RED-phase failure [GLM P1]; D5 — added title-edit corner explicitly [GLM P2]; D10 — added "first post-merge cross-CHG amendment" precedent [DS]; §Rollback plan — explicit F3 bridge-restart callout [DS]. | Claude (via VOY-1811 #73) |
| 2026-05-23 | Round 2 plan-review pre-merge cleanup (GLM 9.5 PASS, DeepSeek 9.8 PASS, MiniMax 10.0 PASS — all clear; DeepSeek surfaced two optional advisories folded in here): §Out of Scope — added the `weakref.WeakValueDictionary` migration trigger row (the D6 forward-reference now matches a real §Out of Scope row) [DS-R2 advisory 1]; Surface 8 — replaced the synthetic `{"backend": "..."}` regression test with two structural tests: (a) parser-level "never emits backend key" gate in `test_assembly_commands.py`, (b) dispatcher-level source-inspection assertion in `test_assembly_writeback_dispatcher.py` [DS-R2 advisory 2]. | Claude (via VOY-1811 #73) |
| 2026-08-30 | Lifecycle closeout: implementation PR #79 merged as `0eea218c`; source issue #73 is closed. Status changed from Proposed to Completed. | Codex |
