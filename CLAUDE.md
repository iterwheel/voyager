# CLAUDE.md — Voyager 🛰️

Guidance for AI agents working in this repository. `AGENTS.md` is a symlink to
this file, so keep instructions portable across fresh checkouts and non-Claude
runtimes.

> *"We don't ship code. We launch rockets."*

---

## Documentation Language

**All project documents are written in English.** This includes everything under
`rules/`, this `CLAUDE.md`, and any future ADRs/REFs/SOPs/CHGs.

When updating docs, write in English. When new content arrives in another language,
translate before committing.

---

## Project Overview

**Organization**: Iterwheel 🌀 (Iteration + Flywheel)
**This repo**: Voyager — the first long-range rocket built by the Iterwheel factory.

The full constitutional context lives under `rules/`. Read these before any
non-trivial decision about naming, architecture, or new components:

- `VOY-1800` — **Founding Philosophy**: Iterwheel meaning, core values, design principles
- `VOY-1801` — **Voyager Mission Charter**: why this repo is named Voyager + sister-mission naming space
- `VOY-1802` — **Bot Roster (Rocket Factory)**: Blueprint → Stack → Static Fire → Clearance → Countdown → Liftoff
- `VOY-1803` — **Visual & Voice Identity**: emoji avatars + aerospace voice + message templates
- `VOY-1804` — **ADR: Naming Convention**: why Iterwheel + Voyager + aerospace bots (and what was rejected)

The five files in `rules/` are the canonical source of truth for the project's
philosophy, mission, and bot/identity specs.

---

## Alfred Workflow (af)

This project uses the `af` CLI (Alfred — Agent Runbook). Project-specific docs
live in `rules/` with prefix **VOY**. Claude Code may also load personal rules
from `~/.claude/CLAUDE.md`, but this repository file must stand on its own for
Codex and other agents.

### Session start

1. Run `COR-1208` (sanity check: `pwd`, `git status --short --branch`, `git log -5`,
   smoke test, load tracker per `COR-1201`) — stop on anomalies until acknowledged
2. Run `af guide --root .` from the repository root for routing
3. For every task: identify SOPs → `af plan <SOP_IDs>` → declare active SOP per `COR-1402`

### Common commands

```bash
af list --root .                       # All PKG + USR + PRJ docs
af read VOY-1800                       # Read Founding Philosophy
af search <pattern> --root .           # Search across docs
af status --root .                     # Doc counts
af validate --root .                   # Structural validation
af index --root .                      # Regenerate VOY-0000 index
```

### Creating new VOY docs

```bash
af create ref --prefix VOY --area 18 --title "..." --root .
af create adr --prefix VOY --area 18 --title "..." --root .
af create sop --prefix VOY --area 18 --title "..." --root .
```

Area code conventions for this project (will evolve):

- `18xx` — Foundation: philosophy, mission charter, identity, naming ADRs
- (other areas TBD as the project grows — claim them here when used)

---

## Naming & Design Constraints

Any new bot, sub-project, or major component **must** satisfy the five principles
in VOY-1800 §Design Principles:

1. **Narrative consistency** — fits the rocket-launch story
2. **Clear responsibility** — single responsibility
3. **Extensibility** — new component finds its rocket-stage word
4. **Sense of ceremony** — workflow as ceremony, not just process
5. **Compound spirit** — every Liftoff is the start of the next iteration

> Anything that breaks the rocket narrative requires a new ADR (per VOY-1804).
> Don't break the story silently.

---

## GitHub Identity

Per WUK-2100 user-level GitHub identity routing, loaded by `af guide`:

- Public GitHub writes (PRs, issues, comments, reviews) must use `gh` CLI as `ryosaeba1985`
- Verify with `gh auth status` before any public mutation
- If the wrong account creates an artifact, close/replace immediately and report both

---

## Voice (when commenting on PRs / issues / commits as a bot)

Use aerospace terminology per VOY-1803. Quick reference:

- **Pass**: *"All engines nominal."* / *"All stations report GO."* / *"v1.2.0 has cleared the tower."*
- **Fail**: *"Hold, hold, hold."* / *"Stack misalignment detected."* / *"NO-GO from Reviewer Station."*

Self-check: would a real NASA / SpaceX flight director say this on a live mission?
If no, rewrite.

---

**Godspeed, Voyager.** 🚀
