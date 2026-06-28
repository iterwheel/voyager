# SOP-1832: Codex Review Loop

**Applies to:** VOY project
**Last updated:** 2026-06-28
**Last reviewed:** 2026-06-28
**Status:** Active

---

## What Is It?

The procedure for driving the **Codex automated PR review** to a clean verdict: how
to trigger a review, how to *reliably* detect its result, and how to close the
resulting threads. This SOP is the source of truth for the contract; the tested
Python helper `scripts/codex_review_watch.py` implements the watch loop.

## Why

Codex's GitHub surface has three traps that break naive `gh api` polling — all hit
repeatedly during PR #224 (8+ rounds), causing hours of false waits and false signals:

1. **Pagination (the big one).** `/pulls/{n}/reviews` and `/pulls/{n}/comments` return
   **30 per page, oldest-first**, and `gh api` does **not** auto-paginate. Without
   `--paginate` you only ever see the 30 OLDEST items (the stale re-anchored comments)
   and NEVER the newest verdict, which sits on a later page. This masquerades as "codex
   stalled / rate-limited" — it is not; codex reviewed fine, the poller was blind.
   (PR #224: 4 "stalls" in a row were all this bug.) With `--paginate`, note that
   `--jq '...|length'` emits a length *per page* — emit one line per match and count
   with `wc -l`.
2. **Trigger dedupe.** Posting `@codex review` again too soon is silently dropped — the
   review never runs. Confirm Codex's 👀 ack on your trigger comment.
3. **Comment re-anchoring.** An unresolved old review comment is re-anchored to the new
   head commit, so it carries the **new** `commit_id` but keeps its **old** `created_at`.
   Detecting "new findings" by `commit_id == head` false-positives on stale, already-
   fixed comments — key on `created_at` instead.
4. **Clean verdict surfaces.** Codex may report a clean review as a PR comment such as
   `Codex Review: Didn't find any major issues` with a `Reviewed commit:` line, not only
   as a thumbs-up reaction. A clean comment is valid only when its reviewed commit
   matches the current PR head prefix and no newer inline findings exist.

Without a documented contract these mistakes recur every session.

---

## When to Use

- Any PR that must pass the Codex gate before merge (the standard for this repo).
- After each push that addresses prior Codex findings (re-review the new head).

## When NOT to Use

- Repos / PRs where Codex review is not enabled.
- Hand-rolling one-off `gh api` polling — use the script; the traps above are not
  obvious and cost hours when missed.

## Steps

1. **Trigger.** Post `@codex review` via `gh pr comment <PR>` as `ryosaeba1985` (the
   public-write identity; see `~/.claude` GitHub Identity Rule).
2. **Confirm the ack.** Verify Codex reacted 👀 to *your* trigger comment within ~60s.
   No ack ⇒ the trigger was dropped (dedupe) ⇒ re-trigger.
3. **Record the cutoff.** Capture the trigger comment's `created_at` as `SINCE`.
4. **Poll for a genuine verdict** — always `gh api --paginate` (the lists are 30/page,
   oldest-first); key on `created_at/submitted_at > SINCE`, **never** on `commit_id` or
   raw counts. Count matches with `wc -l`, not `--jq length` (which is per-page under
   `--paginate`):
   - **Findings** = ≥1 Codex inline comment in `/pulls/{n}/comments` with `created_at > SINCE`.
   - **Clean** = either:
     - a Codex clean summary PR comment after `SINCE` whose `Reviewed commit:` value
       matches the current head SHA prefix, or
     - a 👍 reaction by Codex with no PR head change since the trigger.
     In both cases, there must be no new inline comments after `SINCE`.
5. **Be patient; don't kill the poll early.** Codex can ack then stall. Use a generous
   timeout (20–30 min) and re-trigger after timeout rather than aborting.
6. **Fix and re-loop.** Address findings, push, then return to Step 1 on the new head.
7. **Resolve threads** once findings are fixed via the machine account — see
   `VOY-1828`-era redaction rules and the project memory `resolve-as-countdown-user`;
   resolves run as `iterwheel-countdown-bot`, never the human identity. Outdated
   threads are resolvable by default (`VOY-1831`; `_should_resolve` does not gate on
   `isOutdated`).
8. **Done** when Codex returns a clean verdict on the current head with zero open
   findings. Before accepting a clean verdict, re-check the PR head SHA: a 👍 is a
   PR-level reaction carrying no commit, and a clean summary comment is valid only for
   the `Reviewed commit:` it names. If a new commit landed mid-wait, re-trigger on the
   new head rather than green-lighting it.

### Reference implementation

Tracked helper: `scripts/codex_review_watch.py <PR> [--repo OWNER/REPO]
[--no-trigger] [--timeout-min N] [--since ISO8601]` — exit `0` clean, `2`
findings (printed), `1` error/timeout. The implementation lives in
`voyager.core.codex_review_watch` so verdict classification, pagination, retry, and
head-move behavior stay covered by unit tests.

---

## Examples

**Trigger and wait for a verdict on PR #224:**

```bash
scripts/codex_review_watch.py 224 --timeout-min 25
# → exit 2 + printed findings, OR exit 0 with a clean Codex verdict for the current head
```

**Keep waiting without re-triggering** (a trigger already fired this round):

```bash
scripts/codex_review_watch.py 224 --no-trigger
```

**Anti-pattern (do NOT do this).** No `--paginate`, and keying on the head SHA:

```bash
# WRONG on two counts:
#  - no --paginate → only the 30 OLDEST comments (stale re-anchors); the new verdict is
#    on a later page and is never seen → looks like "codex stalled".
#  - commit_id match → re-anchored OLD comments carry the new commit_id → false findings.
gh api repos/iterwheel/voyager/pulls/224/comments \
  --jq '[.[] | select(.commit_id|startswith("<head>"))] | length'
```

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-06-28 | Replaced the bash watch helper with tested Python helper `scripts/codex_review_watch.py` for issue #225. | Codex |
| 2026-06-28 | Updated the machine-account resolver login to `iterwheel-countdown-bot` after issue #226 account rename | Codex |
| 2026-06-28 | Accept Codex clean summary comments with matching `Reviewed commit:` as clean verdicts, not only thumbs-up reactions. | Codex |
| 2026-06-28 | Initial version — codifies the PR #224 Codex-loop lessons (trigger dedupe, list-endpoint lag, comment re-anchoring) | Claude Code |
| 2026-06-28 | Corrected root cause: pagination (30/page oldest-first, no auto-paginate) — the earlier "list-endpoint lag" framing was wrong; the 4 apparent "stalls" were missed later pages | Claude Code |
