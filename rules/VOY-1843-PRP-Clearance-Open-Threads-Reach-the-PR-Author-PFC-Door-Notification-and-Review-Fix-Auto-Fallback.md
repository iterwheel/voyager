# PRP-1843: Clearance-Open Threads Reach the PR Author — PFC Door Notification and Review-Fix Auto-Fallback

**Applies to:** VOY project — Clearance, Countdown, and governed review-fix automation
**Last updated:** 2026-08-30
**Last reviewed:** 2026-08-30
**Status:** Proposed
**Related:** VOY-1814 (Wukong bridge rollout and rollback), VOY-1831 (Countdown resolve-loop), VOY-1839 (Countdown merge-loop), VOY-1840 (merge-loop deployment)
**Reviewed by:** —
**Alignment:** `crisp | questions_asked: 0 | terms_resolved: 4 | offered_adr: 0`

---

## What Is It?

A bridge-owned, durable author-wakeup reconciler for Codex review threads that
remain in author-keyed Clearance state A: fresh, unresolved, and with no reply
from the PR-author login. Ten minutes after first observing that state, Voyager
sends one PR-level notification through the local PFC dashboard door so the
delegation graph can route the work back to the citizen that opened the PR. If
no GitHub-observable claim appears within a further twenty minutes, Voyager
invokes the existing governed `review_fix` loop internally for only the notified
thread IDs. This proposal adds one external integration edge — Voyager to PFC —
and does not weaken Countdown's resolution or merge gates.

---

## Problem

On 2026-08-30, the Codex review on `frankyxhl/alfred#330` (review
`5058873125`) demonstrated an ownership gap. Clearance correctly classified a
fresh thread with no author response as state A and left it `OPEN`, but its only
action was an in-thread "still open" conclusion. No component called the PR's
authoring citizen back into the task. The governed `voyager/bots/review_fix`
path existed, but its router accepts only a newly created PR issue comment whose
body parses as `/review-fix` or `/pr-review-fix`
(`voyager/bots/review_fix/routing.py:20-29`). The PR therefore stayed safe but
unattended.

The current safety behavior is already correct and MUST remain intact:

1. Clearance defines state A as not outdated with no author reply
   (`voyager/bots/clearance/classify.py:1-9`, `:134-140`) and judges the
   no-response case `OPEN` (`voyager/bots/clearance/judge.py:133-147`). For
   wake-up eligibility, "author" means the PR author specifically: the
   reconciler calls
   `latest_author_reply(thread, author_login=pr_author_login)` rather than
   relying on `classify_thread()`'s login-agnostic fallback. A maintainer or
   another bot replying does not move this reconciler's state from A to C.
2. `countdown_loop.py` is the **resolve-loop**, not the merge-loop. It admits
   only mechanically resolvable threads (`voyager/core/countdown_loop.py:429-469`),
   then records a veto without resolving when the semantic gate returns false
   (`voyager/core/countdown_loop.py:498-507`). Its safety contract explicitly
   says the gate can only veto and never promote a non-candidate
   (`voyager/core/countdown_loop.py:11-16`).
3. `countdown_gate.py` defaults to `should_resolve=false` when evidence is absent
   or uncertain (`voyager/core/countdown_gate.py:4-9`, `:40-42`) and fail-closes
   malformed or negative output (`voyager/core/countdown_gate.py:93-106`). A
   state-A thread therefore remains unresolved until somebody addresses it.
4. The actual Countdown merge predicate independently counts every unresolved
   review thread, failing closed on unreadable pagination
   (`voyager/core/merge_loop.py:404-433`), and returns
   `threads_unresolved` when the count is positive
   (`voyager/core/merge_loop.py:248-251`). `run_merge_loop` records that skip
   instead of reaching the mutation (`voyager/core/merge_loop.py:906-912`).

**Verified answer:** yes — the deployed design already refuses to merge while a
Clearance-OPEN review thread remains unresolved. The missing behavior is author
wake-up and, only after a bounded unclaimed interval, repair fallback. This PRP
must not turn an `OPEN` verdict into a resolve or merge authorization.

---

## Scope

**In scope (v1):**

- Detect current-head Codex threads that remain author-keyed Clearance state A,
  verdict `OPEN`, and GitHub `isResolved=false` for a configurable notification
  delay.
- Batch all due thread IDs for the same repo, PR, and head into one PFC door
  notification.
- Define durable notification, claim, retry, dedupe, and audit semantics that
  survive a bridge restart.
- Invoke the existing governed review-fix machinery internally when the
  notification was delivered but no author claim appears before the fallback
  deadline.
- Roll out behind default-off, repository-scoped notification and auto-fix
  switches according to VOY-1814.

**Out of scope (v1):**

