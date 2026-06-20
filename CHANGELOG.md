# Changelog

All notable changes to **iterwheel-voyager** are documented here. Format
loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/spec/v2.0.0.html). Pre-1.0,
minor bumps may still include surface-level breaking changes — see each
release note for the explicit migration path.

## [Unreleased]

### Added — Bounded review-fix loop runner ([#186](https://github.com/iterwheel/voyager/issues/186))

- Governance now exposes an offline-testable bounded review-fix loop runner
  with injectable gather/classify/fix seams, max-round escalation, per-round
  fix caps, convergence detection, kill-switch halting, and append-only audit
  records for each round and terminal outcome.

### Added — Review-fix verify rollback step ([#185](https://github.com/iterwheel/voyager/issues/185))

- Governance now exposes a local verify-and-rollback primitive for review-fix
  commits: passing verification audits `kept`, failing verification creates a
  local `git revert` and audits `rolled_back`, and rollback failures are
  preserved as `revert_failed` audit records for operator follow-up.

### Added — Scheduled CI-failure sweep (L1 advisory) ([#167](https://github.com/iterwheel/voyager/issues/167))

- Wukong can now run a scheduled CI-failure sweep that scans open pull
  requests for failing required checks on the latest head, including legacy
  Commit Status API contexts, and flags them with the `ci-failing` label.
- The sweep comments at most once per failing check-run/status id, removes
  `ci-failing` after required checks return green, preserves the existing
  signal while required checks are still pending or have not reported yet, and
  respects the global `DRY_RUN` and repository allow-list gates before making
  any GitHub calls.
- New Wukong env knobs configure the job: `BRIDGE_CI_FAILING_ENABLED`,
  `BRIDGE_CI_FAILING_INTERVAL_SECONDS`, `BRIDGE_CI_FAILING_REPOSITORY`, and
  `BRIDGE_CI_FAILING_APP_SLUG`; the production allow-list uses the
  feature-specific `BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CI_FAILING` slug.

### Added — Scheduled stale-PR triage (L1 advisory) ([#166](https://github.com/iterwheel/voyager/issues/166))

- Wukong can now run a scheduled stale-PR triage that finds open pull requests
  with no activity for a configurable staleness window, defaulting to 7 days.
- The triage is L1 advisory only: stale pull requests receive the `stale` label
  and at most one reminder comment per staleness window, with no automatic
  close, merge, or review-request mutation; the scheduler also respects the
  global `DRY_RUN` gate before making any GitHub calls.
- New Wukong env knobs configure the job: `BRIDGE_STALE_PR_ENABLED`,
  `BRIDGE_STALE_PR_INTERVAL_SECONDS`, `BRIDGE_STALE_PR_DAYS`,
  `BRIDGE_STALE_PR_REPOSITORY`, and `BRIDGE_STALE_PR_APP_SLUG`.

## [0.6.0] — 2026-06-19

### Added — Assembly loop safety and telemetry ([#157](https://github.com/iterwheel/voyager/issues/157), [#160](https://github.com/iterwheel/voyager/issues/160), [#161](https://github.com/iterwheel/voyager/issues/161))

- Assembly now caps automated fix rounds with a circuit breaker and escalates
  instead of continuing indefinitely after repeated bot-driven retry loops.
- Assembly loop summaries now record rounds, commits, and estimated token usage
  to local state/logs so operators can audit loop cost and behavior.
- Gates now declare a maturity level (`L1`, `L2`, or `L3`), and newly added
  gates default to advisory `L1` behavior before they are allowed to block or
  act unattended.

### Added — Known-limitation decision memory ([#159](https://github.com/iterwheel/voyager/issues/159), [#174](https://github.com/iterwheel/voyager/issues/174))

- Clearance can persist accepted known limitations and suppress matching
  future findings with a link back to the deciding issue.
- Known-limitation fingerprints now use the stable finding identity
  `repo + path + line + rule/check id` instead of the Codex review comment body,
  with the current Clearance `finding_kind` and Codex finding title as
  production fallback candidates, so accepted limitations keep matching when
  Codex rewords the same known finding's detail text.
- Coarse `finding_kind` identities are combined with the Codex title when
  available, preventing one accepted required-check limitation from suppressing
  a different required-check finding at the same line.
