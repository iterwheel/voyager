# REF-1825: Loop-Convergence Policy

**Applies to:** Voyager Assembly bot, Clearance automation, and managed repositories
**Last updated:** 2026-06-19
**Last reviewed:** 2026-06-19
**Status:** Active
**Date:** 2026-06-19
**Requested by:** Frank Xu (via issue #168)
**Priority:** P2
**Related:** VOY-1811, VOY-1822, VOY-1824, #152, #154, #157, #158

---

## What Is It

This REF documents the loop-convergence policy that ended the #152/#154
whack-a-mole. It defines three rules that govern when automated review loops
(Assembly, Clearance, AC spot-check) should fix a finding, accept it, or stop
trying entirely.

Before this document, these rules existed only in chat history. Future
contributors and bots now have a written policy instead of rediscovering it
under pressure.

This document does not replace VOY-1811 (multi-agent loop configuration),
VOY-1822 (Assembly-driven implementation loop), or VOY-1824 (failure
diagnostics). It is the policy layer that those SOPs reference for convergence
decisions.

## The Three Rules

### Rule 1 — False Positive (Over-Block) = Must Fix

**Definition:** A false positive occurs when a check blocks a correct
implementation — the patch satisfies the acceptance criteria but the check
rejects it.

**Policy:** Every false positive is a bug in the checking mechanism and MUST be
fixed at the source. The check is wrong; the patch (or its equivalent) should
have passed.

**Rationale:** False positives erode trust in automation. If a bot repeatedly
blocks correct work, operators learn to ignore or bypass the check, defeating
its purpose. The #152/#154 loop included several near-miss false positives
that consumed review rounds unnecessarily.

**Examples:**

| Scenario | Verdict | Why |
|----------|---------|-----|
| AC spot-check requires token `STAGE` but the implementation uses `stage` (case variant of the same value) | False positive — must fix | The check pattern is too narrow; fix the matcher to accept case variants when the AC permits them |
| AC spot-check flags a missing token that the implementation provides via a dynamic constant derived from the same value | False positive — must fix | The check matched literal text against AST-generated code; update the check to recognize dynamic derivation within the same value set |
| AC spot-check flags a missing token that is genuinely absent from the patch | Correct block | The finding is accurate; the patch is incomplete |

### Rule 2 — False Negative (Under-Block) = Acceptable

**Definition:** A false negative occurs when a check passes an incorrect
patch — the adoption criteria are not fully satisfied but the check does not
catch the gap.

**Policy:** False negatives are within the design tolerance of automated
checking. They are NOT bugs in the checking mechanism. Falls back to normal
human or Codex review.

**Rationale:** Automated checks are conservative by design. A check that aims
for zero false negatives inevitably produces so many false positives that it
becomes useless (Rule 1). The review pipeline — Codex review, Clearance panel,
human review — exists to catch what automated checks miss. A false negative
means the fallback worked.

A bot that over-reacts to false negatives (attempting to fix every theoretical
miss) reproduces the #152/#154 whack-a-mole: unbounded fix rounds chasing
edge cases within the design tolerance.

**Examples:**

| Scenario | Verdict | Why |
|----------|---------|-----|
| AC spot-check passes a patch that misuses a parameter in ways the check's structural tokens cannot detect | Acceptable false negative | The check only validates exact token presence; semantic correctness is for review |
| AC spot-check passes a patch that omits a non-listed acceptance sub-criterion that was implied by the parent AC but not enumerated | Acceptable false negative | The check only enforces enumerated tokens; implied requirements are for review |
| A check is advertised by name as "conservative AC token spot-check" and misses a finding that would require semantic understanding | Acceptable false negative | The check's documented scope is token-level, not semantic; the gap is a review concern |

### Rule 3 — Circuit Breaker / Max Rounds

**Definition:** The automated fix loop per PR is capped at a configurable
number of rounds (default: 8). Beyond that threshold, the loop halts and
escalates to a human instead of continuing indefinitely.

**Policy:** When `ASSEMBLY_MAX_FIX_ROUNDS` (default 8) is exceeded without
human approval:
1. No further auto-fix commit is pushed.
2. The `loop-circuit-broken` label is applied to the source issue.
3. An escalation comment is posted to the source issue and to the existing PR
   when one exists.

A human may override the circuit breaker only by approving the current PR head.
A plain comment such as "continue" is not a bypass. The bypass allows that
approved head to proceed, but it does not automatically reset existing
`assembly-fix-round-N` labels or the round counter. Operators who want a fresh
counter must explicitly clean up the source issue's circuit-breaker/fix-round
labels or start a new managed PR flow.

**Rationale:** Before #157, a single PR could accumulate ~24 bot-driven fix
commits (#152 → #154). Each round consumed tokens, review attention, and CI
time while producing diminishing returns. The circuit breaker transforms
unbounded "keep trying" into bounded "halt and escalate," which is safer and
more predictable.

**Implementation reference:** The circuit breaker is implemented as
`ASSEMBLY_MAX_FIX_ROUNDS` (env var, default 8) with `assembly-fix-round-N`
label tracking. See issue #157 and `voyager/bots/assembly/writeback.py`.

---

## How the Rules Interact

The three rules form a decision table for any finding in the automated loop:

| Finding classification | Action | Loop behavior |
|------------------------|--------|---------------|
| True positive (correct block) | Block publish, trigger auto-fix | Normal fix round; round counter increments |
| False positive (over-block) | **Must fix** the check | Escalate as a check bug; investigate after the immediate workaround |
| False negative (under-block) | **Accept** — fallback to review | Do not trigger auto-fix; do not increment round counter |
| Round count exceeds threshold | **Halt** — no more auto-fix attempts | Apply `loop-circuit-broken` label to the source issue, post escalation comments; human must unblock |

Current gates encode advisory behavior through the finding source and gate
status. For example, AC spot-check findings are token-level findings; when the
spot-check runs at L1 maturity, the adapter records
`ac_spotcheck_maturity = "L1"` and treats the result as advisory rather than
blocking. The direction-aware action task (#158) will make that policy explicit
by adding a structural `direction` field (`block` or `advisory`) derived from a
finding's source/type, not from prose patterns in the acceptance criteria.

---

## Related Tasks

| Task | Issue | Relation |
|------|-------|----------|
| AC spot-check blocking | #152 | Originating case — introduced conservative token-level AC checking |
| AC nesting preservation | #154 | Originating case — fixed AC structure for accurate spot-check attribution |
| Circuit breaker | #157 | Implements Rule 3 — caps automated fix rounds per PR |
| Direction-aware action | #158 | Implements Rules 1 and 2 structurally — findings carry `block`/`advisory` direction from their source |
| Decision memory | (planned) | Future task — will persist round-by-round decisions so the loop can reference past verdicts instead of re-deriving them |

---

## When to Apply This Policy

Use these rules when:

- Evaluating whether an automated finding blocks publish, triggers a fix
  round, or should be ignored.
- Deciding whether a bot check's behavior is a bug (false positive) or within
  design tolerance (false negative).
- Investigating a loop that has halted via the circuit breaker.
- Adding a new check or gate to Assembly or Clearance.
- Designing or reviewing a new automated review loop pattern.

---

## When NOT to Apply This Policy

Do not apply these rules to:

- Human review threads (humans use their own judgment, not automated policy).
- Manual PR approval or merge decisions (humans override the circuit breaker
  when appropriate).
- Security-critical gates where false negatives are not acceptable (treat as
  a special case with documented exceptions).

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-06-19 | Initial policy document — FP-must-fix, FN-accept, circuit breaker | Assembly |