- Changing Clearance classification, severity, or verdict rules.
- Relaxing Countdown's deterministic candidate filter, fail-closed LLM veto,
  unresolved-thread merge predicate, approval gate, or readiness gate.
- Resolving review threads, approving PRs, or merging PRs from the new
  reconciler. Existing Clearance and Countdown stages retain those duties.
- Teaching Voyager how to map a GitHub PR to a citizen. PFC owns that mapping
  through its delegation graph and ledger; Voyager supplies exact identifiers.
- Adding a second PFC callback or claim API. Claim evidence remains observable
  on GitHub.
- Broad repository rollout, new GitHub App permissions, or live configuration
  changes as part of this proposal.

---

## Proposed Solution

### 1. Bridge-owned durable reconciler

Add a Clearance author-wakeup reconciler to the production bridge process,
where Clearance state classification and the existing review-fix service already
live. A FastAPI lifespan task reconciles persisted records at a bounded interval
(default 60 seconds); each Clearance webhook also nudges the same idempotent
reconcile function. A bridge restart resumes from the ledger rather than losing
an in-memory timer.

The eligibility key is:

```
(repository, pull_number, head_sha, thread_id)
```

A thread becomes eligible only when all of these are true on a current re-read:

- the PR is open and the head SHA matches the recorded head;
- the first comment is a Codex review comment;
- the reconciler's author-keyed classification is state A: the thread is not
  outdated and
  `latest_author_reply(thread, author_login=pr_author_login) is None`;
- Clearance verdict is `OPEN`;
- GitHub reports `isResolved=false`; and
- the state has remained continuously eligible for `N=10` minutes, measured
  from Voyager's first durable observation (not a backdated comment timestamp).

A state change before N terminates the record. A new head gets a new key and a
new N window. This prevents deployment from immediately paging citizens for all
historical review comments and prevents stale-head work.

### 2. The one new edge: Voyager → PFC door

When one or more threads on the same PR/head reach N, send exactly one batched
message to:

```
POST http://localhost:8420/api/agent-send
Content-Type: application/json

{"citizen":"pfc","message":"<structured wake-up message>"}
```

The message is plain text with a versioned marker and these required fields:

```
[voyager-clearance-author-wakeup/v1]
notification_id: <stable idempotency key>
repository: <owner/name>
pull_request: <number>
head_sha: <40-hex SHA>
thread_ids: <comma-separated GitHub review-thread node IDs>
notify_after_minutes: 10
fallback_after_minutes: 20
instruction: route this to the citizen that opened the PR; claim by replying
             as the PR author, resolving a listed thread, or pushing a new head
```

PFC, not Voyager, resolves the authoring citizen from the delegation graph and
routes the work. Repository, PR, head, and thread identifiers are deliberately
included because the loopback door is machine-local and the recipient needs an
unambiguous task. They must not be copied into the public redacted audit.

The bridge records a stable notification ID before the POST and supplies that
same 32-hex value through the door's optional transport `send_id`; the required
application payload remains `citizen` plus `message` as shown above. A 2xx
`ok=true, queued=true` response records `notify_queued` but does **not** start M.
Voyager reads the corresponding terminal send result through the same PFC
integration boundary; only terminal `ok=true` records `notified`, records the
terminal-receipt time, and starts the claim window. Voyager then computes
`claim_deadline = terminal_receipt_at + M` in its own durable ledger. The
already-sent message carries N and M, never this delivery-derived deadline.
Connection errors, timeouts, non-2xx responses, malformed/false
acknowledgements, missing terminal receipts, or terminal delivery failures record
`notify_failed`, retry with the same idempotency key and bounded backoff, and
MUST NOT arm auto-fallback. This receipt read is transport confirmation on the
single Voyager→PFC edge, not a PFC→Voyager claim callback. One delivered
notification is sent per PR/head batch; later due threads may be appended only
by a separately deduped notification.

### 3. Claim contract without a second edge

`M=20` minutes starts when PFC confirms terminal delivery. The PR is claimed if,
after that timestamp and before Voyager's locally recorded deadline, a current
GitHub read observes any of the following:

- a PR-author reply on any listed review thread, even if the reply is not yet
  substantive enough for Clearance to resolve it;
- a new PR head SHA;
- any listed thread becomes resolved; or
- the PR is closed or merged.

These are durable, author-visible facts already available to Clearance. PFC's
HTTP acceptance is delivery, not a claim. A reply by a maintainer or another bot
is not a PR-author claim. A new head supersedes the old record; if the new head
later has qualifying state-A threads, it starts a fresh N window. No PFC→Voyager
callback, polling endpoint, or citizen-name database is introduced.

### 4. Governed review-fix fallback