- GitHub review-thread fetches now enrich returned threads with production
  `ruleId` / `findingKind` candidates derived from the first Codex comment, so
  webhook processing and tests use the same finding identity path.
- Existing body-based `known_limitations.jsonl` entries remain readable through
  legacy dual lookup when no stable rule candidate is available; new records
  are written with the stable finding-identity fingerprint.

### Added — Release and changelog automation ([#162](https://github.com/iterwheel/voyager/issues/162), [#163](https://github.com/iterwheel/voyager/issues/163))

- The existing pytest CI job now runs a release-readiness gate that finds
  shippable merged PRs since the latest `vX.Y.Z` tag and fails when
  `CHANGELOG.md` has an empty `[Unreleased]` section.
- The checker reports the merged PR numbers/titles that need changelog
  coverage, emits a GitHub annotation line from the CLI, and has fixture-style
  tests for empty and populated `[Unreleased]` sections.
- Voyager now routes merged, changelog-relevant PR webhooks into an
  Assembly-backed changelog draft flow that opens a follow-up PR with an
  `[Unreleased]` bullet for the merged PR.
- Changelog skip labels are ignored by the drafter, and duplicate source PR
  bullets are not re-added.
- Production Wukong deployments must allow-list the changelog route with
  `BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CHANGELOG=iterwheel/voyager`; without
  it, merged-PR changelog events are denied as `repository_not_allowed`.

### Added — Wukong production operations ([#164](https://github.com/iterwheel/voyager/issues/164), [#165](https://github.com/iterwheel/voyager/issues/165))

- Wukong can now route merged same-repo PR events into a cleanup bot that
  deletes non-protected head branches after merge while skipping forks,
  protected branches, non-merged PRs, and non-allow-listed repositories.
- Wukong can run a scheduled deployed-version drift check that compares the
  highest stable SemVer GitHub Release tag with the version reported by the
  bridge `/healthz` endpoint and creates a GitHub issue when production lags.
- New Wukong env knobs are documented in `deploy/wukong/bridge.env.example`:
  `BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CHANGELOG`,
  `BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CLEANUP`,
  `BRIDGE_DRIFT_ALERT_ENABLED`, `BRIDGE_DRIFT_ALERT_REPOSITORY`,
  `BRIDGE_DRIFT_ALERT_BRIDGE_URL`, `BRIDGE_DRIFT_ALERT_INTERVAL_SECONDS`, and
  `BRIDGE_DRIFT_ALERT_APP_SLUG`.

## [0.5.0] — 2026-06-17

### Added — Assembly acceptance-criteria exact-token spot-check ([#151](https://github.com/iterwheel/voyager/issues/151), [#152](https://github.com/iterwheel/voyager/pull/152))

- Assembly now runs a conservative acceptance-criteria spot-check after backend
  verification and before publish. When a checked acceptance criterion (or an
  AC value list) states exact machine-readable tokens that are absent from the
  changed files, Assembly returns a `blocked` terminal result and retains a
  failure bundle instead of publishing.
- The check is intentionally narrow: it only blocks on exact-token misses;
  uncertain prose stays non-blocking and falls through to normal review.
- Failure comments now surface the concrete check/command and whether a local
  patch was left behind.
- New env kill switch `ASSEMBLY_AC_SPOTCHECK` — set to `0` / `false` / `off` to
  disable the gate.
- Secret-shaped config keys (e.g. `OPENAI_API_KEY`) are preserved during token
  matching (only the value is redacted), so a correct key addition is not
  misreported as missing.

### Changed — Structural AC nesting for the spot-check ([#153](https://github.com/iterwheel/voyager/issues/153), [#154](https://github.com/iterwheel/voyager/pull/154))

- The Assembly job contract now preserves acceptance-criteria bullet structure
  (`acceptance_criteria_items` with nesting depth + parent index) instead of a
  flattened list only.
- Removal-list attribution in the spot-check follows the real parent/child
  nesting depth, replacing the previous verb allow-list heuristic for child
  attribution. This removes the class of false negatives where a sibling
  criterion using an unlisted verb (e.g. `Audit \`new-mode\``) lost its required
  token.

### Changed — Dependencies

