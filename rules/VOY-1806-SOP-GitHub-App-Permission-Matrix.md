# SOP-1806: GitHub App Permission Matrix

**Applies to:** VOY project
**Last updated:** 2026-05-09
**Last reviewed:** 2026-05-09
**Status:** Active
**Related:** VOY-1802, VOY-1804, VOY-1805

---

## What Is It?

This SOP defines the initial GitHub App permission matrix for the first
Iterwheel Voyager bot roster. It maps each public bot identity to the minimum
repository permissions and webhook events needed for its first operating mode.


## Why

GitHub App permissions are security boundaries. They decide which repository
resources a bot can read, write, and receive webhook notifications for. The
Voyager bots must start with narrowly scoped permissions: enough to comment,
publish checks, and report gate verdicts, but not enough to administer
repositories, manage secrets, deploy production, or merge code directly.

---

## When to Use

- Creating the first-batch GitHub Apps for the Iterwheel organization.
- Reviewing whether a bot needs additional GitHub API access.
- Installing a GitHub App onto selected repositories.
- Auditing why an app was granted a specific permission or webhook event.


## When NOT to Use

- Granting repository administration, organization administration, billing,
  secrets, deployments, or production-write permissions.
- Designing automatic merge authority for `iterwheel-countdown`. That requires a
  separate ADR and hardened implementation.
- Replacing GitHub branch protection. The Countdown bot publishes a verdict; it
  does not substitute for protected-branch policy.


## Steps

1. **Create one GitHub App per public bot identity**

   Use separate GitHub Apps so public GitHub actions can appear under distinct
   bot names such as `iterwheel-blueprint[bot]` and `iterwheel-countdown[bot]`.

   | GitHub App name | Display stage | First operating mode |
   |-----------------|---------------|----------------------|
   | `iterwheel-blueprint` | Blueprint | Issue intake, issue title validation, triage comments, labels, and ready-state rocket reactions. |
   | `iterwheel-stack` | Stack | Issue type, area, size, risk, and routing label classification. |
   | `iterwheel-assembly` | Assembly | Branch creation, code writing, local test execution, commit pushing, pull request opening and updating, and review requesting. |
   | `iterwheel-staticfire` | Static Fire | CI, test, workflow, and check aggregation. |
   | `iterwheel-clearance` | Clearance | Review readiness aggregation. |
   | `iterwheel-countdown` | Countdown | Final GO/HOLD merge gate with PR title/body convention checks, emoji reactions, review-thread resolution, and PR approval authority, but no merge authority. |

2. **Use common app settings**

   | Setting | Value |
   |---------|-------|
   | Owner | `iterwheel` organization |
   | Homepage URL | `https://github.com/iterwheel` |
   | Initial webhook active | No |
   | Later webhook URL | `https://gh.iterwheel.com/github/webhook` |
   | SSL verification | Enabled |
   | Installation visibility | Only on this account |
   | Initial repository installation | Only selected test repositories |
   | User authorization during installation | Disabled unless a later design requires user-scoped API calls |

   Create the apps with webhooks disabled until the local bridge is listening on
   `127.0.0.1:8787` and `https://gh.iterwheel.com/healthz` succeeds. When
   enabling webhooks later, each app should use its own webhook secret and
   private key. Secrets and private keys must be stored outside git with `600`
   file permissions.

3. **Grant the first-batch repository permissions**

   | App | Metadata | Contents | Issues | Pull requests | Checks | Actions | Commit statuses |
   |-----|----------|----------|--------|---------------|--------|---------|-----------------|
   | `iterwheel-blueprint` | Read | No access | Read & write | No access | Read & write | No access | No access |
   | `iterwheel-stack` | Read | No access | Read & write | No access | No access | No access | No access |
   | `iterwheel-assembly` | Read | Read & write | Read-only | Read & write | Read-only | Read-only | Read-only |
   | `iterwheel-staticfire` | Read | Read-only | No access | Read-only | Read & write | Read-only | Read-only |
   | `iterwheel-clearance` | Read | Read-only | Read & write | Read & write | Read & write | No access | Read-only |
   | `iterwheel-countdown` | Read | Read-only | Read & write | Read & write | Read & write | Read-only | Read-only |

   Notes:

   - `Metadata: read` is the baseline repository visibility permission.
   - `Contents: read-only` allows PR-context and repository file reads without
     granting code write access.
   - `Issues: read & write` allows issue comments, labels, issue reactions, and
     PR comments that flow through issue APIs. This is required for
     `iterwheel-blueprint` ready-state rocket reactions, plus Stack label
      management and issue timeline emoji reactions. Assembly needs `read-only`
      issue access to read issue bodies for implementation context.
      For Blueprint, label
     write-back is limited to the VOY-1805 standard labels:
     `blueprint-needed`, `blueprint-ready`, and
     `blueprint-requests-revision`.
     For Stack, label write-back is limited to the VOY-1805 `stack-*`
     classification allow-list, including `stack-needs-review` for
     low-confidence classifications.
   - `Pull requests: read & write` allows PR review workflow participation for
     Clearance. Countdown also receives pull-request write access so it can
     create approving reviews, resolve review threads through the GraphQL API,
     and react to pull request review comments. Stack does not need pull request
     access because it is issue-only.
   - `Checks: read & write` allows each bot that publishes a verdict to create
     check runs.
   - `Actions: read-only` and `Commit statuses: read-only` are reserved for bots
     that summarize CI, gate readiness, or implementation feedback.
    - `Contents: read & write` is granted to `iterwheel-assembly` as the sole
      exception. Assembly needs write access to create branches and push
      implementation commits. Merge authority is denied by branch protection
      (require PR approvals) and explicit SOP prohibition — Assembly must not
      merge, even though it holds the technical permission.
    - `Contents: write` is intentionally denied for all other bots. GitHub's pull request merge API
     requires contents write permission, so denying contents write keeps
     `iterwheel-countdown` from having merge authority.

