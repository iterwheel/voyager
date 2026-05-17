# Changelog

All notable changes to **iterwheel-voyager** are documented here. Format
loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/spec/v2.0.0.html). Pre-1.0,
minor bumps may still include surface-level breaking changes — see each
release note for the explicit migration path.

## [Unreleased]

### Fixed — Stack tied-area label preservation ([#37](https://github.com/iterwheel/voyager/issues/37))

- Stack now preserves an existing human-confirmed issue classification when a
  rerun only needs review because the top area scores are tied and the issue
  already has exactly one Stack label per axis.
- Stack still routes first-pass tied-area issues and incomplete or conflicting
  existing classifications to `stack-needs-review`.

### Added — Multi-agent loop configuration ([#32](https://github.com/iterwheel/voyager/issues/32))

- Added `VOY-1811`, Voyager's project-local COR-1622 parameter
  instantiation for the COR-1617 multi-agent workflow loop, covering
  repository identity, fork PR topology, panel providers, worker dispatch,
  bot actors, runtime profile, invocation variants, adoption status, and
  known schema limitations.
- Updated `VOY-1807` to reflect that Clearance readiness panels now run on
  `iterwheel/voyager`, matching the `VOY-1811` bot-actor configuration.

### Changed — Clearance author-only reviewer deadlock warning ([#28](https://github.com/iterwheel/voyager/issues/28))

- Clearance now surfaces an explicit readiness-panel warning when the only
  configured review-request user is also the PR author. The PR remains at
  `clearance-3-ready-for-approval`, and the panel tells operators to add or
  request an eligible non-author configured reviewer or update
  `VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS`.
- The review-request dispatcher logs the author-only reviewer deadlock with
  repository, PR number, configured users, and PR author so operators can
  diagnose the misconfiguration server-side.

### Changed — Clearance readiness panel ([#30](https://github.com/iterwheel/voyager/issues/30))

- Clearance PR-level readiness comments now use the existing marker with
  `comment_mode = "upsert"`, so repeated `/clearance`, PR, review, and
  CI/webhook triggers update one status panel instead of appending new
  top-level comments.
- The readiness comment is now a compact emoji status panel with numbered
  stage/label, review-request status, thread/approval/automation summary,
  concise next action, and diagnostics inside `<details>`.
- Stage 1.5 review-thread evidence replies remain inline and append-only.

## [0.2.0] — 2026-05-17

### Added — Numbered Clearance readiness labels ([#25](https://github.com/iterwheel/voyager/issues/25), [#26](https://github.com/iterwheel/voyager/pull/26))

Replace the three unnumbered Clearance writeback labels with four numbered
ones and introduce an explicit "ready for human approval" state plus a
configurable review-request dispatcher.

| Status (internal) | Label | Replaces |
|---|---|---|
| `clearance_pending` | `clearance-1-pending` | `clearance-pending` |
| `clearance_blocked` | `clearance-2-blocked` | `clearance-blocked` |
| `clearance_ready_for_approval` *(new)* | `clearance-3-ready-for-approval` | — |
| `clearance_ready` | `clearance-4-ready-for-merge` | `clearance-ready` |

Label colors (documented in `VOY-1805`): `#FBCA04` / `#D93F0B` /
`#5319E7` / `#0E8A16`.

#### Behavioural changes

- `ALL_CLEARANCE_LABELS` preserves the one-label-only invariant **and**
  removes legacy labels on every writeback. In-flight PRs migrate to
  the numbered scheme automatically on the next Clearance trigger; no
  manual cleanup needed.
- `VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS` (comma-separated GitHub
  logins) gates the ready / ready-for-approval split. When **unset**
  (default), behaviour is unchanged from `0.1.0` — any current-head
  approval marks the PR as ready-for-merge. When **set**, ready-for-merge
  requires a current-head approval from a configured user; otherwise
  the PR enters `clearance-3-ready-for-approval` and Clearance
  dispatches a review request to the configured users.
- Review-request dispatch (live mode only) calls
  `POST /repos/.../pulls/<n>/requested_reviewers`. It:
  - skips the PR author,
  - dedupes users already in `requested_reviewers` (case-insensitive),
  - narrows GitHub 422 to "already requested (422 race)" **only** when
    the response body matches the duplicate-reviewer error code; other
    422s surface as sanitized failures,
  - logs every outcome at INFO (warning on failure, no exception trace
    in the public PR comment).
- The Clearance comment now includes a `Review request: ...` line in
  the ready-for-approval state (`requested @x` / `already requested @y`
  / `skipped PR author @z`; multiple parts joined with `; `).
