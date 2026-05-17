# ADR-1808: Cross Account Installation for Iterwheel GitHub Apps

**Applies to:** VOY project
**Last updated:** 2026-05-09
**Last reviewed:** 2026-05-09
**Status:** Accepted
**Related:** VOY-1805, VOY-1806, VOY-1807

---

## What Is It?

This ADR records the decision to use the existing `iterwheel-*` GitHub Apps
across both the `iterwheel` organization and selected personal repositories
owned by `frankyxhl`, instead of creating a duplicate bot fleet under the
personal account.

---

## Context

The first working deployment uses five GitHub Apps owned by the `iterwheel`
organization:

- `iterwheel-blueprint`
- `iterwheel-stack`
- `iterwheel-staticfire`
- `iterwheel-clearance`
- `iterwheel-countdown`

These Apps are installed on the private sandbox repository
`iterwheel/voyager-sandbox`. The bridge on Wukong can receive repository
webhooks, validate issue intake fields, exchange `iterwheel-blueprint` private
key material for installation access tokens, and write issue comments and labels
back as `iterwheel-blueprint[bot]`.

The first personal-account repository is `frankyxhl/trinity`, which is
currently:

| Field | Value |
|-------|-------|
| Repository | `frankyxhl/trinity` |
| Owner | `frankyxhl` personal account |
| Visibility | Public |
| Default branch | `main` |
| Current repository webhooks | `https://gh.iterwheel.com/github/webhook` |

Because this repository is not owned by the `iterwheel` organization, the
existing Apps cannot simply be added to the current `iterwheel` installation if
they remain limited to the creating account only.

Options considered:

| Option | Summary | Decision |
|--------|---------|----------|
| Transfer repository to `iterwheel` | Move `frankyxhl/trinity` to the organization, then add it to the existing App installation. | Rejected for now. Clean long-term for organization-owned projects, but too much ownership movement for the first personal-repo test. |
| Reuse existing `iterwheel-*` Apps across accounts | Make the existing Apps installable on other accounts controlled by Frank, then install them on selected `frankyxhl` repositories. | Accepted. Preserves one public bot identity set while avoiding duplicate private keys and duplicated permissions. |
| Create duplicate `frankyxhl-*` Apps | Recreate the whole bot fleet under the personal account. | Rejected for now. Doubles operational state without a clear identity benefit. |


## Decision

Use the existing `iterwheel-*` GitHub Apps as the canonical public bot
identities for both organization and selected personal repositories.

Specifically:

1. Do not create a second bot fleet under `frankyxhl` unless a future ADR
   identifies a strong identity or security reason.
2. Change the existing Apps so they can be installed on accounts controlled by
   Frank, not only on the owning `iterwheel` organization.
3. Install the Apps only on explicitly selected repositories, starting with
   `frankyxhl/trinity`.
4. Keep the current repository-level webhook bootstrap model until GitHub App
   webhooks are proven reliable for these Apps.
5. Extend the bridge from a single installation id per App to an installation
   mapping keyed by repository owner or repository full name.
6. Keep write-back allow-listed by repository. The bridge must reject write-back
   for repositories that are not explicitly listed in
   `BRIDGE_ALLOWED_REPOSITORIES`.

The intended bridge configuration shape is:

```json
{
  "slug": "iterwheel-blueprint",
  "app_id": "3646512",
  "installations": {
    "iterwheel": "130630088",
    "frankyxhl": "130696149"
  }
}
```

The current Blueprint and Stack write-back allow-lists are app-specific:

```text
BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_BLUEPRINT=iterwheel/voyager-sandbox,iterwheel/voyager,frankyxhl/trinity,frankyxhl/alfred,frankyxhl/babs,frankyxhl/fx_bin,frankyxhl/sweeping-monk
BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_STACK=iterwheel/voyager-sandbox,iterwheel/voyager,frankyxhl/trinity,frankyxhl/alfred,frankyxhl/babs,frankyxhl/fx_bin,frankyxhl/sweeping-monk
```

The first personal-repository rollout started with Blueprint only:

- issue field validation
- issue title validation
- `blueprint-needed`, `blueprint-ready`, and
  `blueprint-requests-revision` labels