4. **Subscribe to webhook events**

   During bootstrap, a selected repository may use one repository-level webhook
   that subscribes to the union of these events and forwards them to the local
   bridge. Keep GitHub App webhooks disabled until per-app webhook activation is
   proven to persist reliably. The repository webhook is only an event source;
   write-back must still use the matching GitHub App installation identity.

   | App | Events |
   |-----|--------|
   | `iterwheel-blueprint` | Issues, Issue comment |
   | `iterwheel-stack` | Issues, Issue comment |
   | `iterwheel-assembly` | Push, Pull request, Issue comment, Check run, Check suite, Status, Workflow run |
   | `iterwheel-staticfire` | Check run, Check suite, Status, Workflow run, Pull request |
   | `iterwheel-clearance` | Pull request, Pull request review, Pull request review comment, Issue comment |
   | `iterwheel-countdown` | Pull request, Pull request review, Pull request review comment, Check run, Check suite, Status, Workflow run, Issue comment |

5. **Do not grant dangerous defaults**

   The first-batch apps must not receive these permissions by default:

   - Administration
   - Secrets
   - Codespaces secrets
   - Dependabot secrets
   - Environments
   - Deployments
   - Workflows write access
   - Contents write access (except Assembly, which receives it with merge prohibited by branch protection)
   - Organization administration
   - Billing or plan access

6. **Install cautiously**

   Install each app only on selected test repositories at first. Expand
   repository access after webhook delivery, signature verification, event
   routing, dry-run publishing, and scoped write-back are proven.


## Examples

### First safe installation

Create `iterwheel-countdown` with the permissions in this SOP, install it only
on one non-critical repository, and configure branch protection to require the
Countdown check. It may approve a PR or resolve a review thread after policy is
satisfied, but it still must not receive contents-write permission or direct
merge authority.

### Permission escalation request

If `iterwheel-countdown` later needs to merge pull requests directly, do not
edit this SOP in place. Write a new ADR describing the exact merge mechanism,
branch protection interaction, rollback behavior, audit trail, and failure
modes.

---

## Change History

| Date       | Change                                                                                                    | By               |
|------------|-----------------------------------------------------------------------------------------------------------|------------------|
| 2026-05-09 | Initial version - recorded per-bot GitHub App permissions, webhook events, and denied dangerous defaults  | Frank Xu + Codex |
| 2026-05-09 | Replaced short `iw-` app names with organization-owned `iterwheel-` names after GitHub App name collision | Frank Xu + Codex |
| 2026-05-09 | Added repository-webhook bootstrap note while GitHub App webhooks remain disabled                         | Frank Xu + Codex |
| 2026-05-09 | Clarified expansion criteria after scoped sandbox write-back was proven                                   | Frank Xu + Codex |
| 2026-05-09 | Recorded Blueprint ready-state rocket reactions as part of Issues write-back behavior                     | Frank Xu + Codex |
| 2026-05-09 | Clarified Blueprint issue title validation, Stack classification, and Countdown PR gate ownership         | Frank Xu + Codex |
| 2026-05-09 | Recorded the three-label Blueprint issue-state standard in the Issues write-back permission note          | Frank Xu + Codex |
| 2026-05-09 | Recorded Stack v1 `stack-*` classification label write-back scope                                         | Frank Xu + Codex |
| 2026-05-09 | Added `stack-needs-review`, Stack status comments, and Stack success `rocket` reactions                   | Frank Xu + Codex |
| 2026-05-09 | Added Clearance v1 PR review-readiness event ownership                                                    | Frank Xu + Codex |
| 2026-05-09 | Tightened Stack to issue-only labels and removed Stack PR event ownership                                 | Frank Xu + Codex |
| 2026-05-23 | Added Assembly bot: app settings, permission row (Contents write exception with merge prohibition), webhook events, and note on dangerous defaults (issue #67) | DeepSeek (via VOY-1811) |
