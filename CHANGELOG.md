# Changelog

All notable changes to **iterwheel-voyager** are documented here. Format
loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/spec/v2.0.0.html). Pre-1.0,
minor bumps may still include surface-level breaking changes — see each
release note for the explicit migration path.

## [Unreleased]

### Added

- Merge loop writes a SECOND, local-only, full-fidelity audit file
  (`~/.voyager/merge-loop.audit.full.jsonl`, mode `600`) alongside the
  existing redacted `merge-loop.audit.jsonl`. Every repo's decision and
  merge-intent record gets the raw `pr`, `reason`, `head`, and
  `review_decision` — no redaction — for local forensics ("which PR did the
  loop touch at HH:MM?"). The redacted file and its fail-closed
  write-ahead contract are unchanged and remain the only one safe to
  share/paste; the full file's write is best-effort and never blocks a
  merge ([#307](https://github.com/iterwheel/voyager/pull/307)).

### Changed — Clearance Stage 3 AND Stage 4 require Codex-reviewed-current-head evidence (BEHAVIOR CHANGE)

- **Clearance no longer requests the operator's review, nor reports the PR
  merge-ready, before Codex has reviewed the current PR head.**
  Operator-reported defect (`order_system_django` #71): Clearance requested
  human approval 9 seconds after PR creation because "zero Codex review
  threads" was treated as "review clear" — Codex hadn't reviewed anything
  yet. Both `clearance_ready_for_approval` (Stage 3, gating the review
  request) and `clearance_ready` (Stage 4, "ready for Countdown"/merge —
  reachable directly when an approval is already present, e.g. the operator
  approves before Codex reviews) now additionally require at least one of:
  (a) a non-dismissed Codex PR review submitted against the current head
  commit; (b) a Codex clean-verdict PR comment — in either of two dialects:
  (b1) one carrying a `Reviewed commit:` footer that prefix-matches the
  current head (head-anchored), or (b2) one with NO footer at all, accepted
  when its `created_at` is later than the current head's arrival timestamp
  (time-anchored, same mechanism as (c) below — reuses `pipeline.py`'s own
  `_is_clean_current_codex_issue_comment` verbatim, since the SWM
  per-thread pipeline already treats this footer-less dialect as clean and
  the gate needed to agree with it rather than re-implement a third parser).
  A footer naming a *different* head is rejected outright and never falls
  through to the time-anchored check, however freshly timestamped — an
  explicit wrong-head footer is stronger evidence than a timestamp. Absent
  all current-head evidence, status stays `clearance_pending` with a
  "waiting for Codex review of the current head"
  reason, no review request is made, and (new) the PR is not reported
  merge-ready either — closing the path where an operator approving early
  let a stage>=3-gated merge loop auto-merge a head Codex never saw. Also
  (c) a Codex `+1` reaction on the PR body posted after the current head
  arrived — mirroring the "thumbs" clean signal
  `voyager/core/codex_review_watch.py` already treats as clean, and the same
  signal the `reaction` webhook already routes Clearance for. A reaction
  carries no commit id, so it's anchored by TIME instead of head SHA: it
  only counts when its `created_at` is later than the current head's
  arrival timestamp (`GitHubAppClient.pull_request_head_updated_at` — the
  same GraphQL timestamp `pipeline.py` already uses for the identical
  staleness problem on Codex clean-verdict issue comments). Fails closed:
  no arrival timestamp available means the reaction never counts, no matter
  how recent. (A fourth candidate evidence type — Codex review-thread
  existence/resolution — was considered and rejected: thread state cannot
  be reliably head-anchored, since a thread resolved on an old head still
  reads "resolved" after a push with zero new Codex activity, and GitHub
  can re-anchor an old, untouched comment to carry the *new* commit id;
  whenever Codex leaves inline findings it also submits a PR review, so (a)
  already covers that case without trusting thread state.) New pure
  predicate `codex_reviewed_current_head` and gate
  `enforce_codex_review_gate` (covering both stages) in
  `voyager/bots/clearance/evaluation.py`, applied both by
  `evaluate_clearance_snapshot` and — because the SWM overlay can
  independently promote to either stage from `automation["status"] in
  {"ready", "ready_with_low_priority"}` without going back through the
  classifier's own branch chain — again by `enrich_clearance_route` after
  the overlay runs. The snapshot passed to `evaluate_clearance_snapshot`
  gained `issue_comments`, `reactions`, and `head_updated_at` keys. All
  three of these evidence-feeding fetches (`reactions`/`head_updated_at`
  for (c), `issue_comments` for (b)) are individually wrapped so a
  GraphQL/REST failure on any one degrades to "that evidence type is
  unavailable" instead of aborting the whole route before evaluation runs;
  the other evidence types still gate/pass normally — e.g. a head-anchored
  Codex review (a) reaches Stage 3 even if the comments or reactions fetch
  failed. `GitHubAppClient.issue_reactions` is now paginated (same
  page/`per_page=100` loop shape as `issue_comments`) — previously a Codex
  `+1` posted after 100 other PR-body reactions accumulated was invisible,
  leaving a thumbs-only-clean PR stuck pending forever
  ([#308](https://github.com/iterwheel/voyager/pull/308)).

## [0.11.0] — 2026-08-09

### Changed — Merge-loop approval gate (BREAKING for zero-touch flows)

- **BREAKING:** The merge loop no longer merges unapproved PRs. It now gates
  on GraphQL `reviewDecision == "APPROVED"`, re-verified both at snapshot
  time (`PrSnapshot.review_decision`) and again immediately before the merge
  mutation (apply-time recheck, alongside the existing base-retarget guard).
  This reverses the v0.9.0 zero-touch design: every target repo's ruleset
  must now require at least one approving review
  (`required_approving_review_count: 1`), and the loop only completes a
  merge the operator has actually approved — "once I approve, it merges
  itself." A repo with no required-review ruleset configured returns a null
  `reviewDecision` and is deliberately unmergeable by the loop until that
  ruleset is set. New skip reasons: `not_approved` (snapshot time),
  `approval_revoked_at_apply` (approve-then-revoke race)
  ([#304](https://github.com/iterwheel/voyager/pull/304)).

## [0.10.0] — 2026-08-08

### Added — Merge-loop operator-configurable author allowlist

- Added `VOYAGER_MERGE_EXTRA_AUTHORS`, an operator-local extension of the
  merge-loop's author gate mirroring `VOYAGER_MERGE_EXTRA_REPOS`: built-in
  agent author plus env-listed GitHub logins (GraphQL login form, e.g.
  `dependabot`), fail-closed parsing, lowercase-normalized matching, resolved
  once per run. With the variable unset, behavior is identical to v0.9.0
  ([#301](https://github.com/iterwheel/voyager/pull/301)).

### Operator notes

- To auto-merge dependabot dependency bumps on an allowlisted repo, set
  `VOYAGER_MERGE_EXTRA_AUTHORS=dependabot` in `~/.voyager/merge-loop.env`
  (GraphQL login form — not `app/dependabot` or `dependabot[bot]`; malformed
  entries fail closed). No other config, dependency, or migration changes;
  leaving the variable unset keeps v0.9.0 behavior exactly.

## [0.9.0] — 2026-08-08

### Added — Countdown merge loop

- Added `vyg countdown merge-loop`, an autonomous rebase-merge loop for
  agent-authored PRs. A PR is merged only when every deterministic condition
  holds on the current head: author is the fixed agent identity, the base
  branch is in the allowed set (`main`), the base has not advanced past the
  head's checks, CI is fully green, zero unresolved review threads, and the
  clearance readiness marker (verified Bot-actor authorship) reports Stage 3
  for that exact head SHA. Any miss fails closed to a skip with an audit
  reason; there is no human per-PR gate and no LLM gate. The live path
  re-verifies base branch, base freshness, and audit writability immediately
  before the single `mergePullRequest` (REBASE + `expectedHeadOid`) mutation,
  merges at most one PR per repo per cycle, and hard-gates on the machine
  account identity. Reuses the resolve-loop's allowlist ceiling,
  single-instance lock, per-run attempt cap, and redacted write-ahead audit
  trail. Includes default-off launchd deployment templates (env/repos files,
  adaptive wrapper, plist) and the VOY-1840 deployment SOP (VOY-1839)
  ([#298](https://github.com/iterwheel/voyager/pull/298)).

### Operator notes

- The merge loop ships default-off (`MERGE_LOOP_ENABLED=false`); installing
  the LaunchAgent performs no live merges. New env surface:
  `MERGE_LOOP_ENABLED`, `MERGE_MAX_MERGES`, `MERGE_FAST_INTERVAL`,
  `MERGE_SLOW_INTERVAL`, `MERGE_FAST_STREAK_MAX`, `VOYAGER_MERGE_EXTRA_REPOS`.
- Going live on a target repo REQUIRES the VOY-1840 Rollout Gate, including
  the ruleset changes with `strict_required_status_checks_policy: true`
  ("require branches up to date") — the loop's apply-time base re-reads
  narrow but cannot eliminate the base-advance race (`mergePullRequest` has
  no `expectedBaseOid`); the server-side strict check is the mandatory
  backstop.
- No model, dependency, or migration changes. The resolve loop is untouched.

## [0.8.2] — 2026-08-02

### Fixed

- Suppressed stale Clearance `OPEN` and `NEEDS_HUMAN_JUDGMENT` thread replies
  when the existing pre-writeback refresh observes a newer PR-author or Codex
  comment that was not part of the classified snapshot
  ([#295](https://github.com/iterwheel/voyager/pull/295)).

### Operator notes

- The change is limited to Clearance verdict-writeback freshness. It introduces
  no model, dependency, configuration, environment-variable, or migration
  changes.
- Deploy v0.8.2 and restart long-running Voyager processes to activate the fix.
  Existing contradictory comments are not removed automatically.
- `RESOLVED` and Stage 1.5 behavior are unchanged. The guard narrows the
  snapshot-to-write race window, but the fresh-read and GitHub reply-create
  operations remain non-transactional.

## [0.8.1] — 2026-08-02

### Fixed

- Prevented duplicate Clearance review-thread verdict replies when one submitted
  review fans out into a review event plus root review-comment events. Root
  comments now defer to the canonical submitted-review trigger, while complete
  Clearance automation runs are serialized per repository and pull request;
  unrelated pull requests remain concurrent
  ([#293](https://github.com/iterwheel/voyager/pull/293)).

### Operator notes

- The change is limited to Clearance webhook routing and in-process automation
  serialization. It introduces no configuration, environment-variable, model,
  or migration changes.
- Deploy v0.8.1 and restart long-running Voyager processes to activate the fix.
  Existing duplicate comments are not removed and may be cleaned up manually.
- Simultaneous Clearance automation across multiple event loops or operating
  system processes remains out of scope; serialization applies within one event
  loop and process.

## [0.8.0] — 2026-08-02

### Added — Governed Countdown review-thread resolution

- Added `vyg countdown resolve-conversation`, a resolve-only command that uses
  the fixed `iterwheel-countdown-bot` machine account without changing the
  operator's active `gh` identity. Repository allow-listing, a viewer-login
  identity check, a single allowed GraphQL mutation, and identifier redaction
  fail closed before any review thread is resolved. `--dry-run` and redacted
  JSON output are available for attended preflight
  ([#222](https://github.com/iterwheel/voyager/pull/222),
  [#242](https://github.com/iterwheel/voyager/pull/242)).
- Added `vyg countdown resolve-loop` for bounded multi-repository resolution.
  The loop applies a deterministic candidate filter before a veto-only DeepSeek
  gate, re-checks thread freshness, respects dry-run and maximum-resolution
  limits, prevents concurrent runs with a file lock, and writes a redacted audit
  trail. Gate errors, malformed output, stale evidence, and identity failures all
  skip resolution ([#224](https://github.com/iterwheel/voyager/pull/224)).
- Added default-off Wukong launchd artifacts and an adaptive scheduler for the
  resolve loop. Candidate-bearing runs use a bounded fast-poll streak; idle or
  failed runs back off to the slow interval. The wrapper re-loads its private
  environment on every iteration and fails closed when that file is unavailable
  or malformed. Runtime defaults are `COUNTDOWN_RESOLVE_LOOP_ENABLED=false`,
  `COUNTDOWN_MAX_RESOLVES=20`, `COUNTDOWN_FAST_INTERVAL=300`,
  `COUNTDOWN_SLOW_INTERVAL=3600`, and `COUNTDOWN_FAST_STREAK_MAX=6`
  ([#243](https://github.com/iterwheel/voyager/pull/243),
  [#280](https://github.com/iterwheel/voyager/pull/280)).
- Added `VOYAGER_RESOLVE_EXTRA_REPOS` for operator-local allow-list extension
  through comma- or whitespace-separated `owner/repo` values without checking
  private repository names into the repository. Invalid entries abort instead
  of being silently ignored. Keep the private environment file
  access-controlled: public loop summaries retain repository names while
  redacting PR/thread identifiers and reasons
  ([#273](https://github.com/iterwheel/voyager/pull/273)).

### Added — Codex review operations

- Replaced the shell-based Codex review watcher with a tested Python watcher
  that retries unacknowledged `@codex review` triggers, paginates GitHub results,
  and rejects review signals that predate the current trigger. Its stable exit
  codes are `0` for clean, `2` for findings, and `1` for error or timeout
  ([#244](https://github.com/iterwheel/voyager/pull/244)).

### Removed — Legacy Countdown credential tooling

- **Breaking:** removed the v0.7.x `review-thread-diagnostic`,
  `user-device-code`, and `user-refresh-check` CLI paths together with the
  GitHub App user-refresh-token machinery. They are superseded by the fixed
  machine-account `resolve-conversation` and `resolve-loop` commands
  ([#223](https://github.com/iterwheel/voyager/pull/223)).
- Operators upgrading from v0.7.3 must provision the
  `iterwheel-countdown-bot` credential in the local `gh` credential store. The
  resolver obtains it with `gh auth token --hostname github.com --user
  iterwheel-countdown-bot`; tokens are never accepted through CLI flags or
  printed in public output. Remove stale `[countdown.dedicated_pat_fallback]`
  configuration and retire obsolete PAT/OAuth material after confirming no
  other consumer still needs it.

### Changed — Dependency maintenance

- Allowed FastAPI releases through 0.138.2 by widening the supported dependency
  range from
  `>=0.136,<0.137.2` to `>=0.136,<0.138.3`
  ([#208](https://github.com/iterwheel/voyager/pull/208),
  [#245](https://github.com/iterwheel/voyager/pull/245)).

### Security and hardening

- Countdown scrubs ambient `GH_TOKEN` and `GITHUB_TOKEN` overrides, verifies the
  authenticated machine-account viewer even during dry-run, blocks
  cross-repository thread targeting, and refuses missing or ambiguous targets
  ([#222](https://github.com/iterwheel/voyager/pull/222)).
- The multi-repository loop frames review content as untrusted data, fails closed
  on truncated evidence or invalid LLM output, writes audit intent before
  mutation, rechecks comment freshness immediately before resolution, and counts
  failed approved attempts toward its safety cap
  ([#224](https://github.com/iterwheel/voyager/pull/224)).
- The adaptive scheduler clears stale environment values when reload fails and
  validates interval values to prevent busy loops and log storms
  ([#280](https://github.com/iterwheel/voyager/pull/280)).

### Operator notes

- Both new Countdown commands perform live mutations unless `--dry-run` is
  supplied explicitly. In particular, `resolve-conversation --pr` resolves all
  mechanically eligible threads and does not use the resolve loop's DeepSeek
  semantic gate; always run an attended dry-run first.
- The scheduled Countdown loop remains disabled until
  `COUNTDOWN_RESOLVE_LOOP_ENABLED=true` is set after the VOY-1835 dry-run and
  identity preflight. It also requires `VOYAGER_DEEPSEEK_API_KEY`; deploying the
  bridge alone does not opt an operator into automatic resolution. The optional
  `VOYAGER_DEEPSEEK_MODEL` defaults to `deepseek-v4-pro` for this new Countdown
  gate; the release does not change the existing Clearance profile selection.
- A repository added through `VOYAGER_RESOLVE_EXTRA_REPOS` must also appear in
  the resolve loop's `--repos` file. Review authors and bounded comment bodies
  are sent to DeepSeek, so private-repository rollout requires explicit privacy
  approval. Countdown audit JSONL and launchd logs currently have no automatic
  rotation.
- The checked-in scheduled-loop deployment templates are Wukong/macOS-specific
  (`launchd`, zsh, and `/Users/frank` paths); other hosts need an adapted
  service definition.
- This release requires no state or database migration. The release PR does not
  change Voyager's Clearance investigator model or default profile.

### Known limitations

- Clearance can still misclassify some negated follow-up phrases as RESOLVED,
  and `/clearance` does not yet apply an equivalent privileged-actor gate. Do
  not expose live Clearance writeback to untrusted contributors until
  [#249](https://github.com/iterwheel/voyager/issues/249) and
  [#253](https://github.com/iterwheel/voyager/issues/253) are resolved.
- Untrusted PR content can influence LLM-derived Clearance verdicts and
  review-fix contract structure. Keep those mutation paths limited to trusted
  repositories and actors pending
  [#254](https://github.com/iterwheel/voyager/issues/254).
- `/assembly` currently ignores unknown flags; use the exact `--dry-run`
  spelling and keep actor/repository allow-lists narrow until
  [#260](https://github.com/iterwheel/voyager/issues/260) is resolved.
- The Codex review watcher can miss a verdict that lands between its first
  trigger and retry cutoff, resulting in a false timeout
  ([#264](https://github.com/iterwheel/voyager/issues/264)).
- The release-readiness helper can accept a malformed historical CHANGELOG in
  one missing-heading case. The v0.8.0 release therefore also uses the release
  workflow's independent exact-heading and version-pin checks
  ([#269](https://github.com/iterwheel/voyager/issues/269)).

## [0.7.3] — 2026-06-23

### Added — Countdown refresh failure diagnostics ([#207](https://github.com/iterwheel/voyager/issues/207))

- Countdown user refresh checks now include safe HTTP failure diagnostics for
  GitHub refresh-token errors: response status, response content type, GitHub
  request id when present, and request-shape booleans for client secret,
  repository id, and refresh-token presence. Token values and response bodies
  remain redacted.

## [0.7.2] — 2026-06-23

### Added — Countdown user token tooling ([#204](https://github.com/iterwheel/voyager/issues/204), [#205](https://github.com/iterwheel/voyager/pull/205))

- Added safe CLI helpers for GitHub App user Device Flow authorization,
  including expected-viewer validation before storing the first refresh token
  and safe JSON metadata that reports only whether the viewer login is present
  and matches the expected operator.
- Extended the Device Flow token exchange to carry the target repository ID and
  updated the Countdown app documentation/config examples for the operator-run
  authorization path.
- Hardened refresh-token persistence to fail closed when `secret-store` is
  unavailable instead of writing a plaintext recovery fallback.

## [0.7.1] — 2026-06-21

### Fixed — Clearance manual-close reply dedupe ([#197](https://github.com/iterwheel/voyager/issues/197))

- Clearance now adds a dedicated manual-close marker when it verifies a review
  thread as resolved but lacks permission to call `resolveReviewThread`, so the
  same unresolved GitHub thread does not receive a fresh manual-close reminder
  on every new PR head while its semantic state is unchanged.
- The dedupe guard uses fresh review-thread comments and `createdAt`
  chronology, ignores normal `clearance-close-reason` evidence from true
  resolve paths, and allows a new manual-close reply after a
  RESOLVED -> OPEN -> RESOLVED state transition.

## [0.7.0] — 2026-06-20

### Added — Governed PR review-fix bot ([#187](https://github.com/iterwheel/voyager/issues/187))

- Voyager now routes explicit `/review-fix` and `/pr-review-fix` PR comments
  into a governed Assembly-backed review-fix loop, using the existing actor
  authorization gate and the L3 governance envelope instead of an unattended
  free-form repair path.
- The bot refuses unsafe runs before mutation: review-fix must be explicitly
  enabled in `[review_fix]`, must include an L3 envelope, must target a
  same-repository non-default branch PR, and never approves, resolves threads,
  merges, or rewrites the base/default branch.
- Review-fix execution now carries the expected PR head SHA into each Assembly
  adapter pass, refreshes that SHA after successful commits, checks for stale
  heads before pushing, and uses force-with-lease semantics so concurrent
  branch movement is escalated instead of overwritten.
- Codex/Clearance thread polling for the loop uses the Assembly App identity,
  ignores already-resolved/outdated or author-replied threads, treats backend
  `dry_run` and `no_changes` as non-fixes, and re-fetches review threads after
  each push before declaring a finding handled.
- Public writeback now upserts a review-fix progress/refusal comment, while
  private audit records keep per-round/per-finding attempt IDs for traceability.

### Added — Review-fix enablement config ([#183](https://github.com/iterwheel/voyager/issues/183))

- Voyager config now parses `[review_fix]` governance settings, including the
  L3 envelope limits, kill switch path, verify command, and review-fix audit
  directory documented in `config.example.toml`.

### Added — Review-fix audit log ([#184](https://github.com/iterwheel/voyager/issues/184))

- The review-fix governance layer now writes append-only JSONL audit records
  for findings, classifications, fix attempts, verification outcomes, rollback
  decisions, max-round escalation, kill-switch stops, and terminal loop status.
- Audit validation rejects malformed or unsafe records instead of silently
  accepting bare strings, missing safety fields, duplicate fixes, non-bool
  classifications, or invalid envelope state.

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

### Fixed — Assembly finding-direction gate ([#158](https://github.com/iterwheel/voyager/issues/158))

- Assembly auto-action is now gated on blocking-direction findings, so tolerated
  false negatives and non-blocking review signals do not accidentally drive
  automated fix behavior.

### Changed — Assembly loop convergence policy ([#168](https://github.com/iterwheel/voyager/issues/168))

- Assembly SOPs now reference the VOY-1825 convergence policy directly, making
  the operator path explicit for false-positive fixes, false-negative
  acceptance, and circuit-breaker stop conditions during multi-bot loops.

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

[Unreleased]: https://github.com/iterwheel/voyager/compare/v0.11.0...HEAD
[0.11.0]: https://github.com/iterwheel/voyager/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/iterwheel/voyager/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/iterwheel/voyager/compare/v0.8.2...v0.9.0
[0.8.2]: https://github.com/iterwheel/voyager/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/iterwheel/voyager/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/iterwheel/voyager/compare/v0.7.3...v0.8.0
[0.7.3]: https://github.com/iterwheel/voyager/compare/v0.7.2...v0.7.3
[0.7.2]: https://github.com/iterwheel/voyager/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/iterwheel/voyager/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/iterwheel/voyager/compare/v0.6.0...v0.7.0
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