- Bump FastAPI to `>=0.136,<0.137.2` ([#150](https://github.com/iterwheel/voyager/pull/150), [#155](https://github.com/iterwheel/voyager/pull/155)).

### Known limitations

- Parent/child-shape classification (distinguishing removal headings from
  required-value headings, and recognizing value-list child lines) is still
  phrasing-bound. Unrecognized phrasings fall through to normal review — a
  false-negative (non-blocking) direction, by design. The spot-check is a
  conservative best-effort gate, not a semantic acceptance-criteria verifier.

## [0.4.10] — 2026-05-30

### Fixed — Clearance per-thread verdict comment dedupe ([#146](https://github.com/iterwheel/voyager/issues/146))

- Clearance now treats a `(review_thread_id, head_sha)` pair as having one
  final public verdict comment, preventing later webhook runs from posting
  contradictory `RESOLVED`, `OPEN`, or `NEEDS_HUMAN_JUDGMENT` conclusions for
  the same head.
- Before posting a review-thread reply, Clearance re-fetches the current thread
  comments once per run and suppresses duplicate output when a prior same-head
  verdict comment already exists, including the Assembly resolver fallback case.
- Same-head progress from an earlier `OPEN`/`NEEDS_HUMAN_JUDGMENT` comment to a
  later `RESOLVED` verdict remains allowed, so a substantive author reply can
  still trigger resolution and a close-reason comment.

## [0.4.9] — 2026-05-30

### Fixed — Clearance Assembly evidence and per-thread verdicts ([#141](https://github.com/iterwheel/voyager/issues/141), [#143](https://github.com/iterwheel/voyager/pull/143), [#142](https://github.com/iterwheel/voyager/issues/142), [#144](https://github.com/iterwheel/voyager/pull/144))

- Clearance now recognizes Assembly-authored fix evidence across GitHub REST
  and GraphQL login forms, so `iterwheel-assembly[bot]` and
  `iterwheel-assembly` are treated as the same App actor when matching PR
  author replies.
- Current-head clean Codex issue comments can now resolve fresh or
  cross-file review-thread uncertainty when they are newer than the current PR
  head and newer than the review thread, while newer non-clean Codex signals
  continue to override older clean signals.
- Clearance now posts current-head in-thread verdict comments for unresolved
  `OPEN` and `NEEDS_HUMAN_JUDGMENT` Codex review threads, with verdict,
  confidence, evidence, head SHA, and duplicate prevention keyed by
  `thread_id + head_sha + verdict`.
- The top-level Clearance readiness comment now summarizes per-thread verdict
  counts and verdict-comment writeback counts, making each run auditable
  without opening every GitHub conversation.
- Investigator-backed verdict comments persist and render the underlying LLM
  model name instead of the configured profile name, avoiding misleading
  public labels such as `pro` or `canary`.

## [0.4.8] — 2026-05-28

### Added — Bridge runtime TOML fallback ([#89](https://github.com/iterwheel/voyager/issues/89), [#138](https://github.com/iterwheel/voyager/pull/138))

- Added `[bridge]` and `[assembly]` runtime sections in
  `~/.voyager/config.toml` for non-secret bridge and Assembly knobs, while
  preserving env-over-TOML precedence for backward-compatible emergency
  overrides.
- Bridge dry-run state, per-agent repository allow-lists, Assembly backend
  selection, phase mode, OMP command/workdir/timeout, and Assembly actor
  authorization can now fall back to TOML when the matching env var is unset.
- Webhook secrets remain env-only. Operators can move non-secret runtime
  settings from `~/.voyager/bridge.env` into `config.toml`, then restart the
  bridge after deploying this wheel.

### Fixed — Wukong production wheel install SOP ([#81](https://github.com/iterwheel/voyager/issues/81), [#139](https://github.com/iterwheel/voyager/pull/139))

- Updated the Wukong deployment SOP to install release wheels with
  `uv pip install --python <versioned-venv>/bin/python <wheel>` so it no
  longer assumes `uv venv` created a `<versioned-venv>/bin/pip` executable.
- Preserved the versioned-venv plus atomic `mv -hf` symlink-swap deployment
  flow and documented why `uv pip install --python ...` is the reliable
  install command on Wukong.

## [0.4.7] — 2026-05-28

### Added — Assembly two-phase implementer/TestPilot mode ([#96](https://github.com/iterwheel/voyager/issues/96), [#136](https://github.com/iterwheel/voyager/pull/136))

- Assembly can now opt into a two-phase execution mode that separates the
  implementer phase from an independent TestPilot phase, while preserving the
  existing single-phase behavior as the default when `ASSEMBLY_PHASE_MODE` is
  unset.
- Added phase-aware backend selection via `ASSEMBLY_IMPLEMENTER_BACKEND` and
  `ASSEMBLY_TESTPILOT_BACKEND`, plus phase metadata in adapter execution
  contexts so operators can route implementation and validation to different
  backends.
- TestPilot can run after a successful implementer pass, add follow-up commits
  to the same Assembly PR branch, and block the run when it finds unresolved
  acceptance-criteria gaps or incomplete dry-run phase results.
- Progress comments now show compact per-phase status for implementer,
  TestPilot, verification, and next action, and the Assembly SOP documents when
  to use two-phase mode versus the single-phase compatibility path.

## [0.4.6] — 2026-05-27

### Added — Assembly resolver fallback for Clearance ([#131](https://github.com/iterwheel/voyager/issues/131))

- Clearance Stage 1.5 can now use the allow-listed Assembly App identity to
  resolve verified-fixed Codex review threads on Assembly-authored PRs when
  GitHub reports `viewerCanResolve=false` for the Clearance App, while keeping
  non-authorized authors on the manual-close path.

### Fixed — Clearance manual-close verification replies ([#130](https://github.com/iterwheel/voyager/issues/130))

- Clearance now posts an in-thread verification reply when it judges a Codex
  review thread `RESOLVED` but skips `resolveReviewThread` because GitHub
  reports `viewerCanResolve=false`, preserving the safe skip while leaving
  reviewer-visible evidence and manual-close guidance.

## [0.4.5] — 2026-05-27

### Fixed — Clearance outdated visual-unresolved review threads ([#119](https://github.com/iterwheel/voyager/issues/119), [#124](https://github.com/iterwheel/voyager/issues/124), [#128](https://github.com/iterwheel/voyager/pull/128))

- Clearance now treats outdated Codex review threads as semantically resolved
  when a later current-head Codex review reports an exact canonical clean
  verdict, while rejecting mixed or newer non-clean Codex reviews as stale
  clean evidence.
- Persisted thread state now records clean-review evidence instead of keeping
  stale investigator `OPEN` text for already-fixed outdated review comments.
- Readiness output separates semantic blockers from GitHub conversations that
  remain visually unresolved only because `viewerCanResolve=false`, preserving
  the Stage 1.5 skip rather than forcing an unsupported `resolveReviewThread`
  mutation.

## [0.4.4] — 2026-05-25

### Fixed — Assembly App-token git publish isolation ([#121](https://github.com/iterwheel/voyager/issues/121))

- Assembly now disables host git credential helpers for App-token branch
  publish subprocesses, preventing stale local HTTPS credentials from
  overriding the temporary `GIT_ASKPASS` installation token.

## [0.4.3] — 2026-05-25

### Added — Assembly backend failure diagnostics ([#93](https://github.com/iterwheel/voyager/issues/93))

- Assembly now records sanitized subprocess backend failure diagnostics for
  clone/config/fetch/checkout/OMP/git/verification/push phases, surfaces a
  compact public failure panel in progress comments, and retains failed OMP
  checkouts under a deterministic private debug bundle path.
- Added `VOY-1824`, the Assembly failure diagnostics SOP, and extended private
  audit manifests with `failure_diagnostic` and `failure_debug_bundle_path`.

## [0.4.2] — 2026-05-25

### Added — Assembly resumable backend sessions ([#105](https://github.com/iterwheel/voyager/issues/105))

- Assembly now accepts `/assembly --resume`, validates compatible private
  session metadata for the same repository, issue, branch, PR, head SHA, and
  backend, and reports `fresh`, `resumed`, or `resume_fallback` in progress
  comments and audit manifests.
- The OMP-backed adapter passes a compatible stored session path through
  `omp --resume=...`; unsafe, expired, missing, or unsupported resume requests
  fall back to a fresh run without exposing private session paths on GitHub.

### Fixed — Clearance Stage 1.5 observability ([#110](https://github.com/iterwheel/voyager/issues/110))

- Clearance readiness comments now distinguish applied, skipped, and failed
  Stage 1.5 review-thread sync actions, and surface skipped `viewerCanResolve`
  auto-resolve attempts so operators can tell when GitHub conversations remain
  visually unresolved even though Clearance no longer treats them as blockers.

### Fixed — Clearance same-repo auto-resolve diagnostics ([#106](https://github.com/iterwheel/voyager/issues/106))

- Clearance now preserves sanitized GitHub GraphQL error type/message details
  in writeback failures, includes them in readiness warnings, and documents that
  even same-repository PR review threads must be gated on `viewerCanResolve`
  before attempting `resolveReviewThread`.

## [0.4.1] — 2026-05-24

### Added — Assembly production-loop SOPs and audit lookup ([#94](https://github.com/iterwheel/voyager/issues/94), [#95](https://github.com/iterwheel/voyager/pull/95), [#92](https://github.com/iterwheel/voyager/issues/92), [#97](https://github.com/iterwheel/voyager/pull/97))

- Added `VOY-1822`, the Assembly-driven implementation loop SOP for real issue
  to PR work, including Codex review settle gates, Clearance handoff, retry
  rules, and operator checklists.
- Added private Assembly OMP audit manifests plus `VOY-1823` audit lookup
  guidance so operators can inspect backend session metadata without putting
  secrets or private traces on GitHub.

### Added — Assembly repository-specific verification commands ([#90](https://github.com/iterwheel/voyager/issues/90), [#91](https://github.com/iterwheel/voyager/pull/91))

- Added per-repository Assembly verification command overrides via
  `ASSEMBLY_VERIFICATION_COMMANDS_<encoded-repo>`, with config examples for
  repositories whose test/lint/typecheck commands differ from Voyager's
  defaults.

### Changed — Codex review settle gate for managed Assembly loops ([#98](https://github.com/iterwheel/voyager/issues/98), [#101](https://github.com/iterwheel/voyager/pull/101))

- Tightened `VOY-1822` so operators wait for a terminal Codex review signal on
  the current head SHA, including a delayed-review settle check, before
  declaring an Assembly PR ready for approval.

### Fixed — Clearance unsupported auto-resolve boundary ([#100](https://github.com/iterwheel/voyager/issues/100), [#103](https://github.com/iterwheel/voyager/pull/103))

- Clearance Stage 1.5 now skips unsupported auto-resolve attempts when GitHub
  reports that the viewer cannot resolve a review thread, instead of trying a
  mutation that is known to fail.

### Changed — Same-repository PR requirement for managed Assembly flows ([#99](https://github.com/iterwheel/voyager/issues/99), [#104](https://github.com/iterwheel/voyager/pull/104))

- `VOY-1822` now requires managed Assembly/Codex implementation PRs to use a
  branch in the target repository, so `headRepository == baseRepository`.
  Fork PRs remain a human-managed exception path and are documented as a
  Clearance auto-resolve risk.
- Assembly writeback now verifies existing, newly-created, and duplicate
  no-change PR contexts before preserving them, failing closed when PR
  repository metadata is missing or points at a fork.
- Release guidance now cross-links the human-managed fork release flow to the
  managed-flow same-repository requirement, so future agents do not copy the
  release PR topology into Assembly-managed work.

## [0.4.0] — 2026-05-24

### Added — Assembly bot MVP and GitHub App writeback ([#67](https://github.com/iterwheel/voyager/issues/67), [#68](https://github.com/iterwheel/voyager/issues/68), [#69](https://github.com/iterwheel/voyager/pull/74))

- Added the Assembly implementation bot for `/assembly` and `/implement`
  issue comments on ready, allow-listed issues, including job-contract
  extraction, branch creation, PR open/update, `@codex review` trigger
  comments, and issue/PR progress comments.
- Added the `iterwheel-assembly` GitHub App registry, permission model, config
  examples, and Assembly safety boundaries: Assembly never merges, approves, or
  resolves review threads.
- Added live issue re-validation, per-command `--dry-run`, CRLF command
  parsing, and stable progress-comment behavior for failed and partial
  writeback paths.

### Added — Assembly authorization and hardening ([#73](https://github.com/iterwheel/voyager/issues/73), [#76](https://github.com/iterwheel/voyager/issues/76))

- Added actor authorization for Assembly triggers, including bot exclusion,
  trusted actor/association policy, warning logs for sender/comment-user
  divergence, and an `unauthorized_actor` refusal comment.
- Hardened Assembly idempotency with a per-`(repository, branch)` writeback
  lock, existing-PR update behavior, SHA-contract documentation, empty-title
  acceptance-criteria handling, and issue-closed refusal documentation.

### Added — Deployable wheel, build metadata, and `vyg` CLI ([#75](https://github.com/iterwheel/voyager/issues/75), [#80](https://github.com/iterwheel/voyager/pull/80))

- Added wheel packaging with build-commit injection, a wheel-content guard,
  `voyager._build_info` fallback behavior, and `/healthz` version/build
  metadata.
- Added the `vyg` CLI for running the bridge from an installed wheel and
  documented Wukong's wheel-based launchd deployment flow.
- Added wheel smoke tests and rollback-oriented deployment helpers, including
  macOS symlink-swap fixes and stale artifact cleanup before builds.

### Added — Assembly fake subprocess and real OMP backend canary ([#82](https://github.com/iterwheel/voyager/issues/82), [#83](https://github.com/iterwheel/voyager/pull/83), [#84](https://github.com/iterwheel/voyager/pull/84), [#87](https://github.com/iterwheel/voyager/pull/87))

- Added a guarded fake subprocess backend for local/test Assembly execution,
  including executed, no-change, failed, timeout, malformed-output, and invalid
  SHA outcomes.
- Added the real `pi-oh-my-pi-deepseek` Assembly backend using `omp -p`,
  isolated temporary checkouts, GitHub App installation tokens via temporary
  `GIT_ASKPASS` only for git clone/push, and token-redaction tests.
- Added environment controls for the real backend:
  `ASSEMBLY_EXECUTION_BACKEND`, `ASSEMBLY_PI_COMMAND_PATH`,
  `ASSEMBLY_PI_WORKDIR`, and `ASSEMBLY_PI_TIMEOUT_SECONDS`.
- Recorded the first sandbox-only OMP canaries on `iterwheel/voyager-sandbox`,
  including successful PR creation, rollback verification, and token/API-key
  boundary checks. Production repositories remain outside the real-OMP rollout.

### Fixed — Assembly duplicate no-change progress downgrade ([#85](https://github.com/iterwheel/voyager/issues/85), [#86](https://github.com/iterwheel/voyager/pull/86))

- Fixed a duplicate `/assembly` delivery path where a later `no_changes`
  result could overwrite the source issue's progress comment from
  `status: applied` to `status: no_changes` after a PR had already been
  opened.
- Assembly now preserves existing branch/PR context for duplicate no-change
  dispatches while keeping true first-run no-change results visible when no PR
  exists.

### Fixed — Clearance fork writeback and stale Codex thread handling ([#62](https://github.com/iterwheel/voyager/issues/62), [#63](https://github.com/iterwheel/voyager/issues/63), [#64](https://github.com/iterwheel/voyager/pull/64), [#65](https://github.com/iterwheel/voyager/pull/65))

- Clearance now skips `resolveReviewThread` on fork PRs without head-repository
  access and avoids caching a negative fork-access result before the first
  mutation attempt.
- Stale State A Codex threads now route through the investigator path instead
  of being treated as a normal unresolved actionable finding.

### Changed — VOY-1811 operating loop documentation ([#56](https://github.com/iterwheel/voyager/issues/56), [#59](https://github.com/iterwheel/voyager/issues/59), [#61](https://github.com/iterwheel/voyager/pull/66), [#78](https://github.com/iterwheel/voyager/pull/78))

- Added the VOY-1811 completion gate for related-PR review-thread sweeps,
  delayed-review checks, and distinct issue-closure versus review-thread
  closure criteria.
- Added DeepSeek TUI durable wakeup notes and the Phase 8 requirement to post
  `@codex review` after each PR push during the iteration loop.
- Added a session retrospective documenting the #76 VOY-1811 run and follow-up
  automation candidates.

### Added — Wukong launchd bridge runbook ([#44](https://github.com/iterwheel/voyager/issues/44))

- Added a repo-safe launchd plist template, Wukong env-file template, and
  `VOY-1814` operator SOP for managing the Voyager bridge on
  `127.0.0.1:8787`.
- Documented private file locations, start/stop/restart/status/log-tail
  commands, healthchecks, and rollback to a previous git tag while preserving
  `DRY_RUN=false` plus app-specific repository allow-lists.

### Changed — Clearance DeepSeek profile policy ([#46](https://github.com/iterwheel/voyager/issues/46))

- Documented Flash, Flash no-thinking, Pro, and Pro max investigator
  profiles with separate confidence thresholds and production-use guidance.
- Made Flash/unknown-model startup warnings actionable while preserving the
  current Flash no-thinking canary behavior until an operator changes
  `[voyager].default_profile`.
- Treats moving public aliases such as `deepseek-chat` as unknown until a
  rollout document pins them to a Voyager policy tier.

### Added — Stack metadata issue template ([#47](https://github.com/iterwheel/voyager/issues/47))

- Added a structured GitHub issue template with optional `Stack Type` and
  `Stack Area` fields plus allowed-value guidance for authors.
- Added Blueprint and Stack regression coverage showing optional Stack
  metadata remains Blueprint-ready and overrides noisy weighted signals when
  provided.

### Added — Managed repository canary expansion plan ([#48](https://github.com/iterwheel/voyager/issues/48))

- Added `VOY-1816`, a staged canary expansion SOP that orders
  `frankyxhl/babs` before `frankyxhl/screen-harness`, excludes
  `frankyxhl/sweeping-monk`, and requires one repository per validation cycle.
- Documented preflight checks, per-bot enablement, validation records, and
  allow-list rollback steps without expanding Wukong production scope.

### Fixed — Clearance writeback failure visibility ([#45](https://github.com/iterwheel/voyager/issues/45))

- Clearance now captures GitHub writeback failures, including
  `resolveReviewThread` permission/API failures, as sanitized structured
  metadata and surfaces a compact operator warning in the PR readiness panel.
- Generic label, reaction, and comment writeback failures now return
  `writeback_failures` metadata without leaking raw exception messages,
  tokens, Authorization headers, or secret-bearing URLs.
- GitHub GraphQL `data.errors` now raise a typed `GitHubGraphQLError` so
  callers can distinguish GraphQL API failures from transport failures.

## [0.3.0] — 2026-05-17

### Changed — Clearance compact thread verification cards ([#40](https://github.com/iterwheel/voyager/issues/40))

- Clearance review-thread conclusion replies now render as compact emoji
  cards for resolved, still-open, and needs-human-judgment outcomes.
- The existing close-reason/conclusion HTML markers are preserved for
  duplicate-reply prevention, while detailed verifier evidence moves into a
  collapsible `<details>` section.

### Changed — Stack compact classification panel ([#38](https://github.com/iterwheel/voyager/issues/38))

- Stack classification comments now render as a compact `## Stack` emoji
  panel with type, area, size, risk, status, and next action at the top.
- Detailed classifier metadata, review reasons, suggested/applied labels, and
  area scores now live inside a collapsible `<details>` section while keeping
  the existing Stack comment marker for upserts.

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

[Unreleased]: https://github.com/iterwheel/voyager/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/iterwheel/voyager/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/iterwheel/voyager/compare/v0.4.10...v0.5.0
[0.4.10]: https://github.com/iterwheel/voyager/compare/v0.4.9...v0.4.10
[0.4.9]: https://github.com/iterwheel/voyager/compare/v0.4.8...v0.4.9
[0.4.8]: https://github.com/iterwheel/voyager/compare/v0.4.7...v0.4.8
[0.4.7]: https://github.com/iterwheel/voyager/compare/v0.4.6...v0.4.7
[0.4.6]: https://github.com/iterwheel/voyager/compare/v0.4.5...v0.4.6
[0.4.5]: https://github.com/iterwheel/voyager/compare/v0.4.4...v0.4.5
[0.4.4]: https://github.com/iterwheel/voyager/compare/v0.4.3...v0.4.4
[0.4.3]: https://github.com/iterwheel/voyager/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/iterwheel/voyager/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/iterwheel/voyager/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/iterwheel/voyager/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/iterwheel/voyager/releases/tag/v0.3.0
[0.2.0]: https://github.com/iterwheel/voyager/releases/tag/v0.2.0
[0.1.0]: https://github.com/iterwheel/voyager/tree/b2e4ca1
