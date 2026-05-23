# CHG-1821: Assembly Execution Backend Fake Subprocess and Pi Canary

**Applies to:** Voyager Assembly bot
**Last updated:** 2026-05-23
**Last reviewed:** 2026-05-23
**Status:** Proposed
**Date:** 2026-05-23
**Requested by:** Frank Xu
**Priority:** Medium
**Change Type:** Normal
**Scheduled:** After plan-review approval in the active VOY-1811 loop for #82.
**Related:** #82, VOY-1805, VOY-1806, VOY-1807, VOY-1814, VOY-1816, VOY-1817, VOY-1818, VOY-1819, VOY-1820

---

## What

Wire the next Assembly execution seam in two controlled stages:

1. Land a deterministic fake subprocess backend that proves the non-dry-run Assembly branch, pull-request, Codex-trigger, and progress-comment path without invoking real OMP.
2. After that implementation merges and deploys, run the first real Oh My Pi / OMP (`omp -p`) canary only on `iterwheel/voyager-sandbox`.

This CHG does not enable real OMP execution for `iterwheel/voyager`, `frankyxhl/alfred`, or `frankyxhl/trinity`.

---

## Why

Assembly currently routes `/assembly`, validates Blueprint/Stack/actor gates, and records a dry-run job contract. The fake subprocess phase proved the dispatcher mutation path; the real backend is the follow-up OMP subprocess path selected by `ASSEMBLY_EXECUTION_BACKEND=pi-oh-my-pi-deepseek`.

Before connecting a coding agent that can modify repositories, Voyager needs a deterministic backend that exercises the GitHub mutation path end to end under tests. That gives us confidence in the contract, token boundary, idempotency, failure comments, and PR creation behavior before adding real OMP subprocess behavior.

---

## Impact Analysis

- **Systems affected:** `voyager/bots/assembly/`, `voyager/core/writeback.py`, Assembly BDD/unit tests, `config.example.toml`, Wukong deployment configuration during sandbox canary.
- **Repositories affected during implementation:** `iterwheel/voyager` only.
- **Repositories affected during real canary:** `iterwheel/voyager-sandbox` only.
- **GitHub App involved:** `iterwheel-assembly`.
- **Downtime required:** No.
- **Security impact:** Medium. The implementation introduces a path that hands a short-lived GitHub App installation token to an execution backend. The token must never be logged, rendered in comments, persisted in job-contract files, or passed to model prompts.
- **Rollback plan:** Keep `ASSEMBLY_EXECUTION_BACKEND=dry-run` in production. For sandbox canary rollback, remove `iterwheel/voyager-sandbox` from `BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_ASSEMBLY` or set `ASSEMBLY_EXECUTION_BACKEND=dry-run`, restart launchd per VOY-1814, then verify `/healthz` and a safe `/assembly` dry-run.

---

## Acceptance Criteria

- A fake subprocess backend exists and is selectable without invoking real OMP.
- The fake backend can return `executed` with commit SHA(s), `no_changes`, and `failed` outcomes.
- The fake backend is guarded for test/local use by an explicit allow env var; selecting it without the allow var fails safely.
- Fake commit SHAs are validated before the dispatcher attempts branch / PR work; malformed fake SHAs fail with a progress comment and no branch / PR creation.
- Dispatcher tests prove that a backend returning a commit SHA causes Assembly to create or update a branch, open or update a PR, post `@codex review`, and upsert Assembly progress comments.
- BDD covers the happy path from `/assembly` route to fake backend branch/PR flow.
- Token-safety tests or assertions prove GitHub App installation tokens are never rendered in comments, summaries, failure details, logs, `repr`, or persisted job-contract files.
- Concurrent fake-backend dispatches for the same repository and branch are serialized by the existing asyncio writeback lock and do not duplicate branch / PR creation.
- Real OMP backend remains disabled for production repositories until a sandbox canary passes.
- The sandbox canary plan names `iterwheel/voyager-sandbox` as the first real OMP target and includes rollback to `dry-run`.

---

## Design Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D1 | Introduce an adapter execution context rather than passing raw environment into `execute(contract)`. | The backend needs auth, working directory, timeout, and command path. A typed context makes secret boundaries explicit and testable. |
| D2 | The context may carry an installation token, but `AssemblyJobContract.to_dict()` must not. | The job contract may be rendered in comments and persisted for debugging. Tokens are runtime-only credentials. |
| D3 | Fake subprocess backend is test-only / local-only unless explicitly selected. | It proves dispatcher behavior without requiring OMP, DeepSeek, network, or repository mutation by a real agent. |
| D4 | The fake backend must not bypass branch/PR dispatcher code. | The point is to prove the existing Assembly writeback sequence, not to replace it with a test shortcut. |
| D5 | Real OMP canary is sandbox-only. | The first real coding backend run must not target production repositories. |
| D6 | Adapter-produced commits must already exist on the remote before `commit_shas` are returned. | Existing `ExecutionAdapter` contract requires this because the dispatcher calls `create_branch_ref(..., commit_shas[-1])`. |
| D7 | Do not add a `/assembly --backend` command flag in this CHG. | Backend selection remains env-only per VOY-1819 F2 to avoid expanding the public command surface. |
| D8 | Keep `DRY_RUN` and `ASSEMBLY_EXECUTION_BACKEND` orthogonal. | `DRY_RUN` gates GitHub mutations; backend selection gates subprocess execution. Tests need both dimensions. |
| D9 | Add `ASSEMBLY_BACKEND_FAKE_SUBPROCESS` as a named constant and wire it through `select_execution_adapter`. | Tests and deployment config must not rely on hard-coded backend strings scattered through the codebase. |
| D10 | `AdapterExecutionContext.installation_token` is runtime-only and redacted from representation / safe serialization. | The context may carry secrets, but comments, logs, failures, and persisted contract records must never expose them. |
| D11 | VOY-1807 updates document backend selection and the OMP canary gate only. | The App already exists; this CHG must not imply a new GitHub App is being created. |

