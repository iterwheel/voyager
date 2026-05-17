# SOP-1816: Managed Repository Canary Expansion

**Applies to:** Voyager managed-repository rollout planning
**Last updated:** 2026-05-18
**Last reviewed:** 2026-05-18
**Status:** Active
**Related:** VOY-1807 (GitHub App Registry), VOY-1808 (Cross Account Installation), VOY-1813 (Clearance writeback failure visibility), VOY-1814 (Wukong Bridge Launchd and Rollback), issue #48

---

## What Is It?

The staged canary expansion SOP for adding more repositories to Voyager's
managed bridge scope. It defines the candidate order, preflight checks,
per-bot enablement plan, validation checklist, and rollback steps. It does not
itself expand or narrow the Wukong allow-list.

## Why

Voyager currently operates on a small canary set. Adding repositories without a
preflight gate risks missing labels, missing GitHub App installation access,
bad webhook routing, branch-protection surprises, or noisy writeback failures.
Expansion must happen one repository at a time, with rollback ready before each
allow-list edit.

Issue #48 also depends on the hardening work from issue #45 and issue #44:
writeback failures must be visible, and the bridge must have an operator
restart/rollback path before broader repository coverage.

Merge order matters: issue #44 / PR #50, which introduces the VOY-1814 launchd
and rollback runbook, must merge before this SOP is merged or used.

Historical records in VOY-1807 and VOY-1808 may list broader selected-repository
installation, webhook, label, or allow-list state for `frankyxhl/babs`,
`frankyxhl/fx_bin`, and `frankyxhl/sweeping-monk`. Treat those records as
inventory inputs, not proof that the repository is in the current verified
canary. Before any rollout action, reconcile this SOP with the live Wukong
allow-list values and record whether the repository is being added, validated as
already enabled, narrowed out, or deferred.

## When to Use

- Planning the next managed repository after the current canary.
- Adding one repository to Blueprint, Stack, or Clearance bridge scope.
- Validating labels, webhooks, installations, branch protection, and rollback
  before changing Wukong allow-lists.
- Recording why a repository was included, deferred, or excluded.

## When NOT to Use

- Emergency rollback of the existing canary; use the rollback section directly.
- Changing GitHub App permission grants.
- Enabling Static Fire or Countdown. They remain excluded unless a later issue
  explicitly selects them.
- Bulk-adding repositories. This SOP is intentionally one repo per validation
  cycle.

## Steps

### 1. Confirm Prerequisites

Do not expand beyond the current canary until these gates are complete:

- Issue #45 / PR #49 writeback failure observability is merged and deployed.
- Issue #44 / PR #50 launchd and rollback runbook is merged, and the Wukong
  bridge restart path has been verified.
- `curl -fsS http://127.0.0.1:8787/healthz` returns `dry_run: false`.
- Current canary repositories still produce successful Blueprint and Stack
  writebacks.
- No open P0/P1 Clearance writeback failure is unresolved for
  `iterwheel/voyager`.

### 2. Use This Candidate Order

Current verified canary set for issue #48, no expansion implied by this SOP:

1. `iterwheel/voyager`
2. `frankyxhl/alfred`
3. `frankyxhl/trinity`

Expansion candidates after live allow-list reconciliation:

1. `frankyxhl/babs` if it is not already active in the live app-specific
   allow-lists; if it is already active, validate it as an existing rollout
   entry instead of adding it again.
2. `frankyxhl/screen-harness`

Explicitly excluded:

- `frankyxhl/sweeping-monk` is not a target for this rollout. If live Wukong
  allow-lists still include it from earlier VOY-1808 work, do not treat that as
  approval to expand or smoke-test it under this SOP; create a separate
  rollback/narrowing record if removal is required.

Deferred until separately approved:

- `frankyxhl/fx_bin`.
- Any repository not listed above, including previously documented or
  experimental repositories.

### 3. Run Per-Repository Preflight

For each candidate, complete this checklist before editing any Wukong
allow-list:

- Inventory the live app-specific Wukong allow-list values and compare them to
  VOY-1807, VOY-1808, and this SOP.
- Confirm the repository exists, visibility is expected, and default branch is
  `main`.
- Confirm selected-repository GitHub App installation access for each enabled
  bot:
  - `iterwheel-blueprint`
  - `iterwheel-stack`
  - `iterwheel-clearance` only if Clearance is enabled for that repository
- Confirm required labels exist:
  - Blueprint: `blueprint-needed`, `blueprint-ready`,
    `blueprint-requests-revision`
  - Stack: all `stack-type-*`, `stack-area-*`, `stack-size-*`,
    `stack-risk-*`, and `stack-needs-review`
  - Clearance, only if enabled: numbered Clearance labels from VOY-1805
- Confirm a repository webhook points to
  `https://gh.iterwheel.com/github/webhook`.
- Confirm webhook events include `issues` and `issue_comment` for Blueprint and
  Stack. Add PR/review events only for repositories selected for Clearance.
