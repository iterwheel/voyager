# REF-1807: GitHub App Registry

**Applies to:** VOY project
**Last updated:** 2026-05-17
**Last reviewed:** 2026-05-17
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
| `iterwheel-assembly` | _(pending)_ | _(pending)_ | No | _(pending)_ | _(pending)_ |

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

Cross-account installation:

| Account | Repository | Strategy | Status |
|---------|------------|----------|--------|
| `frankyxhl` | `frankyxhl/alfred`, `frankyxhl/babs`, `frankyxhl/fx_bin`, `frankyxhl/sweeping-monk`, `frankyxhl/trinity` | Reuse existing `iterwheel-*` Apps by making them installable on selected repositories outside the owning organization. | `iterwheel-blueprint` installed as selected-repository installation `130696149`; `iterwheel-stack` installed as selected-repository installation `130716196`; Static Fire, Clearance, and Countdown remain sandbox-only |

Operational notes:

- `iterwheel-blueprint` was first created as `iw-blueprint`, then renamed after
  `iw-stack` collided with an existing GitHub account.
- `iterwheel-blueprint` and `iterwheel-stack` are public so they can be
  installed outside the `iterwheel` organization, but they are not
  Marketplace-listed.
- App webhooks remain disabled. Enabling them after creation did not persist in
  the GitHub UI, and the App hook configuration API returned no hook entity for
  apps that were originally created webhook-disabled.
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
- Clearance v1 is active for `iterwheel/voyager` and
  `iterwheel/voyager-sandbox`. It verifies current GitHub review state and
  review-thread resolution, but does not claim AI-level semantic repair
  verification. Both repositories use four numbered Clearance labels:
  `clearance-1-pending`, `clearance-2-blocked`,
  `clearance-3-ready-for-approval`, and `clearance-4-ready-for-merge`.
  Legacy labels (`clearance-pending`, `clearance-blocked`, `clearance-ready`)
  are removed on every write-back as part of the migration (issue #25).

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