---

## Implementation Plan

1. **Plan-review gate.** Run COR-1602 / COR-1609 review on this CHG with `glm`, `deepseek`, and `minimax`. Do not implement until all returned reviewers score at least 9.0 or all P1/P2 findings are resolved, or a reviewer failure is recorded as an infrastructure-blocked exception with the passing reviews preserved.
2. **RED tests first.** Add failing adapter, dispatcher, token-safety, idempotency, and BDD assertions before implementation. Only then implement, run GREEN, and refactor.
3. **Execution context.** Add a small dataclass such as `AdapterExecutionContext` with fields for repository, work directory, timeout, optional installation token, and command path. Its `repr`, safe serialization, comments, failure details, logs, and persisted job-contract files must not leak the token.
4. **Dispatcher wiring.** In `dispatch_assembly_writeback`, obtain the Assembly App installation token only when a non-dry-run, commit-producing backend may need it. Pass the token through context, not contract.
5. **Fake subprocess backend.** Add a deterministic backend selectable by env, `ASSEMBLY_EXECUTION_BACKEND=fake-subprocess`. It must also require an explicit local/test allow env var before returning any commit-producing result.
6. **Fake output safety.** The fake backend reads controlled env/test inputs and returns `AdapterResult` variants without invoking OMP. It must validate fake commit SHAs before returning them; malformed, missing, or non-list SHAs produce `status="failed"` with no commits.
7. **Failure shaping.** Map fake backend timeout, malformed output, non-zero exit, invalid SHA, and no-change outcomes into existing Assembly progress-comment and failure-state shapes without exposing raw secrets.
8. **Unit tests.** Extend adapter and dispatcher tests for context creation, fake success/no-change/failure, branch/PR/codex writeback, idempotency, and token redaction.
9. **BDD.** Extend Assembly BDD with a scenario where `/assembly` on a ready, stack-classified issue reaches the fake backend and records the branch/PR/Codex/progress-comment path.
10. **Docs/config.** Update `config.example.toml`, VOY-1807 registry notes, and any Assembly SOP text needed to document fake backend and sandbox-only OMP canary. VOY-1807 changes are limited to backend selection and canary gate notes; no new GitHub App is created.
11. **Validation.** Run scoped assembly tests, BDD, ruff, mypy, and then full `uv run pytest tests/` if runtime is acceptable.
12. **PR.** Open a PR from `82-assembly-fake-subprocess-backend`, trigger Codex review, and handle COR-1615/COR-1612 loops.
13. **Deploy.** After merge, build/deploy per VOY-1814/VOY-1820 wheel flow.
14. **Sandbox OMP canary.** Verify `omp` exists on Wukong, records its version, confirm a model credential/login is available, obtain Frank Xu approval, set sandbox-only env, trigger one `iterwheel/voyager-sandbox` Assembly issue, and record the result.

---

## Testing / Verification

Required local verification before PR:

```bash
uv run pytest tests/unit/test_assembly_adapters.py
uv run pytest tests/unit/test_assembly_writeback_dispatcher.py
uv run pytest tests/unit/test_assembly_writeback_partial_failure.py
uv run pytest tests/bdd/features/assembly.feature
uv run ruff check .
uv run mypy voyager
```

Recommended full verification before merge:

```bash
uv run pytest tests/
```

Sandbox canary verification after deploy:

1. `curl -fsS https://gh.iterwheel.com/healthz` reports the deployed build commit and `dry_run: false`.
2. `command -v omp` and `omp --version` succeed on Wukong; record the exact command output in the execution log.
3. Frank Xu approves the sandbox canary before real OMP env is enabled.
4. `BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_ASSEMBLY` includes only `iterwheel/voyager-sandbox` for the real OMP canary window.
5. `ASSEMBLY_EXECUTION_BACKEND=pi-oh-my-pi-deepseek` only during the sandbox canary.
6. A ready sandbox issue receives `/assembly` and results in a PR opened by `iterwheel-assembly[bot]`.
7. The Assembly progress comment contains execution summary but no token, API key, clone credential, or local secret path.
8. Rollback to `dry-run` succeeds and a follow-up `/assembly` produces no branch/PR mutation.

---

## Approval

