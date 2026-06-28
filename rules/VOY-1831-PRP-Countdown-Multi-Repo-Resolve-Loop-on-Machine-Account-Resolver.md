# PRP-1831: Countdown Multi-Repo Resolve-Loop on Machine-Account Resolver

**Applies to:** VOY project — Countdown review-thread resolution
**Last updated:** 2026-06-28
**Last reviewed:** 2026-06-28
**Status:** Draft
**Related:** #222 (resolve-conversation tool), VOY-1830 (dedicated-PAT removal), VOY-1815 (Clearance DeepSeek Profile Policy)

---

## What Is It?

A multi-repo review-thread resolve loop, `vyg countdown resolve-loop`, that runs
**as the fixed machine account** (`iterwheel-countdown-user`) on top of the #222
`resolve_conversation` resolver — no dedicated PAT, no GitHub-App identity. For each
allowlisted repo it enumerates open PRs, deterministically prefilters the threads the
machine account can resolve, asks an **LLM should-resolve gate** whether each
candidate is actually addressed, and resolves only the approved ones — under a
single-instance lock, a blast-radius cap, and a redacted audit trail.

This rebuilds the orchestration intent of the closed #221 `feat/countdown-resolve-loop-core`
(which was PAT-coupled) on the new machine-account resolver, with NO dedicated-PAT
coupling.

---

## Problem

#222 resolves one PR/thread at a time, operator-invoked. The org needs the same
machine-account resolution applied periodically across repos without a human
copy-pasting commands — but **un-gated** deterministic auto-resolve would close
*every* mechanically-resolvable thread, including ones whose concern was never
addressed. A safety layer is required before unattended resolution is sane.

## Proposed Solution

### Safety model (the crux): LLM gate = fail-closed veto, never an expander

1. **Deterministic prefilter first.** A thread is a candidate only if the machine
   account can mechanically resolve it (`resolve_conversation._should_resolve`
   semantics: unresolved, `viewerCanResolve`, `viewerCanReply`; outdated threads ARE
   candidates — `viewerCanResolve` is the authorization, outdated just means the line
   moved). The
   LLM never sees a non-candidate and cannot promote one.
2. **LLM can only say "no".** It returns `{should_resolve: bool, reason}`. `true`
   lets a candidate proceed; anything else (false, parse failure, refusal, error,
   low confidence) **defaults to skip**. Injection toward "resolve everything"
   cannot exceed the candidate set; worst case = deterministic behavior minus vetoes.
3. **Untrusted-content framing.** Thread comment bodies are framed as DATA; the
   system prompt forbids following instructions embedded in them (reuses the
   `clearance/investigator.py` structured-verdict + robust-JSON pattern).
4. **Evidence freshness.** Two-level, not by a PR head SHA. (a) Thread state:
   `resolve_conversations` re-fetches the thread immediately before the mutation and
   skips it unless still mechanically resolvable. (b) Comment evidence: the loop re-reads
   the thread's live comment count just before mutating and skips (`skipped_stale`,
   `comments_changed`, fail-closed on an unreadable count) if it differs from what the
   gate judged — so a reviewer commenting between judgment and action is not overrun.
5. **Identity gate.** The resolve mutation goes through `resolve_conversations`,
   which hard-gates `viewer.login == iterwheel-countdown-user` and is resolve-only.
6. **Blast radius.** `--max-resolves` caps resolves per run (`capped=True`, never
   silent truncation). Single-instance `flock` prevents concurrent pile-ups.
7. **Redaction (VOY-1828).** Non-sandbox repos never emit raw PR numbers / thread
   node IDs to terminal/JSON/audit; only `iterwheel/voyager-sandbox` may.

### Architecture

New module `voyager/core/countdown_loop.py` + a `resolve-loop` CLI command. Two GitHub
clients, both from the **same machine-account token** (`read_machine_token`):

- **read client** (loop-owned, read-only): open-PR enumeration + per-PR
  review-thread+comments query. Separate from the resolve client so broad reads never
  widen the resolve path.
- **resolve client**: `resolve_conversation.make_github_gql` (operation-allowlisted,
  resolve-only), driven via `resolve_conversations(repo, thread_id=…)`.

Ported from #221 (de-PAT'd, mechanism-agnostic): `single_instance_lock`,
`gate_repos(ceiling=RESOLVE_ALLOWED_REPOS)`, `_list_open_pr_numbers`, `LoopSummary`
(redaction / `capped` / `systemic_failure` / per-target `TargetError`), `load_repo_list`.
New: `ShouldResolveGate` (DeepSeek-backed; injectable for tests) + the
candidate→gate→resolve pipeline + a redacted JSONL audit writer.

```
lock → gate_repos(requested ∩ RESOLVE_ALLOWED_REPOS)
  for repo in allowed:
    for pr in open_pr_numbers(repo):
      for thread in review_threads(repo, pr):
        if not deterministic_candidate(thread): continue
        verdict = gate.should_resolve(thread)          # fail-closed
        if not verdict.should_resolve: audit(skip); continue
        if dry_run: audit(would_resolve); continue
        if resolved >= max_resolves: capped=True; break
        resolve_conversations(repo, thread_id=thread.id, gql=resolve_client)
        audit(result)
→ LoopSummary (redacted)
```

### CLI

`vyg countdown resolve-loop --repos <file> [--max-resolves N] [--dry-run] [--json]`
- `--repos`: `OWNER/REPO`-per-line file; each must also be in the ceiling allowlist or
  it is reported as skipped.
- `--dry-run`: enumerate + gate (incl. LLM judgment) but issue no mutation.
- `--json` emits the redacted dict. There is NO `--show-raw`: redaction keys only on repo
  membership (sandbox shows raw IDs, non-sandbox never does), with no operator override.

### Testing

- Deterministic core (fakes, no network/LLM): lock contention, gate split, pagination
  / cursor cycles, prefilter matrix (fail-closed on None), `systemic_failure`, cap,
  redaction, repo-list parsing.
- Gate: injected fake `ShouldResolveGate` for pipeline tests; focused tests for the
  DeepSeek adapter's prompt framing + fail-closed parsing (mirrors investigator tests).
- Hard invariant: a prompt-injected "resolve everything" verdict cannot resolve a
  non-candidate; a gate error/parse-failure defaults to skip.

## Open Questions

- **Audit location & rotation:** start with a single redacted JSONL under
  `~/.voyager/` (append, `LOCK_EX`); rotation deferred.
- **LLM evidence scope:** v1 feeds the thread's own comments + PR title/number context.
  Feeding the diff hunk the thread anchors to is a later enhancement.
- **Scheduling:** launchd/unattended runs are out of scope here (separate SOP/CHG,
  mirrors VOY-1814) — ship the command, schedule after attended dry-run → attended live.

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-06-28 | Initial PRP | Claude Code |
