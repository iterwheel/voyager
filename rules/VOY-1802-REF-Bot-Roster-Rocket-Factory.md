# REF-1802: Bot Roster (Rocket Factory)

**Applies to:** VOY project
**Last updated:** 2026-05-09
**Last reviewed:** 2026-05-09
**Status:** Active
**Related:** VOY-1800 (Founding Philosophy), VOY-1803 (Visual & Voice), VOY-1804 (Naming Convention ADR)

---

## What Is It?

The bot roster spec for Iterwheel's GitHub automation pipeline.
The whole flow is designed as **a complete rocket launch mission** — every time
code travels from issue to release, it is like a rocket going from blueprint to
liftoff.

> **The organization itself is a precision rocket factory.**
> Each iteration builds a rocket; each release is a launch; the flywheel never stops.

---

## End-to-End Pipeline

```
Blueprint → Stack → Assembly → Static Fire → Clearance → Countdown → Liftoff
  Design    Classify  Implement   Ground test    Polling     T-minus     Launch
```

Each stage maps to a single bot — single responsibility, clean handoff.

---

## Current Roster

| # | Bot | GitHub Responsibility | Rocket-Stage Metaphor |
|---|-----|-----------------------|----------------------|
| 1 | **Blueprint** 📐 | Issue intake and title validation | Mission blueprint: every launch starts from a design |
| 2 | **Stack** 🛰️ | Issue classification labels | Vehicle stacking: classify and align each component |
| 3 | **Assembly** 🔧 | Code implementation | Vehicle assembly: build and integrate the work |
| 4 | **Static Fire** 🔥 | CI / test aggregation | Static fire test: prove engines run on the ground |
| 5 | **Clearance** ✅ | Review status aggregation | Go/No-Go poll: each station confirms readiness |
| 6 | **Countdown** ⏱️ | PR convention checks and merge gate | T-minus countdown: the final merge gatekeeper |
| 7 | **Liftoff** 🚀 | Release / Deploy | Launch: the moment we leave the ground |

---

## Future Roster

New bots must satisfy VOY-1800 §Design Principles #3: *"just find the matching rocket-stage word."*
Reserved expansion slots:

| Bot | Potential Responsibility | Rocket Stage |
|-----|-------------------------|--------------|
| **Manifest** | Auto-label / classification | Payload manifest |
| **Caliper** | PR size / complexity | Precision measurement |
| **Tanking** | Pre-merge check / conflict detection | Fuel loading |
| **Cargo** | Dependency / package management | Payload cargo |
| **Apogee** | Production monitoring | Apex of flight |
| **Telemetry** | Metrics / observability | Telemetry stream |

> Any new bot requires a fresh ADR: stating its space-mission stage,
> single responsibility, and boundary against existing bots.

---

## Design Notes

- **Single responsibility** — one bot does one thing; cross-stage logic must split into separate bots
- **Observable** — every bot's action must leave a clear GitHub-visible trace:
  labels, comments, reactions, checks, reviews, or releases as appropriate to
  that bot's responsibility
- **Retryable** — failures can be re-run; no external side effects until Liftoff
- **Narrative-consistent** — all bot names come from real aerospace terms; no invented words

---

## Change History

| Date       | Change                                                                                                                           | By               |
|------------|----------------------------------------------------------------------------------------------------------------------------------|------------------|
| 2026-05-09 | Initial version — extracted from `Iterwheel-Founding-Document.md` v1.1 (Bot Roster / End-to-End Pipeline / Future Bots sections) | Claude Code      |
| 2026-05-09 | Translated to English (project standard: English-only docs)                                                                      | Claude Code      |
| 2026-05-09 | Updated Blueprint, Stack, and Countdown responsibility boundaries to match the live GitHub App roster                            | Frank Xu + Codex |
| 2026-05-23 | Added Assembly bot between Stack and Static Fire; updated pipeline, roster, and Stack metaphor (issue #67) | DeepSeek (via VOY-1811) |