- [ ] Plan reviewed by: glm, deepseek, minimax
- [ ] Approved on: YYYY-MM-DD
- [ ] Implementation reviewed before PR
- [ ] Sandbox canary approved by: Frank Xu before real OMP env is enabled

Approval notes:

- Initial plan review recorded GLM PASS and MiniMax PASS. DeepSeek did not pass; Wukong failed to start the `claude` command (`exec: claude: not found`, exit 127), so this is treated as an infrastructure-blocked exception to note in the PR, not as a DeepSeek approval.
- The advisory fixes from the initial plan review were folded into this CHG before implementation started.
- RED tests were written by an independent test worker. Implementation was then written by a separate implementation worker.
- GLM reviewed the RED test changes and passed them: `.trinity/reviews/20260523-215811-tests`.
- GLM reviewed the implementation changes and passed them: `.trinity/reviews/20260523-221024-voyager-bots-assembly`.
- Stage 2 RED tests for the real OMP backend were written by an independent test worker. Implementation was then written by a separate implementation worker and adjusted by the orchestrator during acceptance.
- GLM reviewed the Stage 2 real OMP backend and passed it: `.trinity/reviews/20260523-231007-VOY-1821-Stage-2-real-OMP-backend-final-dirty-working-tree-diff`.
- Real OMP canary approval is still pending. The real canary has not run and remains limited to `iterwheel/voyager-sandbox` after merge and deploy.

---

## Execution Log

| Date | Action | Result |
|------|--------|--------|
| 2026-05-23 | Created #82 and drafted this CHG. | Proposed; awaiting plan review. |
| 2026-05-23 | Ran initial Trinity plan review for this CHG. | GLM PASS and MiniMax PASS. DeepSeek failed before review because Wukong could not execute `claude` (`exec: claude: not found`, exit 127); this is recorded as an infrastructure-blocked exception for the PR, not a DeepSeek pass. |
| 2026-05-23 | Folded GLM/MiniMax plan-review advisories into the CHG. | Added explicit TDD order, VOY-1820 relation, fake safety gates, token redaction boundary, idempotency coverage, VOY-1807 scope, and Frank Xu sandbox-canary approval requirement. |
| 2026-05-23 | RED tests authored by an independent test worker. | Added unit and BDD coverage for fake subprocess backend selection, token redaction, SHA validation, safe failures, dispatcher branch/PR/Codex/progress-comment flow, and same-branch concurrency/idempotency. GLM reviewed the test scope PASS at `.trinity/reviews/20260523-215811-tests`. |
| 2026-05-23 | Implementation authored by a separate implementation worker. | Added `AdapterExecutionContext`, fake subprocess backend constants/adapter wiring, dispatcher context/token handling, failed-progress rendering, and related config/reference docs. GLM reviewed the implementation scope PASS at `.trinity/reviews/20260523-221024-voyager-bots-assembly`. |
| 2026-05-23 | Completed local verification for the fake subprocess phase. | Passed targeted Assembly tests, BDD via step definitions, `uv run pytest tests/` (`1171 passed`), `uv run ruff check .`, `uv run mypy voyager`, and `af validate`. |
| 2026-05-23 | Confirmed real OMP canary status. | Not run and not approved. It remains sandbox-only for `iterwheel/voyager-sandbox` after merge/deploy; production repositories stay on non-real-OMP execution. |
| 2026-05-23 | Began Stage 2 preflight. | Installed Oh My Pi `omp` v15.2.4 to `/Users/frank/.local/bin/omp`. `omp --list-models deepseek` reported no available models because no provider credential/login is configured in the process environment. |
| 2026-05-23 | RED tests authored by an independent Stage 2 test worker. | Added real OMP adapter tests for token-required selection, context defaults/env overrides, successful clone/run/push shape, no token in argv, no token in OMP/local-command env, and sanitized failures. |
| 2026-05-23 | Implementation authored by a separate Stage 2 implementation worker and accepted by the orchestrator. | Added the real `pi-oh-my-pi-deepseek` adapter using `omp -p`, temp checkout isolation, GitHub App token via temporary `GIT_ASKPASS` only for clone/push, default workdir `~/.voyager/state/assembly`, env overrides, safe failed `AdapterResult` behavior, and docs/config updates. |
| 2026-05-23 | Completed Stage 2 local verification. | Passed `uv run pytest tests/` (`1182 passed`), `uv run ruff check .`, `uv run mypy voyager`, `git diff --check`, and `af validate --root /Users/frank/Projects/voyager`. |
| 2026-05-23 | Ran Stage 2 GLM implementation review. | GLM PASS 9.2/10 at `.trinity/reviews/20260523-231007-VOY-1821-Stage-2-real-OMP-backend-final-dirty-working-tree-diff`; advisory context-guard and BDD-subprocess items were addressed after review. |

---

## Post-Change Review

- Did fake subprocess prove the branch/PR/Codex/progress-comment path without real OMP?
- Did token-safety tests catch all rendered/persisted surfaces?
- Did sandbox OMP canary complete without touching production repositories?
- Are follow-up issues needed for broader rollout or OMP ergonomics?

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-05-23 | Initial CHG draft for #82. | Codex |