- Confirm the latest webhook ping or test delivery returns `200 OK`.
- Inspect default-branch protection, required reviews, required status checks,
  and fork/permission constraints.
- Prepare one complete test issue and, if Clearance is selected, one test PR.
- Record the rollback command and the exact allow-list variable to edit.

### 4. Enable Bots Conservatively

Default expansion for `babs` and `screen-harness`:

| Bot | Enablement | Notes |
|-----|------------|-------|
| Blueprint | Yes | Issue intake, labels, comment, and ready-state reaction. |
| Stack | Yes | Issue-only classification and one label per Stack axis. |
| Clearance | Optional second phase | Enable only after PR labels, review events, and branch protection are verified. |
| Static Fire | No | Excluded until explicitly selected. |
| Countdown | No | Excluded until explicitly selected. |

### 5. Add One Repository

After preflight passes and the operator approves expansion:

1. Edit `/Users/frank/.voyager/bridge.env` on Wukong.
2. Add the repository only to the app-specific allow-list variables for the
   selected bots, for example:

   ```bash
   BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_BLUEPRINT=iterwheel/voyager,frankyxhl/alfred,frankyxhl/trinity,frankyxhl/babs
   BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_STACK=iterwheel/voyager,frankyxhl/alfred,frankyxhl/trinity,frankyxhl/babs
   ```

3. Leave `BRIDGE_ALLOWED_REPOSITORIES` unset unless a later issue explicitly
   chooses the global fallback.
4. Restart the bridge with the VOY-1814 launchd command:

   ```bash
   launchctl kickstart -kp gui/$(id -u)/com.iterwheel.voyager.bridge
   ```

5. Verify health:

   ```bash
   curl -fsS http://127.0.0.1:8787/healthz
   curl -fsS https://gh.iterwheel.com/healthz
   ```

6. Open the test issue, then run `/blueprint` and `/stack` comments if a
   targeted recheck is needed.
7. If Clearance is enabled, open a test PR and verify the readiness panel.
8. Wait for one clean cycle before moving to the next repository.

### 6. Record Validation

Use this checklist per repository:

```text
Repository:
Candidate wave:
Enabled bots:
GitHub App installations verified:
Labels verified:
Webhook id:
Webhook events:
Latest delivery status:
Allow-list variables edited:
Bridge restart verified:
Test issue:
Test PR, if Clearance enabled:
Rollback tested or dry-run verified:
Decision:
```

### 7. Roll Back One Repository

Rollback removes the repository from Wukong writeback scope and verifies denied
routes. Do not change GitHub App permissions during first rollback; keep the
external installation state intact until the incident is understood.

1. Remove the repository from each app-specific allow-list in
   `/Users/frank/.voyager/bridge.env`.
2. Restart the bridge:

   ```bash
   launchctl kickstart -kp gui/$(id -u)/com.iterwheel.voyager.bridge
   ```

3. Verify bridge health:

   ```bash
   curl -fsS http://127.0.0.1:8787/healthz
   curl -fsS https://gh.iterwheel.com/healthz
   ```

4. Trigger or replay a safe issue event for the rolled-back repository.
5. Verify the route is denied:
   - no Blueprint, Stack, or Clearance labels are written;
   - no bot marker comment is created or updated;
   - Wukong logs show `repository_allowlist_denied` for that repository.
6. If webhook noise must stop immediately, disable or remove the repository
   webhook after the allow-list rollback is verified.

## Examples

### Example: Add `frankyxhl/babs` for Blueprint and Stack

1. Complete the preflight checklist for `frankyxhl/babs`.
2. Add `frankyxhl/babs` to
   `BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_BLUEPRINT` and
   `BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_STACK`.
3. Do not add it to `BRIDGE_ALLOWED_REPOSITORIES_ITERWHEEL_CLEARANCE` yet.
4. Restart the Wukong bridge with `launchctl kickstart -kp`.
5. Open one templated issue and verify Blueprint plus Stack writebacks.
6. Wait for one clean cycle before preparing `frankyxhl/screen-harness`.

### Example: Roll Back `frankyxhl/babs`

1. Remove `frankyxhl/babs` from the Blueprint and Stack app-specific
   allow-lists.
2. Restart the Wukong bridge.
3. Trigger a safe issue event.
4. Verify no bot labels or marker comments are written and Wukong logs show
   `repository_allowlist_denied`.

## Pitfalls

- Do not add both expansion candidates in the same change.
- Do not use the global `BRIDGE_ALLOWED_REPOSITORIES` fallback for convenience.
- Do not enable Clearance until PR labels, review webhooks, and branch
  protection are understood for that repository.
- Do not treat a successful webhook ping as full validation; it only proves
  delivery, not writeback permissions or labels.
- Do not include `frankyxhl/sweeping-monk` in this rollout.

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-05-18 | Initial staged canary expansion SOP for issue #48. | Codex |