- upserted Blueprint intake comment

After sandbox verification, Stack may use the same selected-repository rollout
scope as Blueprint. Static Fire, Clearance, and Countdown should not write back
to `frankyxhl/trinity` until their routing and write-back behavior are proven in
the sandbox.

Blueprint ready-state write-back also adds a `rocket` issue reaction from
`iterwheel-blueprint[bot]`. If a later validation changes the issue back to
`blueprint-requests-revision`, the bridge removes the bot's own `rocket`
reaction. Blueprint state labels are mutually exclusive: a failed check keeps
`blueprint-requests-revision`, a passed check keeps `blueprint-ready`, and
`blueprint-needed` remains the entry/backlog marker for issues that have not yet
completed an initial Blueprint pass.


## Implementation Status

| Item | Status |
|------|--------|
| Make `iterwheel-blueprint` installable outside `iterwheel` | Complete. The App is public but not Marketplace-listed. |
| Install `iterwheel-blueprint` on selected `frankyxhl` repositories | Complete. Installation id is `130696149`; selected repositories are `frankyxhl/alfred`, `frankyxhl/babs`, `frankyxhl/fx_bin`, `frankyxhl/sweeping-monk`, and `frankyxhl/trinity`. |
| Extend bridge config to multiple installation ids | Complete for `iterwheel-blueprint`: `iterwheel` maps to `130630088`, `frankyxhl` maps to `130696149`. |
| Add `frankyxhl/trinity` repository webhook | Complete. Webhook id is `619959453`; subscribed events are `issues` and `issue_comment`; latest response is `200 OK`. |
| Smoke test on `frankyxhl/trinity` | Complete. `/blueprint` on issue #77 first produced the non-ready Blueprint state; after the issue body was completed, the bot updated its comment and applied `blueprint-ready`. |
| Add ready-state rocket reaction | Complete. A signed replay of issue #77 added a `rocket` reaction from `iterwheel-blueprint[bot]`. |
| Enable repository webhooks for `frankyxhl/alfred`, `frankyxhl/babs`, and `frankyxhl/fx_bin` | Complete. Webhook ids are `619961538`, `619961554`, and `619961564`; each ping delivery returned `200 OK`. |
| Add Blueprint labels to `frankyxhl/alfred`, `frankyxhl/babs`, and `frankyxhl/fx_bin` | Complete. Each repository has `blueprint-needed`, `blueprint-ready`, and `blueprint-requests-revision`. |
| Add `iterwheel/voyager` to Blueprint | Complete. The repository is part of installation `130630088`, has webhook `619976821`, and issue #1 passed title/intake validation. |
| Make `iterwheel-stack` installable outside `iterwheel` | Complete. The App is public but not Marketplace-listed. |
| Install `iterwheel-stack` on selected `frankyxhl` repositories | Complete. Installation id is `130716196`; selected repositories are `frankyxhl/alfred`, `frankyxhl/babs`, `frankyxhl/fx_bin`, `frankyxhl/sweeping-monk`, and `frankyxhl/trinity`. |
| Extend bridge config to multiple installation ids for Stack | Complete for `iterwheel-stack`: `iterwheel` maps to `130630216`, `frankyxhl` maps to `130716196`. |
| Keep Stack routing issue-only | Complete. Stack ignores `pull_request` events and `/stack` comments on pull request conversations; PR convention checks belong to Countdown. |
| Upgrade Stack classification to v2 | Complete. Stack now parses explicit `Work Type` / `Stack Area` body fields before using weighted area scoring. |
| Add Stack labels to rollout repositories | Complete. Each of the seven rollout repositories has 27 Stack labels across type, area, size, risk, and review-state axes. |
| Add `frankyxhl/sweeping-monk` to Blueprint and Stack | Complete. The repository has webhook `620063000`, all Blueprint/Stack labels, app-specific allow-list entries on Wukong, and smoke test issue #4 passed with `blueprint-ready` plus Stack v2 classification labels. |


## Consequences

### Positive

- Preserves one canonical public bot identity set:
  `iterwheel-blueprint[bot]`, `iterwheel-stack[bot]`, and so on.