- State machine: 4 new label-name signals wired with `PR_OPEN`
  first-eval-block and `CLEARANCE_READY → ready-for-approval` downgrade
  transitions. Legacy signals (`clearance-pending`, `clearance-ready`,
  `clearance-blocked`) retained so in-flight PRs do not wedge.
- Case-insensitive comparison across configured-approver match
  (evaluator + overlay) and already-requested dedup (dispatcher).

#### Migration

For deployments that previously relied on `clearance-pending`,
`clearance-blocked`, or `clearance-ready`:

1. The new labels (`clearance-1-pending` / `-2-blocked` /
   `-3-ready-for-approval` / `-4-ready-for-merge`) must be created in
   each managed repository with the colors above. The Clearance bot
   reads/writes labels but does not create them.
2. The legacy three labels can be deleted at the operator's
   convenience — Clearance removes them from every PR on its next
   writeback. They can also be left in place; they will just
   accumulate as unattached labels.
3. Sandbox E2E expectations: any `scripts/e2e/matrix.yaml`-style fixture
   files in downstream repos that hard-code legacy label strings need
   updating. The voyager sandbox matrix was updated in `57afe48`.

#### New configuration

- Environment variable `VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS` —
  comma-separated GitHub logins (whitespace-stripped, empty parts
  dropped). Empty / unset = legacy 0.1.0 behaviour. Example:
  `VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS=frankyxhl,alice`.

#### Tooling

- Added pre-push hooks to `.pre-commit-config.yaml` mirroring the CI
  lint job (`ruff check . && ruff format --check . && pytest`). New
  operators activate with:
  ```
  uv run pre-commit install --hook-type pre-commit --hook-type pre-push
  ```
- Mypy hook switched from the upstream mirror (isolated venv, missing
  runtime deps) to a local hook using the project's `uv` venv.

#### Docs

- `VOY-1805` (Bot Accounts + Responsibilities): label table updated
  with the 4 numbered names + hex colors + legacy-migration note.
- `VOY-1807` (GitHub App Registry): operational labels updated to
  numbered scheme.

#### Known limitations (tracked as follow-up issues)

- [#27](https://github.com/iterwheel/voyager/issues/27) — `Review
  request: already requested @user` line is appended (not upserted) on
  every Clearance trigger; long-lived `ready-for-approval` PRs
  accumulate duplicate comment bodies. Design decision pending between
  steady-state line suppression and switching `comment_mode` to upsert.
- [#28](https://github.com/iterwheel/voyager/issues/28) — when the PR
  author is the only configured reviewer in
  `VOYAGER_CLEARANCE_REVIEW_REQUEST_USERS`, the PR sits at
  `ready-for-approval` indefinitely (author can't approve their own
  PR). Design decision pending between warn-only and degrade-to-current-
  head-approval.

#### Internals (advisories from the Trinity / Codex bot review panels)

- `_PREEMPTING_REASON_PREFIXES` in `overlay.py` duplicates reason
  strings that the evaluator constructs in `evaluation.py`. A future
  reword on either side would silently break the overlay's preempt
  logic. Worth consolidating into shared constants in `constants.py`
  in a follow-up PR.
- Draft-PR detection is asymmetric between the evaluator (structured
  `pull_request.draft`) and the overlay (reason-string `startswith`).
  Same consolidation candidate as above.

### Process

This was the first release exercised end-to-end under
**WUK-2101 (Subagent TDD Split with Trinity Code Review)**: tests-only
subagent writes RED tests, implementation-only subagent writes GREEN
production code, Trinity panel (`codex` / `gemini` / `minimax` over
multiple rounds) reviews the diff. The Codex bot's PR-time review
caught a P1 routing bug the Trinity panels missed (bootstrap case:
env-set + no approvals → `clearance_ready_for_approval` was
unreachable), which the orchestrator addressed in `c162117` per
**COR-1623 (PR Review Thread Verification)**.

## [0.1.0] — earlier (no formal release notes)

Initial public surface — Blueprint / Stack / Clearance bots, GitHub App
auth, FastAPI webhook bridge, DeepSeek LLM adapter, rocket-factory
pipeline state machine, SWM-1101 per-thread verdict pipeline. See
`b2e4ca1` and prior history.

[0.2.0]: https://github.com/iterwheel/voyager/releases/tag/v0.2.0
[0.1.0]: https://github.com/iterwheel/voyager/tree/b2e4ca1
