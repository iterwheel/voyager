# CHG-1829: Countdown Dedicated PAT Fallback Production Gate

**Applies to:** Voyager Clearance Stage 1.5 and Countdown resolver fallback
**Last updated:** 2026-06-25
**Last reviewed:** 2026-06-25
**Status:** Rolled Back
**Superseded by:** VOY-1830 (never approved or activated; withdrawn pre-approval — dedicated-PAT machinery removed in favor of the gh-login machine-account resolver, #222)
**Related:** VOY-1805, VOY-1806, VOY-1807, VOY-1827, VOY-1828, VOY-1830, #214

---

## What Is It?

This CHG formalizes the dedicated machine-user PAT fallback proven by the
VOY-1828 Wukong canary.

The fallback is not `iterwheel-countdown[bot]` resolver capability. It is a
separate human-created dedicated machine-user route that may be used only after
the Countdown GitHub App installation token proves that the App route still
cannot resolve the same review thread.

## Canary Result

The Wukong canary succeeded on a private sandbox review thread:

- Countdown App baseline: `viewerCanResolve=false`, `viewerCanReply=true`,
  `isResolved=false`, `isOutdated=false`.
- Dedicated PAT query: expected-login check passed and
  `viewerCanResolve=true`.
- Resolve mutation: `operation_applied=true`.
- After-state verification: `after.isResolved=true`.
- Resolved-by actor class: dedicated machine-user fallback.

Public evidence must continue to omit token material, private key material,
private PR numbers, review-thread node IDs, and the exact machine-user login.

## Decision

Add a production code path that is default-off and guarded by all of these
conditions:

1. Clearance Stage 1.5 has already judged the Codex review thread semantically
   `RESOLVED`.
2. Clearance cannot resolve the thread with its own App token
   (`viewerCanResolve=false`).
3. The Countdown App baseline for the same thread reports
   `viewerCanResolve=false`, `viewerCanReply=true`, `isResolved=false`, and
   `isOutdated=false`.
4. `[countdown.dedicated_pat_fallback]` is enabled in operator-local config.
5. The repository is present in
   `[countdown.dedicated_pat_fallback].allowed_repositories`.
6. The PAT is loaded only from the configured macOS Keychain service.
7. The expected-login environment variable is set and matches the PAT viewer.
8. The PAT query for the same thread reports `viewerCanResolve=true`,
   `viewerCanReply=true`, `isResolved=false`, and `isOutdated=false`.
9. The post-mutation verification reports `isResolved=true`.

If any gate fails, Voyager records the failing gate and does not broaden
permissions, switch identities, or retry another thread.

## Configuration

The default config keeps the fallback disabled:

```toml
[countdown.dedicated_pat_fallback]
enabled = false
allowed_repositories = ["iterwheel/voyager-sandbox"]
keychain_service = "voyager/countdown-dedicated-pat"
expected_login_env = "VOYAGER_PAT_ACCOUNT"
```

Production activation requires an explicit operator-local config change. Do
not commit real tokens, real logins, private PR numbers, or review-thread node
IDs.

## Runtime Behavior

Clearance Stage 1.5 remains the semantic source of truth. The fallback only
syncs GitHub UI state after Clearance has already decided that a Codex review
thread is resolved.

The public Stage 1.5 action records:

- `resolver_app=dedicated-pat-fallback`
- `resolver_actor_class=dedicated_machine_user_fallback`
- `resolver_login=dedicated-pat-fallback-user`
- Countdown App baseline flags
- PAT `viewerCanResolve` result
- whether the mutation applied

It does not record the real dedicated login.

## Rollback

Rollback is any one of:

- set `[countdown.dedicated_pat_fallback].enabled=false`
- remove the repository from `allowed_repositories`
- unset the expected-login env var
- delete or revoke the Keychain PAT

If the dedicated account gains access to any additional private repository,
stop using the PAT until a fresh access audit is complete.

## Operator Tasks

Before enabling this outside sandbox, Frank must:

- approve this CHG
- choose the exact repository allow-list
- keep the dedicated PAT in Keychain service `voyager/countdown-dedicated-pat`
- export the expected-login env var only in the Wukong service environment
- remove any temporary plaintext token file after confirming Keychain storage
- approve any production-repository expansion as a separate change

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-06-25 | Initial CHG after the successful VOY-1828 dedicated PAT fallback canary. | Codex |
