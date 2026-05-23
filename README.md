# Voyager 🛰️

> *"We don't ship code. We launch rockets."*

**Voyager** is the first long-range vessel built by **Iterwheel** — a precision
rocket factory for code, automation, and multi-agent systems.

This repository is the founding charter. Code arrives later; the constitution
ships first.

---

## What is Iterwheel?

**Iterwheel** = **Iter**(ation) + (Fly)**wheel**

Two ideas stacked into one word:

- **Iteration** — stepwise improvement; each round is a little better than the last
- **Flywheel** — accumulated momentum; slow at first, exponential later, never stops

> Every iteration adds a little kinetic energy to the flywheel.
> The longer it spins, the faster it goes.

That belief drives everything in this repo.

---

## Why "Voyager"?

In 1977, NASA launched two probes that were only meant to study Jupiter and Saturn.
Nearly **50 years later**, both Voyagers are still flying — and still transmitting
data home. They are the farthest human-made objects in history.

Voyager embodies the Iterwheel spirit at every level:

| Iterwheel Spirit | How Voyager Shows It |
|------------------|----------------------|
| **Compound** | Half a century of flight; time keeps taking it farther |
| **Flywheel** | Gravity assists from planets accelerate it; every flyby spins the wheel faster |
| **Iteration** | Every transmission is a new discovery |
| **Spiral ascent** | Jupiter → Saturn → Uranus → Neptune → interstellar space |
| **Never returns** | Once launched, only forward — that is what `release` means |

Full reasoning: [`rules/VOY-1801`](rules/VOY-1801-REF-Voyager-Mission-Charter.md).

---

## The Rocket Factory Pipeline

Every code change at Iterwheel is treated as a launch mission. Each stage is
owned by exactly one bot:

```
Blueprint → Stack → Assembly → Static Fire → Clearance → Countdown → Liftoff
   📐         🛰️        🔧          🔥             ✅           ⏱️           🚀
 Design    Classify  Implement   Ground test     Polling     T-minus      Launch
```

| # | Bot | Stage | GitHub Job |
|---|-----|-------|------------|
| 1 | **Blueprint** 📐 | Mission blueprint | Issue intake and title validation |
| 2 | **Stack** 🛰️ | Vehicle stacking | Issue classification labels |
| 3 | **Assembly** 🔧 | Implementation | Branch, code, commit, PR — write work only |
| 4 | **Static Fire** 🔥 | Ground test | CI / test aggregation |
| 5 | **Clearance** ✅ | Go / No-Go poll | Review status aggregation |
| 6 | **Countdown** ⏱️ | T-minus | PR convention checks and final merge gate |
| 7 | **Liftoff** 🚀 | Launch | Release / deploy |

Reserved future slots: **Manifest**, **Caliper**, **Tanking**, **Apogee**, **Telemetry**.
Assembly graduated from reserved to active (see VOY-1802, issue #67).
See [`rules/VOY-1802`](rules/VOY-1802-REF-Bot-Roster-Rocket-Factory.md) for the full spec.

---

## Document Map

The constitutional documents under `rules/` are the canonical source of truth.
Read them in order on a first visit:

| Doc | Type | Purpose |
|-----|------|---------|
| [VOY-1800](rules/VOY-1800-REF-Iterwheel-Founding-Philosophy.md) | REF | Founding philosophy: Iterwheel meaning, core values, design principles |
| [VOY-1801](rules/VOY-1801-REF-Voyager-Mission-Charter.md) | REF | Voyager mission charter + sister-mission naming space |
| [VOY-1802](rules/VOY-1802-REF-Bot-Roster-Rocket-Factory.md) | REF | Bot roster: the rocket factory pipeline |
| [VOY-1803](rules/VOY-1803-REF-Visual-and-Voice-Identity.md) | REF | Visual and voice identity (aerospace tone) |
| [VOY-1804](rules/VOY-1804-ADR-Naming-Convention-Iterwheel-Voyager-Aerospace-Bots.md) | ADR | Naming convention decision (and rejected alternatives) |
| [VOY-1805](rules/VOY-1805-SOP-GitHub-Bot-Accounts-and-Responsibilities.md) | SOP | GitHub bot account roster + responsibilities |
| [VOY-1806](rules/VOY-1806-SOP-GitHub-App-Permission-Matrix.md) | SOP | GitHub App permission matrix |
| [VOY-1807](rules/VOY-1807-REF-GitHub-App-Registry.md) | REF | GitHub App registry + webhook state |
| [VOY-1808](rules/VOY-1808-ADR-Cross-Account-Installation-for-Iterwheel-GitHub-Apps.md) | ADR | Cross-account installation strategy |

The index at [`rules/VOY-0000`](rules/VOY-0000-REF-Document-Index.md) is auto-regenerated
by `af index`.

---

## Design Principles

Any new bot, sub-project, or major component **must** satisfy these five
principles (from VOY-1800):

1. **Narrative consistency** — fits the rocket-launch story
2. **Clear responsibility** — single responsibility per component
3. **Extensibility** — new components find a matching rocket-stage word
4. **Sense of ceremony** — workflow as ceremony, not just process
5. **Compound spirit** — every Liftoff is the start of the next iteration

Anything that breaks the rocket narrative needs a new ADR. Don't break the story
silently.

---

## Voice

When bots speak — on PRs, issues, status checks — they speak as flight controllers,
not as utilities.

- **Pass:** *"All engines nominal."* / *"All stations report GO."* / *"v1.2.0 has cleared the tower."*
- **Hold:** *"Hold, hold, hold."* / *"Stack misalignment detected."* / *"NO-GO from Reviewer Station."*

Self-check: would a real flight director say this on a live mission? If not, rewrite.
Full guide: [`rules/VOY-1803`](rules/VOY-1803-REF-Visual-and-Voice-Identity.md).

---

## Status

🛠️ **Pre-launch.** This repo currently holds the founding charter plus the first
GitHub App operating rules. The public bot identities are
`iterwheel-blueprint`, `iterwheel-stack`, `iterwheel-staticfire`,
`iterwheel-clearance`, and `iterwheel-countdown`.
`iterwheel-assembly` is the next bot to be created (see VOY-1807).

---

## The Iterwheel Way

- Every PR is a mission
- Every Review is a Go/No-Go poll
- Every Release is a Liftoff
- Every iteration spins the flywheel a little faster

**Welcome to Iterwheel.** 🌀
**Godspeed, Voyager.** 🛰️
