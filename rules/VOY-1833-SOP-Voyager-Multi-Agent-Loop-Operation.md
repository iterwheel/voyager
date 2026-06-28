# SOP-1833: Voyager Multi-Agent Loop Operation

**Applies to:** VOY project (`iterwheel/voyager`)
**Last updated:** 2026-06-28
**Last reviewed:** 2026-06-28
**Status:** Active
**Related:** COR-1500 (TDD Development Workflow), COR-1617 (Multi-Agent Workflow Loop), COR-1618 (Out-of-Band Consent Auto-Pick), COR-1619 (Orchestrator vs Worker Dispatch), COR-1622 (Multi-Agent Loop Project Configuration), VOY-1811 (Multi-Agent Loop Configuration), VOY-1822 (Assembly-Driven Implementation Loop), VOY-1825 (Loop-Convergence Policy), VOY-1832 (Codex Review Loop)

---

## What Is It?

The Voyager operator SOP for running the COR-1617 multi-agent workflow loop in
`iterwheel/voyager`.

This SOP is the procedural entry point. `VOY-1811` remains the parameter source:
repository identity, consent gates, worker dispatch, review panel, bot actors,
runtime profile, R-count limits, and completion-gate bindings. When an operator
says `follow VOY-1811`, agents should route to this SOP and use `VOY-1811` as
the configuration reference.

---

## Why

`VOY-1811` is a REF because it instantiates COR-1622 parameters for Voyager. Over
time, operational guidance grew around that parameter table: invocation phrases,
autonomous-operation rules, Codex review triggering, runtime notes, and the
completion gate. Keeping those rules only in a REF makes `af plan VOY-1811`
ambiguous and encourages agents to treat a configuration document as a workflow.

This SOP gives the loop a real procedural document while preserving `VOY-1811`
as the stable config source.

---

## When to Use

Use this SOP when:

- The operator asks to `follow VOY-1811`, `follow VOY-1811 once`, or
  `follow VOY-1811 for #N`.
- A Voyager issue should be handled through the COR-1617 multi-agent loop with
  Voyager-specific parameters.
- The work needs plan review, TDD worker dispatch, PR convergence, Codex review,
  Clearance readiness, handoff, and completion-gate discipline as one loop.

---

## When NOT to Use

Do not use this SOP when:

- You only need to look up Voyager loop parameters; read `VOY-1811` directly.
- The task is only a PR review-response loop; use `VOY-1832` and COR-1612.
- The task is Assembly-managed implementation work; use `VOY-1822`, which is the
  Assembly-specific implementation-loop SOP derived from `VOY-1811`.
- The work is a release; use `VOY-1810`.
- The request is a normal small code or documentation change that does not need
  the COR-1617 loop.

---

## Prerequisites

- Run `af guide --root .` and route the task before starting.
- Confirm GitHub-visible writes use `ryosaeba1985` per WUK-2100.
- Read `VOY-1811` for current parameter values before dispatching reviewers,
  workers, PR operations, bot polling, or completion gates.
- If using Codex personal custom agents, ensure `test_writer` and `implementer`
  exist under `~/.codex/agents/`; otherwise use the fallback rows in `VOY-1811`
  and keep RED and GREEN authorship distinct.
- If the invocation does not name an issue, enforce the COR-1618 consent and
  intake-quality gates before selecting work.

---

## Steps

1. **Declare the active process.** Use COR-1402 and declare this SOP plus the
   relevant COR-1617 phase or overlay. Apply COR-1500 for code changes.

2. **Classify the invocation.** Treat the operator phrase as one of these three
   modes:

   | Invocation | Behavior |
   |------------|----------|
   | `follow VOY-1811` | Looping mode. Select work only after COR-1618 consent and `blueprint-ready` intake quality pass. After mergeable handoff and retrospective, Phase 12 may restart the loop if the runtime supports safe wakeups. |
   | `follow VOY-1811 once` | Same gated selection as looping mode, but stop after Phase 11 for the selected issue. |
   | `follow VOY-1811 for #N` | User-directed issue selection. Bypass COR-1618 consent per the Normative Bypass Clause and run phases 2-11 for the named issue. |

   `follow VOY-1810` is never an alias for this workflow; `VOY-1810` is the
   release-process SOP.

