# REF-1807: GitHub App Registry

**Applies to:** VOY project
**Last updated:** 2026-06-22
**Last reviewed:** 2026-06-22
**Status:** Active
**Related:** VOY-1805, VOY-1806, VOY-1808

---

## What Is It?

This registry records the actual GitHub Apps created under the `iterwheel`
organization for the Voyager bot roster.

---

## Content

| App | App ID | Public page | App webhook active | Private key | Installed repositories |
|-----|--------|-------------|--------------------|-------------|------------------------|
| `iterwheel-blueprint` | `3646512` | `https://github.com/apps/iterwheel-blueprint` | No | Stored on Wukong: `~/github-openclaw-agent/secrets/iterwheel-blueprint.private-key.pem` | `iterwheel/voyager`, `iterwheel/voyager-sandbox` (`130630088`); `frankyxhl/alfred`, `frankyxhl/babs`, `frankyxhl/fx_bin`, `frankyxhl/sweeping-monk`, `frankyxhl/trinity` (`130696149`) |
| `iterwheel-stack` | `3646534` | `https://github.com/apps/iterwheel-stack` | No | Stored on Wukong: `~/github-openclaw-agent/secrets/iterwheel-stack.private-key.pem` | `iterwheel/voyager`, `iterwheel/voyager-sandbox` (`130630216`); `frankyxhl/alfred`, `frankyxhl/babs`, `frankyxhl/fx_bin`, `frankyxhl/sweeping-monk`, `frankyxhl/trinity` (`130716196`) |
| `iterwheel-staticfire` | `3646537` | `https://github.com/apps/iterwheel-staticfire` | No | Stored on Wukong: `~/github-openclaw-agent/secrets/iterwheel-staticfire.private-key.pem` | `iterwheel/voyager-sandbox` (`130630275`) |
| `iterwheel-clearance` | `3646538` | `https://github.com/apps/iterwheel-clearance` | No | Stored on Wukong: `~/github-openclaw-agent/secrets/iterwheel-clearance.private-key.pem` | `iterwheel/voyager`, `iterwheel/voyager-sandbox` (`130630338`) |
| `iterwheel-countdown` | `3646540` | `https://github.com/apps/iterwheel-countdown` | No | Stored on Wukong: `~/github-openclaw-agent/secrets/iterwheel-countdown.private-key.pem` | `iterwheel/voyager-sandbox` (`130630407`) |
| `iterwheel-assembly` | `3821103` | `https://github.com/apps/iterwheel-assembly` | No | Stored on Wukong: `~/.voyager/secrets/iterwheel-assembly.pem`; mirrored at `~/github-openclaw-agent/secrets/iterwheel-assembly.private-key.pem` | `iterwheel/voyager`, `iterwheel/voyager-sandbox` (`134829044`); `frankyxhl/alfred`, `frankyxhl/trinity` (`134830000`) |

Current repository event source:

| Repository | Webhook ID | URL | Active | Events | Last delivery state |
|------------|------------|-----|--------|--------|---------------------|
| `iterwheel/voyager-sandbox` | `619824421` | `https://gh.iterwheel.com/github/webhook` | Yes | `check_run`, `check_suite`, `issues`, `issue_comment`, `label`, `pull_request`, `pull_request_review`, `pull_request_review_comment`, `status`, `workflow_run` | `200 OK` |
| `iterwheel/voyager` | `619976821` | `https://gh.iterwheel.com/github/webhook` | Yes | `issues`, `issue_comment`, `pull_request`, `pull_request_review`, `pull_request_review_comment` | `200 OK` |
| `frankyxhl/alfred` | `619961538` | `https://gh.iterwheel.com/github/webhook` | Yes | `issues`, `issue_comment` | `200 OK` |
| `frankyxhl/babs` | `619961554` | `https://gh.iterwheel.com/github/webhook` | Yes | `issues`, `issue_comment` | `200 OK` |
| `frankyxhl/fx_bin` | `619961564` | `https://gh.iterwheel.com/github/webhook` | Yes | `issues`, `issue_comment` | `200 OK` |
| `frankyxhl/sweeping-monk` | `620063000` | `https://gh.iterwheel.com/github/webhook` | Yes | `issues`, `issue_comment` | `200 OK` |
| `frankyxhl/trinity` | `619959453` | `https://gh.iterwheel.com/github/webhook` | Yes | `issues`, `issue_comment` | `200 OK` |

Current bridge write-back:

