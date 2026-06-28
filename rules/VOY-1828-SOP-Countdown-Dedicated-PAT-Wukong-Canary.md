# SOP-1828: Countdown Dedicated PAT Wukong Canary

**Applies to:** Voyager Countdown resolver canary operators on Wukong
**Last updated:** 2026-06-28
**Last reviewed:** 2026-06-25
**Status:** Deprecated
**Superseded by:** VOY-1830 (dedicated-PAT machinery removed in favor of the gh-login machine-account resolver, #222)
**Related:** VOY-1805, VOY-1806, VOY-1807, VOY-1827, VOY-1830, #214

---

## What Is It?

This SOP is the Wukong runbook for the issue #214 sandbox canary. It tests
whether a dedicated machine-user PAT can resolve a GitHub pull request review
thread only after the Countdown GitHub App installation token proves that the
App route still cannot resolve the same thread.

This is a fallback canary. It does not authorize production use, does not close
issue #200, and does not prove that `iterwheel-countdown[bot]` can resolve
review threads.

## Why

This SOP is retained as a deprecated historical runbook so operators can audit
what the Wukong canary did and why VOY-1830 later removed the dedicated-PAT
machinery. It must not be used for new resolver work; the current route is the
gh-login machine-account resolver documented after VOY-1830.

## Reader and Action

The reader is the Wukong operator running the Countdown resolver spike.

After reading this SOP, the operator should be able to run the App baseline,
run the dedicated PAT fallback leg, resolve exactly one approved sandbox review
thread when the gates pass, and record evidence without leaking token material,
private PR numbers, or review-thread node IDs.

## When to Use

Use this SOP when all of these are true:

- VOY-1827 has approved a dedicated machine-user PAT fallback canary.
- Wukong has the Countdown GitHub App config and private key available.
- A private sandbox PR has exactly the intended unresolved diff-backed review
  thread.
- Clearance or an operator-approved canary note says this thread is safe to
  close.
- The dedicated PAT is stored in an operator secret backend, not in the repo.

## When NOT to Use

Do not use this SOP to:

- run against `iterwheel/voyager`
- resolve more than one review thread
- use a maintainer's personal PAT
- skip the same-thread Countdown App baseline
- run when the thread is already resolved or outdated
- record token material, private PR numbers, or review-thread node IDs in
  issues, PRs, docs, terminal transcripts, or screenshots
- describe a successful PAT resolve as Countdown bot capability

## Preconditions

Before running the canary, confirm Wukong has:

- a clean checkout that contains the `vyg countdown review-thread-diagnostic`
  PAT fallback support
- `/Users/frank/.voyager/config.toml` with an `iterwheel-countdown` app entry
- a Countdown App ID of `3646540`
- a selected-repository installation for `iterwheel/voyager-sandbox` with
  installation ID `130630407`
- a readable Countdown private key referenced by the Wukong config
- private file permissions: config file `600`, secret directory `700`, private
  key file `600`
- a macOS Keychain item for the dedicated PAT under service
  `voyager/countdown-dedicated-pat`

The private sandbox PR number and review-thread node ID are operator notes.
Keep them in shell-local variables or a private note. Do not commit them.

## Steps

The steps below are retained for historical audit of the deprecated canary only.
Do not run them for new work unless a later approved CHG explicitly reactivates
this route.

## Step 1: Store or Confirm the PAT on Wukong

If the dedicated PAT is not already in Wukong Keychain, save it by prompting
for the secret value. Keep the bare `-w` as the final argument; macOS
`security add-generic-password -h` documents that final-position form as the
interactive password prompt. Do not pass the token as `-w <token>` because that
exposes it through process arguments.

```bash
security add-generic-password -U \
  -a "<dedicated-machine-user-login>" \
  -s voyager/countdown-dedicated-pat \
  -l "Voyager Countdown dedicated PAT" \
  -j "Issue #214 sandbox resolver canary; classic PAT; expires 2026-07-25" \
  -w
```

Confirm lookup without printing the token by checking only the command exit
status:

```bash
security find-generic-password \
  -a "<dedicated-machine-user-login>" \
  -s voyager/countdown-dedicated-pat \
  -w >/dev/null
```

If the lookup fails, stop. Do not paste the PAT into `.env`, a shell command,
or the repo.

## Step 2: Set Private Operator Variables

Set these only in the current Wukong shell. Use prompts so private target
values are not written into shell history:

```bash
export VOYAGER_CANARY_REPO="iterwheel/voyager-sandbox"

printf "Private sandbox PR number: "
IFS= read -r VOYAGER_CANARY_PR
export VOYAGER_CANARY_PR

printf "Private review-thread node ID: "
IFS= read -r VOYAGER_CANARY_THREAD_ID
export VOYAGER_CANARY_THREAD_ID

printf "Dedicated PAT Keychain account: "
IFS= read -r VOYAGER_PAT_ACCOUNT
export VOYAGER_PAT_ACCOUNT
```

Do not echo these into public logs. The PR number, thread ID, and exact
machine-user login must stay out of public docs and GitHub comments unless a
later CHG explicitly changes that boundary.

## Step 3: Validate Wukong Countdown Config

Run this from the Voyager checkout on Wukong:

```bash
uv run python - <<'PY'
from voyager.core.config import load_config

cfg = load_config()
app = cfg.apps["iterwheel-countdown"]
print(app.slug)
print(app.app_id)
print(app.private_key_path.exists())
print(app.installations.get("iterwheel/voyager-sandbox"))
PY
```

Expected output shape:

```text
iterwheel-countdown
3646540
True
130630407
```

If the private key path does not exist or the sandbox installation ID is
missing, stop and fix the Wukong config before continuing.

## Step 4: Run the Countdown App Baseline

Query the target thread with the Countdown App installation token:

```bash
uv run vyg countdown review-thread-diagnostic \
  --repo "$VOYAGER_CANARY_REPO" \
  --pr "$VOYAGER_CANARY_PR" \
  --thread-id "$VOYAGER_CANARY_THREAD_ID" \
  --json
```

Required result:

- `app_slug` is `iterwheel-countdown`
- actor class is Countdown bot
- `type` is `PullRequestReviewThread`
- `repository` is the private sandbox repository
- `isResolved=false`
- `isOutdated=false`
- `viewerCanResolve=false`
- `viewerCanReply=true`

If `viewerCanResolve=true`, stop. The App route is enough and the PAT fallback
must not be used for this canary.

If the thread is missing, resolved, outdated, or belongs to a different PR,
stop. Fix the operator notes or create a fresh canary thread.

## Step 5: Run the Dedicated PAT Query Leg

Query the same thread with the dedicated PAT:

```bash
uv run vyg countdown review-thread-diagnostic \
  --repo "$VOYAGER_CANARY_REPO" \
  --pr "$VOYAGER_CANARY_PR" \
  --thread-id "$VOYAGER_CANARY_THREAD_ID" \
  --pat-token-command "security find-generic-password -a ${VOYAGER_PAT_ACCOUNT:?} -s voyager/countdown-dedicated-pat -w" \
  --pat-expected-login-env VOYAGER_PAT_ACCOUNT \
  --json
```

Required result:

- `app_slug` is `dedicated-pat-fallback`
- actor class is dedicated machine user
- the CLI accepted the private expected-login check before redacting the actor
- `type` is `PullRequestReviewThread`
- `repository` is the private sandbox repository
- `isResolved=false`
- `isOutdated=false`
- `viewerCanResolve=true`
- `viewerCanReply=true`

If `viewerCanResolve=false`, stop. The fallback route did not prove resolver
capability for this thread.

## Step 6: Resolve the Thread Only If Both Gates Passed

Run the mutation only when Step 4 and Step 5 both matched the required results
for the same thread node:

```bash
uv run vyg countdown review-thread-diagnostic \
  --repo "$VOYAGER_CANARY_REPO" \
  --pr "$VOYAGER_CANARY_PR" \
  --thread-id "$VOYAGER_CANARY_THREAD_ID" \
  --pat-token-command "security find-generic-password -a ${VOYAGER_PAT_ACCOUNT:?} -s voyager/countdown-dedicated-pat -w" \
  --pat-expected-login-env VOYAGER_PAT_ACCOUNT \
  --resolve \
  --json
```

The expected-login env is private operator state. It lets the CLI prove the
PAT viewer matches the dedicated machine user before running the mutation,
while keeping the exact login out of public evidence.

Expected result:

- the operation is applied
- post-mutation `isResolved=true`
- resolved-by actor class is the dedicated machine user
- resolved-by actor is not `iterwheel-countdown[bot]`

If the command reports that no operation was applied, record the failure and
stop. Do not retry against another thread in the same run.

## Evidence Rules

Public evidence may record:

- host route: Wukong
- token class: dedicated machine-user PAT
- secret backend service name: `voyager/countdown-dedicated-pat`
- repository class: private sandbox
- App baseline flags: `viewerCanResolve`, `viewerCanReply`, `isResolved`,
  `isOutdated`
- PAT leg flags: `viewerCanResolve`, `viewerCanReply`, `isResolved`,
  `isOutdated`
- whether the resolve operation applied
- post-mutation `isResolved`
- resolved-by actor class

Public evidence must not record:

- token material
- private key material
- passkeys, passwords, two-factor codes, or recovery codes
- private PR numbers
- review-thread node IDs
- exact shell history containing private target values
- screenshots that reveal token material or private target values

The diagnostic JSON includes thread IDs. Redact `thread_id` fields before
sharing output outside private operator notes.

## Rollback and Follow-Up

If the canary succeeds:

- record the result as a dedicated machine-user fallback route, not Countdown
  bot resolver capability
- leave issue #200 open unless a separate decision closes it
- open a follow-up CHG before enabling any production behavior
- revoke the PAT at the end of the canary window unless a follow-up CHG
  explicitly keeps it for more testing

If the canary fails:

- record the failing gate and stop
- do not widen Countdown App permissions
- do not broaden the machine user's repository access
- do not create a maintainer personal PAT fallback

If the wrong thread or wrong actor resolves the thread, treat the run as a
failure. Record the actor class and stop.

## Troubleshooting

If config loading fails:

- confirm `/Users/frank/.voyager/config.toml` exists
- confirm the `iterwheel-countdown` app key is present
- confirm the config file is readable only by the operator

If the private key is missing:

- use the path printed by the config validation command as the source of truth
- restore the Wukong-local private key from the operator secret path
- do not copy private key material into the repo

If Keychain lookup fails:

- confirm the service is `voyager/countdown-dedicated-pat`
- confirm the account is the dedicated machine-user login
- unlock the Wukong login Keychain if macOS prompts
- store the token again with `security add-generic-password -U`

If the App baseline returns `viewerCanResolve=true`:

- stop
- do not use the PAT fallback
- record that the App route became viable and needs separate review

If the PAT leg returns `viewerCanResolve=false`:

- stop
- confirm the machine user still has write access only to the sandbox
- confirm the PAT has not expired or been revoked
- do not add access to production repositories

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-06-28 | Added standard Why and Steps sections required by SOP validation while preserving deprecated historical-runbook status. | Codex |
| 2026-06-25 | Added Wukong operator SOP for the issue #214 dedicated PAT fallback canary, including same-thread App baseline, PAT query, controlled resolve, evidence, and rollback rules. | Codex |