When Voyager's locally recorded claim deadline expires, the bridge re-reads the
PR and every listed thread. It invokes review-fix only if all notification-time
predicates still hold, the head is unchanged, no claim evidence exists, the
repo is explicitly allowlisted for author wake-up **and** review-fix, and
auto-fallback is enabled.

Refactor the current manual-command entry point into a reusable internal
invocation contract with `source=clearance_author_wakeup` and an explicit
`finding_ids` allowlist. The manual `/review-fix` and `/pr-review-fix` behavior
remains unchanged. The internal source does not synthesize a GitHub comment or
fake a webhook actor. It must pass the same controls already enforced by
`dispatch_review_fix_writeback`:

- `[review_fix]` is configured at autonomy L3 with a safety envelope;
- bridge dry-run and per-agent repository allowlists permit the operation;
- the review-fix kill-switch is absent;
- the expected head SHA still matches;
- max rounds and max fixes per round remain bounded;
- verification and rollback run for every proposed fix; and
- append-only audit records are written before and after the attempt.

One invocation handles only the notified thread IDs and runs at most once for a
`(repo, PR, head, notification_id)` tuple. A refusal, verification rollback,
escalation, or runtime error is terminal for that tuple and requires a new head
or a manual `/review-fix`; the reconciler does not hammer the same PR. A
successful fix produces a new head and returns control to normal Clearance,
resolve-loop, approval, and merge-loop processing. It never resolves or merges
the thread itself.

### 5. State and audit model

Persist a local 0600 JSONL/SQLite-equivalent ledger under
`~/.voyager/state/clearance-author-wakeup/` with records for:

- `state_a_observed`, `state_a_cleared`, and `state_a_superseded`;
- `notify_intent`, `notify_failed`, and `notified` (including terminal receipt
  time and the locally derived claim deadline);
- `claimed` with the non-sensitive claim class;
- `fallback_intent`, `fallback_refused`, `fallback_started`, and
  `fallback_finished`; and
- the dedupe keys, timestamps, attempt counts, and current head.

The local full-fidelity ledger may contain PR and thread IDs and stays 0600.
Shareable logs redact PR numbers, thread IDs, message text, and free-text errors
outside the existing sandbox exception, matching Countdown's current redaction
discipline.

### 6. Configuration and permission changes

All new behavior is default-off and env-over-TOML per VOY-1814.

| Surface | Proposed setting/change | Default / safety rule |
|---------|-------------------------|-----------------------|
| `config.toml` | `[clearance.author_wakeup] enabled` | `false` |
| `config.toml` | `pfc_door_url` | `http://localhost:8420/api/agent-send`; loopback-only URL validation |
| `config.toml` | `notify_after_minutes` (N) | `10`; positive integer |
| `config.toml` | `fallback_after_minutes` (M) | `20`; positive integer measured after terminal delivery |
| `config.toml` | `auto_review_fix` | `false` independently of notification enablement |
| `config.toml` | `allowed_repositories` | empty; exact `owner/name` entries only |
| `config.toml` | `audit_dir` | `~/.voyager/state/clearance-author-wakeup` |
| `bridge.env` | matching `CLEARANCE_AUTHOR_WAKEUP_*` overrides | temporary/emergency override only; malformed values fail closed |
| Existing `[review_fix]` | L3 envelope, audit dir, kill switch, round/fix caps | unchanged and still mandatory |
| Existing bridge allowlists | target repo must be allowed for both Clearance and Assembly/review-fix | no global fallback allowlist |

No new GitHub App permission is required. Notification and claim detection reuse
Clearance's existing PR/thread read access. Repair reuses the Assembly App's
existing Contents, Issues, and Pull requests write permissions and only on repos
where that App is already installed. The first intended external target,
`frankyxhl/alfred`, already appears in the Assembly installation registry, but
rollout must verify the live installation and the app-specific bridge allowlist
before enabling it. Do not grant Administration, Workflows write, secrets, or
new Countdown/Clearance Contents-write permission.

The PFC door is loopback-only and currently needs no new credential. If PFC later
adds authentication, its token is env-only in `bridge.env`, never committed to
TOML or the audit. Runtime files retain VOY-1814 permissions:
`bridge.env`/`config.toml` 0600, state directory 0700, and audit files 0600.

---

## Rollout and Rollback (VOY-1814)

1. **Implement and verify offline.** Add unit/BDD coverage for eligibility age,
   continuous-state reset, per-head batching, stable dedupe, door failures,
   claim evidence, same-head apply-time rechecks, finding-ID scope, and every
   review-fix refusal gate. Run only the touched test files and touched-file
   lint locally; CI runs the complete suite.