| Agent | Repository scope | Trigger | Write-back |
|-------|------------------|---------|------------|
| `iterwheel-blueprint` | `iterwheel/voyager`, `iterwheel/voyager-sandbox`, `frankyxhl/alfred`, `frankyxhl/babs`, `frankyxhl/fx_bin`, `frankyxhl/sweeping-monk`, `frankyxhl/trinity` | `issues.opened`, `issues.edited`, `issues.reopened`, or `/blueprint` issue comment | Validates issue title format and intake fields, maintains exactly one Blueprint state label from `blueprint-needed`, `blueprint-ready`, and `blueprint-requests-revision`, upserts one Blueprint intake comment, and adds a `rocket` issue reaction when the issue is Blueprint-ready |
| `iterwheel-stack` | `iterwheel/voyager`, `iterwheel/voyager-sandbox`, `frankyxhl/alfred`, `frankyxhl/babs`, `frankyxhl/fx_bin`, `frankyxhl/sweeping-monk`, `frankyxhl/trinity` | `issues.opened`, `issues.edited`, `issues.reopened`, or `/stack` issue comment on a non-PR issue | Maintains one issue label from each Stack axis (`stack-type-*`, `stack-area-*`, `stack-size-*`, and `stack-risk-*`) when confident; otherwise applies `stack-needs-review`; upserts one Stack classification comment; adds `rocket` on successful issue classification and `eyes` when human review is needed |
| `iterwheel-clearance` | `iterwheel/voyager`, `iterwheel/voyager-sandbox` | `pull_request.opened`, `pull_request.edited`, `pull_request.reopened`, `pull_request.ready_for_review`, `pull_request.converted_to_draft`, `pull_request.synchronize`, `pull_request_review.submitted`, `pull_request_review.dismissed`, `pull_request_review_comment.*`, or `/clearance` PR comment | Maintains one PR review-readiness label from `clearance-1-pending`, `clearance-2-blocked`, `clearance-3-ready-for-approval`, and `clearance-4-ready-for-merge`; upserts one Clearance comment; adds `rocket` when ready and `eyes` otherwise |
| `iterwheel-countdown` | `iterwheel/voyager-sandbox` resolver canary only | Manual `vyg countdown review-thread-diagnostic` invocation. Production event wiring is deferred to a follow-up CHG. | Queries `PullRequestReviewThread.viewerCanResolve`, `viewerCanReply`, `isResolved`, and `isOutdated` as Countdown. With `--resolve`, calls `resolveReviewThread` only when the target thread belongs to the specified PR, is currently unresolved, and Countdown's live `viewerCanResolve=true`. |
| `iterwheel-assembly` | `iterwheel/voyager`, `iterwheel/voyager-sandbox`, `frankyxhl/alfred`, `frankyxhl/trinity` | `/assembly` or `/implement` issue comment on a `blueprint-ready` allow-listed issue. | When `ASSEMBLY_EXECUTION_BACKEND` produces commits, creates a `<issue#>-<slug>` branch ref on the source repo, opens or updates a PR with `Closes #N`, posts `@codex review` after each push, and upserts an Assembly progress comment on both the issue and the PR. Never merges, approves, resolves review threads, or applies Clearance/Countdown labels. Initial allow-list ships empty; `iterwheel/voyager-sandbox` is the intended first production target via `BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_ASSEMBLY`. Requires actor authorization per VOY-1818; default deny when `BRIDGE_ASSEMBLY_AUTHORIZED_ACTORS` and `BRIDGE_ASSEMBLY_AUTHORIZED_ASSOCIATIONS` are unset. Operators run the end-to-end issue-to-PR loop using VOY-1822. |

Cross-account installation:

| Account | Repository | Strategy | Status |
|---------|------------|----------|--------|
| `frankyxhl` | `frankyxhl/alfred`, `frankyxhl/babs`, `frankyxhl/fx_bin`, `frankyxhl/sweeping-monk`, `frankyxhl/trinity` | Reuse existing `iterwheel-*` Apps by making them installable on selected repositories outside the owning organization. | `iterwheel-blueprint` installed as selected-repository installation `130696149`; `iterwheel-stack` installed as selected-repository installation `130716196`; `iterwheel-assembly` installed on `frankyxhl/alfred` and `frankyxhl/trinity` as selected-repository installation `134830000`; Static Fire, Clearance, and Countdown remain sandbox-only |

Countdown resolver canary status:

