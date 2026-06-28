# REF-1827: Countdown Dedicated PAT Fallback Canary

**Applies to:** VOY project
**Last updated:** 2026-06-28
**Last reviewed:** 2026-06-25
**Status:** Deprecated
**Superseded by:** VOY-1830 (dedicated-PAT machinery removed in favor of the gh-login machine-account resolver, #222)
**Related:** VOY-1805, VOY-1806, VOY-1807, VOY-1828, VOY-1830

---

## What Is It?

This REF preserves the now-superseded hypothesis and operator notes for testing
a dedicated machine-user Personal Access Token as a Countdown review-thread
resolver fallback.

It is canary-only. It does not authorize production use, does not close the
Countdown resolver design, and does not prove that `iterwheel-countdown[bot]`
can resolve review threads.

---

## Why

This document is historical reference material after VOY-1830 removed the
dedicated-PAT machinery in favor of the gh-login machine-account resolver. It
remains useful for understanding why the PAT route was explored, what boundaries
were required during the canary, and why the route must not be treated as active
operator guidance.

---

## Reader and Action

The reader is a future operator running the Countdown resolver spike.

After reading this document, the operator should be able to decide whether a
dedicated PAT can be created for a sandbox canary, how to create it without
leaking credentials, and when to stop instead of widening access.

---

## Current Hypothesis

The GitHub App installation-token route is still the primary Countdown identity.
The dedicated PAT route exists only as an explicit fallback canary after the App
token reports `viewerCanResolve=false` for a Clearance-approved thread.

The current account shape creates an important constraint:

- Fine-grained PAT creation from an outside-collaborator machine account may
  show only the machine account itself as a resource owner.
- If the `iterwheel` organization is not selectable as a resource owner, the
  fine-grained PAT cannot be scoped to an organization-owned private sandbox
  repository.
- In that state, creating a fine-grained PAT under the machine account resource
  owner is the wrong test. It would not grant the intended repository access.

The viable canary fallback under that constraint is a classic PAT with the
minimum classic scope needed for private repository pull request operations.
Because a classic PAT follows the machine user's repository access, the machine
user must remain dedicated and must have access only to the sandbox repository
for the canary.

Classic `repo` is not a narrow pull-request-only permission. The blast-radius
control is the machine user's repository access surface, not the token scope.
If that account is later added to another private repository, the same classic
PAT becomes broader and must not be reused without a fresh access audit.

---

## When to Use

Use this historical reference only when all of these are true:

- Countdown's GitHub App installation token has already reported
  `viewerCanResolve=false` for the target review thread.
- Clearance has already produced semantic evidence that the thread should be
  closed.
- The target is a private sandbox pull request.
- The machine user is dedicated to Countdown fallback testing.
- The machine user has repository access only to the sandbox target.
- The token can be stored directly in an operator-controlled secret backend.

---

## When NOT to Use

Do not use this historical reference to:

- Substitute a maintainer's personal PAT.
- Give Countdown merge, approval, or ordinary comment-writing authority.
- Run against `iterwheel/voyager` before a sandbox canary succeeds and a
  follow-up CHG approves production expansion.
- Store a token in `.env`, shell history, GitHub issues, pull requests, docs,
  terminal logs, screenshots, or chat.
- Describe a successful PAT canary as `iterwheel-countdown[bot]` resolver
  capability.

---

## Procedure

1. **Preflight the machine user**

   Confirm that the account is dedicated to Countdown fallback testing, has a
   verified email route, and has no repository access except the sandbox target.
   Any password, passkey, two-factor, recovery-code, or email-verification step
   must be performed by the human operator.

2. **Attempt fine-grained PAT scoping first**

   Open the machine user's fine-grained PAT creation page and inspect the
   resource owner selector.

   If the `iterwheel` organization is selectable, prefer a fine-grained PAT:
   select only the sandbox repository, keep the expiration at 30 days or less,
   and grant only Metadata read plus Pull requests read/write.

   If the selector shows only the machine account, stop the fine-grained path.
   Do not generate a fine-grained PAT under the machine account resource owner
   for this canary.

3. **Use classic PAT only as the constrained fallback**

   Create a classic PAT only when fine-grained repository scoping is blocked by
   the resource-owner constraint above.

   The classic PAT must:

   - expire in 30 days or less
   - use only the `repo` scope, while treating that scope as broad
   - belong to the dedicated machine user
   - be generated only after an explicit operator confirmation at the final
     GitHub UI step
   - be copied and stored by the operator without the agent reading it

4. **Store the token outside the repo**

   Store the PAT directly in Keychain or another approved secret backend. Do not
   paste it into source files, config examples, `.env`, shell commands, issue
   comments, pull request comments, or public documentation.

   The current macOS Keychain convention is:

   | Field | Value |
   |-------|-------|
   | Service | `voyager/countdown-dedicated-pat` |
   | Account | the dedicated machine-user login |
   | Label | `Voyager Countdown dedicated PAT` |
   | Comment | `Issue #214 sandbox resolver canary; classic PAT; expires 2026-07-25` |

   Save the token by prompting for the secret value. Keep the bare `-w` as the
   final argument; macOS `security add-generic-password -h` documents that
   final-position form as the interactive password prompt. Do not pass the token
   as `-w <token>` because that exposes it through process arguments:

   ```bash
   security add-generic-password -U \
     -a "<dedicated-machine-user-login>" \
     -s voyager/countdown-dedicated-pat \
     -l "Voyager Countdown dedicated PAT" \
     -j "Issue #214 sandbox resolver canary; classic PAT; expires 2026-07-25" \
     -w
   ```

   Read the token only into process memory for a canary command. Do not print it:

   ```bash
   security find-generic-password \
     -a "<dedicated-machine-user-login>" \
     -s voyager/countdown-dedicated-pat \
     -w
   ```

5. **Run the canary as a fallback, not a primary path**

   The canary order is:

   - query the target thread with the Countdown App installation token
   - confirm the App token still reports `viewerCanResolve=false`
   - load the dedicated PAT from the secret backend
   - query the same target with the PAT
   - run `resolveReviewThread` only if the PAT actor reports
     `viewerCanResolve=true`, the thread is still unresolved, and the thread
     belongs to the intended sandbox PR

   If the mutation runs, record whether the operation applied, the
   post-mutation `isResolved` value, and the resolved-by actor class.

   The CLI entry point for the PAT leg is below. Keep `VOYAGER_PAT_ACCOUNT` in
   operator-local state only; it is used to verify the PAT viewer before the
   CLI redacts the actor class:

   ```bash
   uv run vyg countdown review-thread-diagnostic \
     --repo iterwheel/voyager-sandbox \
     --pr "<private-sandbox-pr-number>" \
     --thread-id "<operator-note-thread-node-id>" \
     --pat-token-command "security find-generic-password -a ${VOYAGER_PAT_ACCOUNT:?} -s voyager/countdown-dedicated-pat -w" \
     --pat-expected-login-env VOYAGER_PAT_ACCOUNT \
     --json
   ```

   Use `--resolve` only after the App-token baseline for the same target thread
   reports `viewerCanResolve=false` and the PAT-token leg reports
   `viewerCanResolve=true`.

   On Wukong, run the full App-baseline plus PAT-resolve canary through
   VOY-1828. Keep the private sandbox PR number and review-thread node ID in
   operator notes only.

6. **Rollback**

   Revoke the PAT after the canary window or when the route is abandoned. If the
   machine user gains access to any additional private repository, reassess the
   blast radius before using an existing classic PAT again.

---

## Evidence Rules

Public docs may record:

- actor class: dedicated machine user
- secret backend item name
- token class: fine-grained PAT or classic PAT
- expiration class: 30 days or less
- repository class: private sandbox repository
- fine-grained resource-owner result
- `viewerCanResolve`, `viewerCanReply`, `isResolved`, and `isOutdated` flags
- whether the operation applied
- the resolved-by actor class

Public docs must not record:

- token material
- passwords, passkeys, two-factor codes, recovery codes, or email codes
- private PR numbers
- review-thread node IDs
- screenshots containing token material
- secret-store commands that embed token values

---

## Promotion Criteria

A successful sandbox canary is not production approval.

Before this route can become production behavior, a follow-up CHG must define:

- the exact code path for an explicit fallback credential
- the secret backend contract
- the feature flag or repository allow-list
- the requirement that the App installation token is attempted first
- the Clearance evidence required before fallback
- the audit record, including the actual resolved-by actor class
- rollback and token revocation steps

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-06-28 | Reclassified from unsupported HYP document type to deprecated REF and clarified superseded historical-reference status. | Codex |
| 2026-06-25 | Initial HYP for the dedicated machine-user PAT fallback canary after fine-grained PAT resource-owner scoping proved unavailable for the outside-collaborator account shape. | Codex |
| 2026-06-25 | Added the macOS Keychain service convention and safe save/read commands for the 30-day classic PAT canary. | Codex |
| 2026-06-25 | Added the `vyg countdown review-thread-diagnostic --pat-token-command` canary entry point and documented that `--resolve` still requires the App-token baseline first. | Codex |
| 2026-06-25 | Linked VOY-1828 as the Wukong-specific full canary SOP. | Codex |
