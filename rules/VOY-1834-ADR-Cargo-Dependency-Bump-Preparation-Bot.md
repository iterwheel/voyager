# ADR-1834: Cargo Dependency-Bump Preparation Bot

**Applies to:** VOY project
**Last updated:** 2026-06-28
**Last reviewed:** 2026-06-28
**Status:** Proposed
**Related:** VOY-1800 (Design Principles), VOY-1802 (Bot Roster), VOY-1804 (Naming ADR), VOY-1805/1806/1807 (GitHub App identity), VOY-1811 (loop config), VOY-1816 (canary expansion), VOY-1825 (loop convergence), COR-1625 / COR-1626 (autonomy levels)
**Founds:** issue #238

---

## What Is It?

A record of the decision to promote the reserved **Cargo 📦** roster slot
(VOY-1802 Future Roster) into a Current-roster factory bot, and of the boundaries
that define it.

> **Cargo's single responsibility:** *prepare dependency-bump PRs so they can be
> verified and merged — and never merge them.*

Rocket stage: **Payload Cargo** — the cargo team loads and prepares the payload;
the flight director (Countdown) calls GO/NO-GO. Cargo never decides whether the
mission launches.

This ADR is Phase 0 of a larger program (see §Decision #6): it founds Cargo via
governance only — no code. The operational SOP and the implementation are
downstream issues.

> **Status gate:** while this ADR is `Proposed`, VOY-1802 keeps Cargo in the
> **Future Roster** (reserved) with a pointer here. Cargo promotes to the **Current
> Roster** only when this ADR is **Accepted** — so document status never contradicts
> the roster.

---

## Context

Dependency-bot (Dependabot and similar) version-bump PRs across Iterwheel-managed
repos need the same mechanical preparation every time:

- rebase onto a fresh `main` (strict up-to-date branch protection makes them go `BEHIND`)
- **regenerate the lockfile** when the bump touches it (`uv.lock` /
  `requirements-*.txt` / `package-lock.json`) — GitHub native auto-merge cannot do this
- classify the bump (patch / minor / major)
- push so CI can run

No factory bot owns this today; it is done by hand (origin: a 7-PR manual run on
`frankyxhl/trinity` — #231 `actions/checkout` major → escalated; #261 coverage,
#235 `certifi` → lockfile-regen prep). The roster already reserves the right name
and stage for it — **Cargo | Dependency / package management | Payload cargo**.

The open question was not *whether* to automate this, but *how to classify it* so
it does not collide with bots that already exist — chiefly **Assembly 🔧** (which
also writes commits, branches, and PRs) and **Countdown ⏱️** (which owns the
merge). Getting those two boundaries right is the substance of this decision.

---

## Decision

### 1. Cargo is an independent "prepare + verify" bot — hands, not brain

Cargo applies an **external version bump** authored by no one on the team and
produces **no new behavior**. It only prepares the branch and reports facts. It
makes no merge judgment. This squeezes Cargo into a niche nobody owns: **pure
payload preparation**.

### 2. Boundary vs Assembly 🔧 (the boundary missing from issue #238)

| | Assembly 🔧 | Cargo 📦 |
|---|---|---|
| Trigger | issue (a functional intent) | dependency-bot PR (an external event) |
| Output | **new behavior** (code we authored) | **no new behavior** (a version bump we did not author) |
| Success | the feature is implemented | the bump is prepared and verifiable |
| On failure | iterate until implemented | stop, escalate — never author code to adapt |

The line: *if it is not something we developed, it is not Assembly.* Adapting
calling code to a breaking dependency change **is** functional work and belongs to
Assembly / a human — not Cargo.

### 3. Boundary vs Countdown ⏱️ / Tanking / Static Fire 🔥 / Liftoff 🚀

- **Countdown ⏱️ owns all merge judgment** — go/no-go, the safe-subset policy, and
  the merge action itself. Cargo hands prepared PRs to Countdown's gate and stops.
  Cargo surfaces *facts* (semver level, lockfile touched?, non-dependency source
  touched?) as labels; Countdown turns facts into a *decision*.
- **Tanking** (reserved) owns conflict detection; Cargo consumes that signal, does
  not duplicate it.
- **Static Fire 🔥** owns CI aggregation; Cargo pushes so Static Fire can run, and
  consumes the result, but does not run or aggregate CI itself.
- **Liftoff 🚀** is release/deploy, post-merge — not involved. The Cargo auto chain
  stops at **merge to `main`**.

### 4. Identity

Cargo acts as an Iterwheel **GitHub App bot account** (per VOY-1805 / VOY-1806 /
VOY-1807), not a personal account.

### 5. Autonomy: Cargo starts at L2; the L3 goal lives in Countdown

- **Cargo itself is L2 — Assisted + Gated (COR-1625):** it prepares; the merge is
  gated. Cargo never merges, so it has no L3 surface of its own.
- On CI failure (an *unexpected* event): Cargo **does not merge, opens a GitHub
  issue, and notifies a human**. It never edits application code to force tests green.
- The operator goal — **scoped-L3 auto-merge of a safe subset** (patch/minor +
  non-source + allowlisted repo, CI green) — is a **Countdown** capability, not a
  Cargo one. It is recorded here as a path, not claimed now. Its single hard
  precondition is **automated rollback** (COR-1626 envelope #5): a squash-merge to
  `main` has no clean automated undo *in general*, but a dependency-bump commit is
  the easiest possible case to auto-`revert`. That rollback drill is owned by
  Countdown (the merge owner) and gates any L3 promotion.

### 6. This is a 3-phase program; this ADR is Phase 0

The operator goal cannot be delivered by issue #238 alone — it spans three roles:

- **Phase 0 — issue #238 (this ADR):** found Cargo. Governance only.
- **Phase 1 — downstream issue:** implement Cargo (prepare / classify / label /
  escalate / self-heal sweep) + canary onto one managed repo (VOY-1816). The full
  chain runs; merge stays human (L2).
- **Phase 2 — downstream issue:** give **Countdown** the scoped-L3 auto-merge
  capability for Cargo-fact-labeled safe bumps, gated on the automated `revert`
  rollback drill. The L3 risk concentrates here — Countdown gaining an
  unattended-merge + rollback capability — not in Cargo.

### 7. No reverse communication — decoupled pull

Iterwheel bots are separate GitHub Apps with no direct channel; all communication
is mediated by GitHub-visible state (labels / comments / check-runs / status),
re-dispatched by the webhook bridge (VOY-1802 "Observable"). The only reverse need
— re-preparing a branch that goes `BEHIND` again between prep and merge — is
**designed away**: Countdown simply emits NO-GO on a stale branch, and Cargo keeps
its own payloads fresh via its **own sweep loop** (on push-to-`main` and/or
scheduled), reusing the existing `_stale_pr_loop` / `_ci_failing_loop` pattern,
composed with VOY-1811 loop config + VOY-1825 convergence. Countdown never calls
Cargo. Zero coupling.

---

## Consequences

### Positive

- ✅ Both adjacency boundaries are now sharp: Cargo ≠ Assembly (no authored
  behavior) and Cargo ≠ Countdown (no merge judgment) — satisfies VOY-1800
  §Design Principles #2 (single responsibility) and #1 (narrative consistency).
- ✅ A recurring cross-repo toil (manual rebase + multi-language lockfile regen)
  gets an owner.
- ✅ The L3 risk is isolated to one small, well-gated capability in Countdown,
  reached only after an explicit rollback drill — not smeared across the pipeline.
- ✅ The reverse-communication problem is dissolved, not built — fewer moving parts.

### Negative / Trade-offs

- ⚠️ Cargo, once judgment is removed, is "only hands." Accepted: cross-repo rebase +
  multi-language lockfile regeneration is substantial, recurring, and owned by no
  one today — it earns a bot. Keeping judgment out is precisely what keeps it from
  colliding with Assembly and Countdown.
- ⚠️ Lockfile regeneration runs package managers (`uv lock`, `npm install`), which
  can execute arbitrary dependency install scripts — a supply-chain surface. The
  execution environment and identity are an explicit constraint for the Phase 1 SOP.
- ⚠️ The operator's headline goal (unattended patch merges) is **not** delivered by
  this ADR; it requires Phases 1–2. This ADR only makes that path legible.

### Triggers for Revisiting

- Cargo is asked to adapt application code to a breaking bump (would erase the
  Assembly boundary) — needs a new ADR.
- The scoped-L3 auto-merge is wanted *before* the automated-rollback drill exists
  (would violate COR-1626) — needs an amendment.
- A second dependency-management concern appears that does not fit "prepare a
  bump PR" (e.g., authoring dependency upgrades proactively) — re-evaluate the
  Cargo/Assembly split.

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-06-28 | Initial version — founds Cargo (issue #238 Phase 0); adds the Assembly boundary and the 3-phase / L3-in-Countdown program | Claude Code |