| Check | Status | Evidence |
|-------|--------|----------|
| App exists | Complete | `iterwheel-countdown` App ID `3646540`; public page `https://github.com/apps/iterwheel-countdown`. |
| Current public App metadata | Complete with event caveat | `gh api /apps/iterwheel-countdown` on 2026-06-22 after the issue #202 rollback returned permissions `metadata: read`, `contents: read`, `issues: write`, `pull_requests: write`, `checks: write`, `actions: read`, and `statuses: read`; events `[]`. Earlier the same day, before the #202 permissions-page save, the public metadata returned events `check_run`, `check_suite`, `issue_comment`, `pull_request`, `pull_request_review`, `pull_request_review_comment`, `status`, and `workflow_run`. App webhooks are inactive with an empty webhook URL, and the GitHub App settings UI hid event checkboxes while saving the permission rollback, so the repository-level webhook remains the current bootstrap event source. |
| Installation scope | Verified | On 2026-06-22, the Countdown installation token listed `total_count=1`, `repository_selection=selected`, repositories `iterwheel/voyager-sandbox`. Do not broaden Countdown to all repositories for issue #200 or #202. |
| Operator credentials | Available on Wukong; absent locally | `/Users/frank/.voyager/config.toml`, `./voyager.toml`, `/etc/voyager/config.toml`, and local Countdown PEM paths were absent on this workstation on 2026-06-22. Wukong has `~/.voyager/secrets/iterwheel-countdown.pem`; the live canary used a one-off in-memory `AppConfig` so App ID, installation ID, and private key material stayed in the operator secret path and were not committed. |
| Capability query canary | Complete-negative | On 2026-06-22, Countdown queried a private sandbox PR review thread as actor `iterwheel-countdown[bot]`. Response: type `PullRequestReviewThread`, repo `iterwheel/voyager-sandbox`, `isResolved=false`, `isOutdated=false`, `viewerCanResolve=false`, `viewerCanReply=true`. An existing sandbox canary thread also returned `viewerCanResolve=false`. This is not resolver-capable evidence. Keep private PR numbers and thread node IDs in operator notes, not this public registry. |
| Permission escalation check | Complete-negative; permission rollback complete | Issue #202 tested the narrowest adjacent escalation because Pull requests was already `read & write` and has no higher level. Baseline App metadata was `contents: read`; selected installation scope was only `iterwheel/voyager-sandbox`; canary target was a private sandbox PR with a private sandbox `PullRequestReviewThread` node. Baseline diagnostic: actor `iterwheel-countdown[bot]`, type `PullRequestReviewThread`, `isResolved=false`, `isOutdated=false`, `viewerCanResolve=false`, `viewerCanReply=true`. After GitHub sudo/operator approval, `Contents` was temporarily changed to `read & write`; the repeated diagnostic returned the same flags, including `viewerCanResolve=false`. `Contents` was then rolled back to `read-only`; final diagnostic again returned `viewerCanResolve=false`, `viewerCanReply=true`, `isResolved=false`, `isOutdated=false`. The sandbox PR was closed and its temporary branch was deleted after evidence collection. |
| Resolve canary | Blocked by capability bit | No `resolveReviewThread` mutation was run because Countdown's live `viewerCanResolve=false` in baseline, elevated, and rollback states. |

Operational notes:

- `iterwheel-blueprint` was first created as `iw-blueprint`, then renamed after
  `iw-stack` collided with an existing GitHub account.
- `iterwheel-blueprint` and `iterwheel-stack` are public so they can be
  installed outside the `iterwheel` organization, but they are not
  Marketplace-listed.
- App webhooks remain disabled. Enabling them after creation did not persist in
  the GitHub UI, and the App hook configuration API returned no hook entity for
  apps that were originally created webhook-disabled.
- Issue #202 exposed a GitHub App settings UI hazard: saving Countdown
  permissions while App webhooks were inactive and the webhook URL was empty
  cleared the public App metadata `events` list. This did not affect the
  current repository-level webhook event source, but operators must re-check
  `.events` after future App settings saves and re-enable per-app event
  subscriptions only as part of a deliberate App webhook activation task.
- A repository-level webhook is the current bootstrap event source for
  allow-listed repositories. The five GitHub Apps still provide the per-agent
  write-back identities and permission boundaries.
- The local bridge is listening on Wukong at `127.0.0.1:8787`, and
  `https://gh.iterwheel.com/healthz` succeeds through the Cloudflare tunnel.