- Avoids duplicating private keys, App IDs, permission matrices, and runbook
  state under `frankyxhl`.
- Allows selected personal repositories to benefit from the same Voyager
  automation pipeline.
- Keeps repository expansion explicit through installation selection and bridge
  allow-lists.

### Negative / Trade-offs

- Making Apps installable beyond the owning organization increases public
  surface area. The bridge must treat every incoming webhook as untrusted until
  signature, repository allow-list, and route checks pass.
- Each GitHub App can have multiple installation IDs. The bridge can no longer
  assume one installation id per App.
- Operational registry updates become more important: every account/repository
  installation must be recorded with its installation id and enabled behavior.
- If an external account installs a public App, the bridge must not write back
  unless the repository is allow-listed.

### Implementation Notes

For a new personal repository, perform these steps:

1. Confirm the repository is part of the selected-repository installation for
   each App being enabled, for example `iterwheel-blueprint` or
   `iterwheel-stack`.
2. Add the repository to each enabled App-specific allow-list, such as
   `BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_BLUEPRINT` or
   `BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_STACK`. Keep the global
   `BRIDGE_ALLOWED_REPOSITORIES` narrow unless a route intentionally relies on
   the fallback allow-list.
3. Record each new installation id in VOY-1807.
4. Add a repository webhook on the target repository pointing to
   `https://gh.iterwheel.com/github/webhook`.
5. Add or verify labels for the enabled App. Blueprint uses
   `blueprint-needed`, `blueprint-ready`, and
   `blueprint-requests-revision`; Stack uses the 27-label
   `stack-type-*`, `stack-area-*`, `stack-size-*`, `stack-risk-*`, and
   `stack-needs-review` set.
6. Add or copy the issue template if Blueprint form-based intake is desired.
   After the Voyager template is validated, copy
   `.github/ISSUE_TEMPLATE/iterwheel_issue.md` to managed repositories such as
   `frankyxhl/alfred` and `frankyxhl/trinity`; keep its allowed Stack Type and
   Stack Area values synchronized with `voyager/bots/stack/constants.py`.
7. Run a smoke test on a non-critical issue. Opening a complete issue should
   exercise both Blueprint and Stack; `/blueprint` and `/stack` comments can be
   used for targeted rechecks.

### Triggers for Revisiting

Write a new ADR if:

- Personal repositories need a distinct bot identity from organization
  repositories.
- A customer or third-party account needs to install these Apps.
- The bridge begins using App webhooks instead of repository-level webhooks.
- Direct merge authority or production-write authority is proposed for a
  personal repository.

---

## Change History

| Date       | Change                                                                                                    | By               |
|------------|-----------------------------------------------------------------------------------------------------------|------------------|
| 2026-05-09 | Initial version - chose cross-account installation for existing `iterwheel-*` Apps over duplicate Apps    | Frank Xu + Codex |
| 2026-05-09 | Recorded first `frankyxhl` selected-repository installation and `trinity` Blueprint smoke test            | Frank Xu + Codex |
| 2026-05-09 | Added Blueprint event-source webhooks and labels for the remaining selected `frankyxhl` repositories      | Frank Xu + Codex |
| 2026-05-09 | Added Blueprint ready-state `rocket` issue reaction behavior                                              | Frank Xu + Codex |
| 2026-05-09 | Standardized Blueprint labels as `blueprint-needed`, `blueprint-ready`, and `blueprint-requests-revision` | Frank Xu + Codex |
| 2026-05-09 | Tightened Blueprint labels so a checked issue keeps only one Blueprint state label at a time              | Frank Xu + Codex |
| 2026-05-09 | Expanded Stack to the same six-repository selected rollout scope as Blueprint after sandbox verification  | Frank Xu + Codex |
| 2026-05-09 | Tightened Stack to issue-only classification; pull request checks remain Countdown scope                  | Frank Xu + Codex |
| 2026-05-09 | Recorded Stack v2 explicit-field and weighted-area classifier behavior                                   | Frank Xu + Codex |
| 2026-05-09 | Added `frankyxhl/sweeping-monk` to the Blueprint and Stack selected rollout                              | Frank Xu + Codex |