2. **Ship default-off.** Merge an approved CHG/implementation, build the wheel
   from a clean release checkout, install it into a versioned production venv,
   and atomically swap `~/.voyager/.venv` with the VOY-1814 `ln -s` +
   `mv -hf` recipe. Verify `vyg version` reports the intended version and commit.
3. **Back up private configuration.** Back up `~/.voyager/bridge.env` and
   `~/.voyager/config.toml`, preserve 0600 permissions, add only the new
   default-off keys, and validate parsing before restarting
   `com.iterwheel.voyager.bridge`. Do not edit secrets or unrelated allowlists.
4. **Notification-only sandbox canary.** Allowlist only
   `iterwheel/voyager-sandbox`, leave `auto_review_fix=false`, create one
   controlled state-A thread, and temporarily shorten N for the attended test.
   Prove exactly one PFC message contains the repo/PR/head/thread tuple, repeated
   reconciles dedupe, and the unresolved thread still makes merge-loop report
   `threads_unresolved`.
5. **Dry-run fallback canary.** Keep the sandbox scope, enable auto-fallback
   with global/route dry-run still true, shorten M for the attended test, and
   verify the review-fix audit reports the exact finding IDs with zero GitHub
   mutations. Repeat with a PR-author claim and prove no fallback starts.
6. **Single live canary.** Confirm the Assembly App installation and explicit
   Clearance + review-fix allowlists, set the review-fix envelope to one round
   and one fix for the canary, verify the kill switch, then enable one repository
   and one naturally occurring PR. Inspect PFC delivery, the local full audit,
   bridge logs, the verification result, the new head, and subsequent Clearance
   state. Keep `frankyxhl/alfred` as the first non-sandbox scope; do not expand
   until this evidence is recorded.
7. **Expand deliberately.** Restore the approved bounded envelope only after the
   live canary; add repositories one at a time to the app-specific allowlists.
   Verify `launchctl print`, local/public `/healthz`, and post-restart error logs
   after every production wheel/config change as required by VOY-1814.
8. **Rollback.** First set `CLEARANCE_AUTHOR_WAKEUP_ENABLED=false` and
   `CLEARANCE_AUTHOR_WAKEUP_AUTO_REVIEW_FIX=false`, then restart the bridge.
   If code rollback is required, atomically swap `~/.voyager/.venv` to the named
   prior version with `mv -hf`, restart, verify `readlink`, `vyg version`, and
   local/public health. Rollback stops future notifications/fallbacks; it does
   not undo a verified commit already pushed. Existing unresolved threads stay
   OPEN and Countdown continues to refuse merge, which is the safe state.

---

## Testing and Acceptance Criteria

- [ ] A continuously eligible author-keyed state-A thread produces no
  notification before N and exactly one batched PFC notification at/after N.
- [ ] The PFC message contains repository, PR number, current head SHA, and all
  due thread node IDs, plus N/M and a notification ID, with no secret material
  and no delivery-derived deadline.
- [ ] Door failure or ambiguous acknowledgement retries safely and cannot arm
  auto-fallback.
- [ ] Eligibility and claim detection key replies on the PR-author login;
  maintainer and bot replies neither move author-keyed state A to C nor claim
  the PR.
- [ ] PR-author reply, new head, thread resolution, or PR close before M records
  a claim/supersession and prevents fallback.
- [ ] At the locally computed terminal-delivery-plus-M deadline, the fallback
  re-reads the same head/state and scopes review-fix to the notified thread IDs
  only.
- [ ] Missing L3 envelope, repo allowlist, kill switch, dry-run, head mismatch,
  or verification failure refuses/rolls back exactly as the existing governed
  review-fix path does.
- [ ] No new path resolves a thread, approves a PR, or merges a PR.
- [ ] Merge-loop continues to return `threads_unresolved` until GitHub reports
  zero unresolved review threads.
- [ ] Notification and fallback are idempotent across duplicate webhooks,
  repeated reconcile ticks, timeouts, and bridge restart.
- [ ] Shareable audit output redacts non-sandbox PR/thread identifiers; local
  full-fidelity state remains 0600 under a 0700 directory.


## Open Questions

None for this proposal. The initial values are normative defaults:
`N=10` minutes to notify and `M=20` additional minutes after terminal PFC
delivery before auto-fallback. Repository rollout remains separately gated by
default-off configuration and an implementation CHG.

---

## Change History

| Date       | Change                                                                                                             | By    |
|------------|--------------------------------------------------------------------------------------------------------------------|-------|
| 2026-08-30 | Initial proposed design from the `frankyxhl/alfred#330` unattended state-A incident; no implementation             | Codex |
| 2026-08-30 | Addressed PR #318 Codex review: author-login-keyed wake-up eligibility and terminal-receipt-derived local deadline | Codex |