- The bridge acknowledges signed GitHub webhooks before performing GitHub
  write-back. Route processing, label/comment/reaction writes, and enriched
  metadata are handled in a FastAPI background task so GitHub delivery logs do
  not time out while the Apps write back.
- The bridge now runs with `BRIDGE_DRY_RUN=false` for
  explicitly allow-listed repositories. Installation access tokens are
  generated on demand in memory and are not written to disk.
- Each app has exactly one active private key in GitHub. Earlier uncaptured keys
  from the first browser automation attempt were deleted.
- Private key files are stored outside git on Wukong with `600` file
  permissions. Local downloaded `.pem` copies were removed after transfer.
- Repository installation started with the selected private test repository
  `iterwheel/voyager-sandbox`. Stack now uses the same seven-repository rollout
  scope as Blueprint.
- Stack label setup is complete on all seven rollout repositories. Each has the
  27-label set: `stack-type-*`, `stack-area-*`, `stack-size-*`,
  `stack-risk-*`, and `stack-needs-review`.
- Stack v2 is active in the bridge. It prefers explicit issue-body fields such
  as `Work Type`, `Stack Type`, and `Stack Area`, then uses weighted area
  signals so long issues do not fall into `stack-needs-review` only because they
  mention many generic terms.
- Assembly App creation is tracked in issue #68. `iterwheel-assembly` is public
  and installed only on selected repositories: `iterwheel/voyager`,
  `iterwheel/voyager-sandbox`, `frankyxhl/alfred`, and `frankyxhl/trinity`.
  The App follows the VOY-1806 least-privilege matrix: Contents write granted
  as the sole exception, merge prohibited by branch protection, and no
  implementation route active until issue #69 lands.
- VOY-1821 adds `ASSEMBLY_EXECUTION_BACKEND=fake-subprocess` as a deterministic
  Assembly dispatcher verification backend under the existing
  `iterwheel-assembly` App; it does not create a new GitHub App. The real
  `pi-oh-my-pi-deepseek` backend invokes the Oh My Pi CLI as `omp -p` and
  remains explicitly approved and limited to `iterwheel/voyager-sandbox`; it
  must not be enabled for `iterwheel/voyager`, `frankyxhl/alfred`, or
  `frankyxhl/trinity`.
- VOY-1822 is the operator SOP for running an Assembly-driven implementation
  from a ready issue through PR verification, Clearance, human approval, merge,
  and completion-gate checks. Future Assembly audit manifests and failure
  diagnostics should cite VOY-1822 as the implementation-loop entry point.