3. **Load Voyager parameters.** Read `VOY-1811` and bind the current values for
   repository, trusted reactors, write identity, PR remote, review panel, worker
   agents, bot actors, runtime profile, retry policy, R-count cap, and completion
   gate.

4. **Select or verify the target issue.** For non-`for #N` invocations, verify
   the trusted rocket reaction and `blueprint-ready` intake quality before
   picking work. For `for #N`, record that the operator named the target issue in
   live chat.

5. **Prepare branch and identity.** Confirm `gh auth status` uses
   `ryosaeba1985` before GitHub-visible writes. Create or reuse the feature
   branch according to `VOY-1811`'s `<pr-push-remote>` value.

6. **Plan the change.** Create the plan artifact required by COR-1617 and the
   task type, normally CHG-shaped per `VOY-1811`'s `<spec-format>`. Run the
   plan-review panel from `VOY-1811`; all viable reviewers must meet the
   configured pass threshold with no blockers.

7. **Dispatch implementation with TDD boundaries.** For substantial changes,
   use the configured RED worker for tests and GREEN worker for implementation.
   The RED worker may edit only tests, fixtures, and test helpers. The GREEN
   worker may edit production or supporting files, but must not weaken the RED
   tests. If personal Codex agents are unavailable, use the fallback dispatch
   rows in `VOY-1811`.

8. **Verify locally.** Run the relevant unit, integration, BDD, lint, format,
   typecheck, security, and project-specific checks before pushing. If the spec
   and implementation diverge and the correct direction is unclear, pause for
   operator policy input.

9. **Open or update the PR.** Push to the configured remote and open a ready PR,
   not a draft, unless the operator explicitly requests draft state. Include the
   issue linkage, validation evidence, and any loop-specific notes needed by
   reviewers.

10. **Converge the PR.** After each push, use `VOY-1832` to trigger and poll
    Codex review on the current head. Address actionable P0/P1/P2 findings,
    wait for CI and Clearance, and repeat until Codex has no major issues,
    required checks pass, and Clearance is no longer blocking. Respect the
    `VOY-1811` R-count cap and `VOY-1825` where Assembly-managed convergence
    policy applies.

11. **Handoff for approval and merge.** Do not approve or merge on behalf of the
    required human reviewer. Report the exact remaining gate if the PR is clean
    but blocked on human approval.

12. **Run the completion gate.** Before reporting Phase 11 complete, perform the
    `VOY-1811` related-PR review-thread sweep and delayed-review sweep. Do not
    report completion while any related PR has unresolved actionable review
    feedback or while target issue closure is still pending.

13. **Retrospect and decide restart.** Record notable process gaps, SOP updates,
    and follow-ups. For `follow VOY-1811`, restart only when the selected runtime
    can preserve the wakeup, stop-marker, and branch-guard semantics recorded in
    `VOY-1811`.

---

## Autonomous Operation Contract

Once the consent gate clears, the agent should continue through Phases 2-11
without asking routine permission between phases, polling cycles, or adjacent
loop iterations. Polling CI, Codex, and Clearance during PR convergence is part
of the loop.

Valid pause points are limited to:

- Consent gate failure.
- Spec versus implementation divergence where the correct resolution is not
  obvious.
- A finding that requires operator policy input, such as a new permission grant,
  GitHub App installation, or branch-protection change.
- Operator-only credentials or authorization.
- R-count hard stop at the configured cap.

---

## Examples

**Run one named issue through the loop:**

```text
follow VOY-1811 for #230
```

The agent routes to `VOY-1833`, loads parameters from `VOY-1811`, records that
the operator named the issue, and runs COR-1617 phases 2-11 without the consent
auto-pick gate.

**Run one consent-gated issue and stop after retrospective:**

```text
follow VOY-1811 once
```

The agent routes to `VOY-1833`, verifies the trusted reaction plus
`blueprint-ready` intake quality, selects one issue, and stops after Phase 11.

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-06-28 | Initial SOP separating the Voyager loop operating procedure from the VOY-1811 parameter REF. | Codex |