- Clearance v1 is active for `iterwheel/voyager` and
  `iterwheel/voyager-sandbox`. It verifies current GitHub review state and
  review-thread resolution, but does not claim AI-level semantic repair
  verification. Both repositories use four numbered Clearance labels:
  `clearance-1-pending`, `clearance-2-blocked`,
  `clearance-3-ready-for-approval`, and `clearance-4-ready-for-merge`.
  Legacy labels (`clearance-pending`, `clearance-blocked`, `clearance-ready`)
  are removed on every write-back as part of the migration (issue #25).
- Countdown resolver capability is diagnostic-only for issue #200. The local
  command exists so operators can prove the live GitHub GraphQL capability bit
  before any Clearance-to-Countdown production handoff is designed.

---

## Change History

| Date       | Change                                                                                                            | By               |
|------------|-------------------------------------------------------------------------------------------------------------------|------------------|
| 2026-05-09 | Initial version - recorded created Iterwheel GitHub Apps and current activation state                             | Frank Xu + Codex |
| 2026-05-09 | Generated private keys, stored them on Wukong, removed local downloaded copies, and deleted uncaptured stale keys | Frank Xu + Codex |
| 2026-05-09 | Installed all five Apps on `iterwheel/voyager-sandbox` and recorded repository webhook bootstrap state            | Frank Xu + Codex |
| 2026-05-09 | Enabled `iterwheel-blueprint` issue label/comment write-back for the sandbox repository                           | Frank Xu + Codex |
| 2026-05-09 | Recorded planned cross-account installation path for `frankyxhl/trinity`                                          | Frank Xu + Codex |
| 2026-05-09 | Made `iterwheel-blueprint` public, installed it on selected `frankyxhl` repositories, and verified `trinity` #77  | Frank Xu + Codex |
| 2026-05-09 | Added repository webhooks and Blueprint labels for `frankyxhl/alfred`, `frankyxhl/babs`, and `frankyxhl/fx_bin`   | Frank Xu + Codex |
| 2026-05-09 | Enabled Blueprint ready-state `rocket` issue reactions and verified it on `frankyxhl/trinity` #77                 | Frank Xu + Codex |
| 2026-05-09 | Added `iterwheel/voyager` to Blueprint installation, webhook allow-list, and issue title validation smoke test    | Frank Xu + Codex |
| 2026-05-09 | Standardized Blueprint issue-state labels and removed the older `needs-blueprint` name from the registry          | Frank Xu + Codex |
| 2026-05-09 | Tightened Blueprint write-back so only one Blueprint state label is active at a time                              | Frank Xu + Codex |
| 2026-05-09 | Added Stack v1 sandbox write-back scope for deterministic classification labels and `eyes` reactions              | Frank Xu + Codex |
| 2026-05-09 | Added Stack low-confidence `stack-needs-review`, upserted comments, and success `rocket` reactions                | Frank Xu + Codex |
| 2026-05-09 | Expanded `iterwheel-stack` to the six-repository Blueprint rollout scope and verified all Stack labels            | Frank Xu + Codex |
| 2026-05-09 | Tightened Stack routing to issue-only classification and ignored pull request events/comments                     | Frank Xu + Codex |
| 2026-05-09 | Deployed Stack v2 weighted classification after `frankyxhl/trinity` #88 exposed over-broad area matching          | Frank Xu + Codex |
| 2026-05-09 | Added Clearance v1 sandbox PR review-readiness routing, labels, and smoke test on `iterwheel/voyager-sandbox` #5 | Frank Xu + Codex |
| 2026-05-09 | Added `frankyxhl/sweeping-monk` to Blueprint and Stack, webhook `620063000`, and smoke test issue #4              | Frank Xu + Codex |
| 2026-05-09 | Changed the bridge webhook path to ACK first and perform GitHub write-back in a background task                   | Frank Xu + Codex |
| 2026-05-17 | Updated Clearance label set to four numbered labels; added legacy-migration note (issue #25)                      | Claude Code      |
| 2026-05-17 | Replaced legacy label names in main registry table write-back row with four numbered clearance labels (issue #25) | Claude Code      |
| 2026-05-17 | Recorded Clearance activation on `iterwheel/voyager` after PR #36 showed live Clearance readiness panels on the main repository. | Codex |
| 2026-05-23 | Added Assembly placeholder registry row (App not yet created; governed by VOY-1805 boundaries and VOY-1806 permission matrix) | DeepSeek (via VOY-1811) |
| 2026-05-23 | Added `iterwheel-assembly` placeholder row to main app table | DeepSeek (via VOY-1811) |
| 2026-05-23 | Updated Assembly placeholder with operator creation instructions; added bridge write-back row, operational note, and config template (issue #68) | DeepSeek (via VOY-1811) |
| 2026-05-23 | Recorded created `iterwheel-assembly` App `3821103`, Wukong PEM paths, and selected-repository installations `134829044` and `134830000` for issue #68 | Codex |
| 2026-05-23 | Activated Assembly bridge write-back row per CHG-1817 (issue #69 first safe MVP): documented `/assembly` / `/implement` trigger, branch + PR + `@codex review` + progress-comment write-back, and operator-controlled `BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_ASSEMBLY` allow-list. | Claude (via VOY-1811) |
| 2026-05-23 | Added actor authorization requirement (VOY-1818) to `iterwheel-assembly` write-back row | Claude (via VOY-1811 #76) |
| 2026-05-23 | Documented VOY-1821 `fake-subprocess` Assembly backend selection under the existing `iterwheel-assembly` App and the sandbox-only gate for the later real OMP canary | Codex |
| 2026-05-23 | Recorded that the real Assembly backend uses the Oh My Pi CLI command `omp -p` and remains sandbox-only for the first canary | Codex |
| 2026-05-24 | Linked the Assembly write-back row and operational notes to VOY-1822, the Assembly-driven implementation-loop SOP | Codex |
| 2026-06-22 | Recorded Countdown resolver diagnostic command, public App metadata evidence, and pending canary evidence requirements for issue #200 | Codex |
| 2026-06-22 | Recorded issue #202 Countdown `Contents: read & write` permission canary: `viewerCanResolve` stayed false, no resolve mutation ran, Contents was rolled back to read-only, and inactive App webhook event subscriptions were cleared by the GitHub settings save | Codex |
