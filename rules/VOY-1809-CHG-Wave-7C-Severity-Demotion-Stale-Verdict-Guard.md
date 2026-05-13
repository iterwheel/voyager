# CHG-1809: Wave 7C — Severity Demotion + Stale-Verdict Guard

**Applies to:** VOY project (voyager clearance bot)
**Last updated:** 2026-05-13
**Last reviewed:** 2026-05-13
**Status:** Approved
**Date:** 2026-05-13
**Scheduled:** ASAP (pending CHG approval; implementation begins commit-by-commit on the same branch)
**Requested by:** Frank Xu (via session 2026-05-13)
**Priority:** Medium
**Change Type:** Normal
**Related:** VOY-1806 (App Permission Matrix — F7 dependency), SWM-1101 (clearance pipeline source), SWM-1102 (severity demotion source §B row 1), SWM-1103 (guarded.py source — partial re-architecture for webhook context), CLD-1801 (trinity review cadence), COR-1602 (multi-reviewer loop)
**Targets:** `voyager/bots/clearance/pipeline.py`, `voyager/bots/clearance/severity.py` (NEW), `voyager/bots/clearance/severity_input.py` (NEW), `voyager/core/writeback.py`, `voyager/core/github_app.py`, `tests/clearance/**`

---

## What

Wave 7C ports two SWM concerns the deterministic pipeline currently lacks:

1. **7C-1 Severity demotion** (port SWM-1102 §B row 1) — extract severity + finding-kind from Codex review body; demote one step when `finding_kind == "required_check_coupling"` AND base branch lacks branch protection; surface `effective_severity` into PR-level status aggregation so low-priority OPEN threads stop blocking PRs.
2. **7C-2 Stale-verdict guard** — webhook-driven equivalent of SWM-1103's `VerdictCheck`. Before any GitHub mutation in `dispatch_route_writeback`, refetch current PR head_sha via REST and skip if it differs from the head at which the verdict was computed.

### Out of scope

- Polling cycle (voyager pipeline.py docstring locks "Trigger: webhook-only")
- Dashboard / CLI (separate wave)
- Full SWM-1103 `guarded.py` CLI port (CLI-bound; not applicable to webhook bot)
- Hardening #3 + #7 from 7B-3 (min_confidence floor + canary threshold; need empirical telemetry first)
- Multi-row SWM-1102 §B / §C demotion rules (add lazily when real PRs exercise them)
- Checkbox gate (deferred to 7D per Gemini round-1 P2)

---

## Why

The pipeline currently hardcodes `Severity.P2` at `pipeline.py:226+248` and the `effective_severity` model field is never populated. Every OPEN review thread blocks PR merging regardless of how minor the finding is, which doesn't match SWM-1101's intent. Without severity-aware status aggregation, voyager produces unnecessary PR blocks for nitpicks, eroding operator trust in the automation.

Separately, GitHub webhook delivery is at-least-once and unordered. With LLM investigator latency from 7B-3 (~2-5s per thread), concurrent `push` or `synchronize` deliveries can result in a verdict computed against the *prior* head_sha being applied as a writeback after the PR head has advanced. The stale-verdict guard closes this race.

---

## Architectural decision: β locked

The user-locked design has severity influence PR-level mergeability ("β" in the plan history), not just metadata:

- **α (rejected)** — `effective_severity` populated but `_compute_status` unchanged; all OPEN threads still block regardless of severity. Codex r1 called this "cosmetic."
- **β (locked)** — `_compute_status` filters OPEN threads by `effective_severity`. P3-only OPEN threads → `Status.READY` with reason `"N low-priority thread(s) still open"`. P1/P2 OPEN threads continue to block.

User rationale: "我希望让 AI 来帮我先做判断" — let voyager auto-decide that nitpick-tier OPEN threads don't gate merges.

---

## Decisions (D1-D6)

| ID | Question | Lock | Rationale |
|---|---|---|---|
| D1 | `finding_kind` source | **A — parse Codex review body** | Gemini r1 caught: option C (investigator output) chronologically impossible (investigator runs after judge but severity needed before). Matches SWM-1102 approach. |
| D2 | Where in pipeline | **B — new phase between classify + judge** | All 3 r1 reviewers agree; pipeline.py has clean phase boundaries here. |
| D3 | `branch_protected` source | **C — lazy REST** (`GET /repos/.../branches/{base}`); fail-safe to `True` (don't demote on uncertainty) | GLM + Gemini r1: protection rules can change without push events → cache (B) goes stale. |
| D4 | Current head_sha for stale guard | **B — fresh REST** before mutations | 3-way P1: webhook payload's `head.sha` always equals `automation.head_sha` (same delivery), so option A misses the exact race. |
| D5 | Stale-handling style | **B — `automation.status="stale_verdict_skip"` + structured log** with `expected_sha` / `actual_sha` | DeepSeek + Gemini: silent skip destroys observability. |
| D6 | Checkbox gate (7C-3) | **Skip — defer to 7D** | Gemini r1: dilutes focus. |

---

## Implementation Plan

### Surface map

### 7C-1 Severity Demotion

| File | Action |
|---|---|
| `voyager/bots/clearance/severity.py` | NEW — pure-function evaluator. Ports `swm/severity.py` with `base_branch` parameter (no hardcoded "main"). |
| `tests/clearance/test_severity.py` | NEW — 14 unit-test scenarios (demote × pass-through × invariants × edge cases). |
| `voyager/bots/clearance/severity_input.py` | NEW — extracts BOTH `codex_severity` AND `finding_kind` from Codex review body. Emits `severity_extraction_failure` counter on `None` returns. |
| `voyager/core/github_app.py` | EDIT — add `branch_protected(repo, branch) -> bool` REST helper. **Verify VOY-1806 covers required permission before merging commit 3.** |
| `voyager/bots/clearance/pipeline.py` | EDIT — invoke `severity.evaluate` as new phase between classify + judge in `_process_thread`. Populate BOTH `pipeline.py:226` (Thread) AND `pipeline.py:248` (ThreadSnapshot) with real values (currently hardcoded `Severity.P2`). Emit `severity_demoted` structured log when `effective < codex`. |
| `voyager/bots/clearance/pipeline.py::_compute_status` | EDIT — **β change**: filter OPEN threads by `effective_severity`. Update precedence rules so P3-only OPEN threads return `Status.READY`. |

### 7C-2 Stale-Verdict Guard

| File | Action |
|---|---|
| `voyager/bots/clearance/pipeline.py::compute_clearance_automation` | EDIT — emit `head_sha` key in returned automation dict (schema-additive). |
| `voyager/core/writeback.py::dispatch_route_writeback` | EDIT — before mutations, fresh REST `GET /repos/.../pulls/{n}` → `.head.sha`. Compare to `automation.get("head_sha")` (`.get` not subscript, to tolerate legacy in-flight payloads from before this commit deploys). On mismatch: set `automation.status="stale_verdict_skip"`, log with `expected_sha`/`actual_sha`, skip writeback. |

---

### β aggregation rules (`_compute_status` revised)

Precedence in `_compute_status` becomes:

1. No threads → `Status.READY`
2. Any OPEN with `effective_severity ∈ {P1, P2}` → `Status.BLOCKED`, reason `"N high-priority thread(s) still OPEN"`
3. Any `NEEDS_HUMAN_JUDGMENT` → `Status.PENDING`
4. Only OPEN P3 threads remaining (others RESOLVED) → `Status.READY`, reason `"all blocking threads RESOLVED; N low-priority thread(s) still open"`
5. All RESOLVED → `Status.READY`, reason `"all Codex review threads RESOLVED"`

---

### Commit sequence (6 commits, MVE cut at commit 4)

| # | Commit (impl + test split) | LOC est |
|---|---|---|
| 1 | `feat(clearance): severity demotion evaluator` | +62 / +167 tests |
| 2 | `feat(clearance): codex severity extractor (body parsing)` | +70 / +110 tests |
| 3 | `feat(clearance): wire severity into pipeline + populate Thread/ThreadSnapshot` (α) | +60 / +90 tests |
| 4 | `feat(clearance): status aggregation by effective_severity (β)` ← **MVE** | +30 / +70 tests |
| 5 | `feat(clearance): emit head_sha in automation dict (schema)` | +10 / +20 tests |
| 6 | `feat(clearance): stale-verdict guard in writeback` | +50 / +90 tests |

**Trinity gate cadence**: MVE round after commit 4. Final round after commit 6. ≥9.0 from each reviewer per gate. Max 3 iterations per gate per CLD-1801 / COR-1602. Reviewer set: GLM + Codex + DeepSeek + Gemini (4 reviewers); reviewers that hang or fail to return within 15min are dropped from the round and noted in the Execution Log, but the gate still requires ≥9.0 from all reviewers that did return.

### Error-handling spec

| Path | Behavior | Rationale |
|---|---|---|
| `severity_input.extract()` exception | Return `None` + log warning + increment `severity_extraction_failure` counter | DeepSeek r2 P2: prevent silent breakage if Codex changes output format. Alert threshold: >20% of recent calls. |
| `severity.evaluate()` exception | Let propagate (pure function — only fails on type errors = programmer bug) | Pure function; runtime errors indicate bug, not data. |
| `branch_protected()` REST 404/5xx/timeout | Return `True` (don't demote on uncertainty) + log warning | Fail-safe: preserve full rigor when protection status unknown. |
| Stale-guard REST error fetching current head | Emit `stale_guard_failed_fail_open=true` structured log + increment `stale_guard_fail_open` counter, then proceed with writeback (fail-open) | One transient API hiccup shouldn't block all writebacks, but degraded operation must be observable so persistent API instability surfaces. Codex r4 P2. |
| Legacy `automation` dict without `head_sha` (commit 5 not yet deployed) | `.get("head_sha")` returns None → skip guard, proceed | Gemini r2 P3: rolling deploy safety. |

---

## Convergent findings folded in (rounds 1+2 reviewers)

| # | Finding | Source | Lands in |
|---|---|---|---|
| F1 | `severity_extraction_failure` counter + alert threshold | DeepSeek r2 P2 | Commit 2 |
| F2 | Extractor returns BOTH `codex_severity` AND `finding_kind` | GLM r2 P2-1 | Commit 2 |
| F3 | Patch BOTH `pipeline.py:226` (Thread) AND `:248` (ThreadSnapshot) | GLM r2 P2-2 | Commit 3 |
| F4 | `severity_demoted` structured log on demotion | DeepSeek + Gemini r1 P2 | Commit 3 |
| F5 | `stale_verdict_skip` log includes `expected_sha`/`actual_sha` | Gemini r1 P3-2 | Commit 6 |
| F6 | Inline comment: `stale_verdict_skip` is un-enumerated vs `Status` enum; doc that downstream consumers must tolerate unknown `automation.status` values | GLM r2 P3-1 + Codex r4 P3 | Commit 6 |
| F7 | Verify GitHub App permissions for `branch_protected` | Gemini r2 P2 | Commit 3 prerequisite (check VOY-1806) |
| F8 | Commit 6 uses `automation.get("head_sha")` not subscript | Gemini r2 P3 | Commit 6 |
| F9 | `branch_protected` 404/5xx/timeout paths in commit 3 test plan | DeepSeek r2 P3 | Commit 3 tests |
| F10 | `base_branch` parameter (no hardcoded "main") | Codex r1 P1-D3 | Commit 1 (✅ already applied) |

---

## Impact Analysis

### Systems affected

- **`voyager/bots/clearance/pipeline.py`** — `_process_thread` gains severity-extraction + demotion phase between classify and judge; `_compute_status` aggregation gains severity-aware OPEN-thread filtering (β); `compute_clearance_automation` returned dict gains `head_sha` key (schema-additive).
- **`voyager/core/writeback.py`** — `dispatch_route_writeback` gains pre-mutation fresh-head REST fetch + skip-on-mismatch path.
- **`voyager/core/github_app.py`** — gains `branch_protected(repo, branch) -> bool` REST helper.

### Channels affected

- **GitHub PR status checks** — PRs with only P3-tier OPEN review threads will transition from `BLOCKED` to `READY`. This is the intended new behavior under β.
- **JSONL state ledger** (`Thread.effective_severity`, `Thread.demotion_reason`, `ThreadSnapshot.*`) — fields previously written as hardcoded `Severity.P2` will now reflect real values. Downstream readers must tolerate the new value range.
- **Structured logs** — new events `severity_demoted` (commit 3) and `stale_verdict_skip` (commit 6) emitted at INFO level.
- **Automation dict consumers** (`enrichment.py`, `overlay.py`) — gain optional `head_sha` key (must use `.get()` per F8); β changes the `status` value distribution to allow `READY` with low-priority OPEN threads.

### Downtime required

None. Rolling deploy compatible:

- Commits 1-4 are pure-source additions / pipeline edits — no state migration.
- Commit 5 (schema-additive `head_sha`) deploys before commit 6 (consumer) so legacy payloads in flight pass through commit 6's `.get()` lookup as `None` and fail-open.
- No external system state mutations.

### Rollback plan

Revert all 6 implementation commits + this CHG file in one PR. No external state cleanup needed. JSONL state ledger entries written with the new `effective_severity` values remain valid historical data after rollback; future entries revert to hardcoded `Severity.P2`.

### External dependencies

- **GitHub App permission**: `branch_protected()` requires `metadata: read` (which the voyager App already has per `VOY-1806`). REST endpoint `GET /repos/{owner}/{repo}/branches/{branch}` returns `.protected: bool`. **F7 prerequisite — verify before merging commit 3.**
- **Codex review-body format stability** — F1 mitigation (extraction-failure counter + >20% alert threshold) catches drift.

---

## Testing / Verification

### Per-commit gates

Each impl+test pair (6 pairs total) must pass:
- `uv run pytest tests/ -q` — all existing + new tests pass
- `uv run ruff check .` clean
- `uv run ruff format --check .` clean
- `uv run mypy voyager` clean

### Scenario coverage matrix

| Commit | Coverage focus | Expected new tests |
|---|---|---|
| 1 | severity evaluator (pure function) | 14 (demote × pass-through × invariants × edges) — ✅ done |
| 2 | extractor: body parsing, fail-closed on `None`, extraction-failure counter | ~12 |
| 3 | pipeline integration: classify → severity → judge ordering; Thread + ThreadSnapshot populated; `severity_demoted` log emitted on demotion; `branch_protected` 404/5xx/timeout paths | ~10 |
| 4 | β: `_compute_status` precedence — P1/P2 OPEN → BLOCKED; P3-only OPEN → READY; mixed cases | ~8 |
| 5 | `automation.head_sha` emission; legacy payload absence tolerance | ~3 |
| 6 | stale-guard: match → proceed; mismatch → skip + log + `automation.status="stale_verdict_skip"`; REST error → fail-open proceed | ~10 |

### Trinity gate cadence

- **MVE round** after commit 4. Reviewer set: GLM + Codex + DeepSeek + Gemini (4 reviewers). Target ≥9.0 from each. Reviewers that hang or fail to return within 15min are dropped from the round and noted in the Execution Log, but the gate still requires ≥9.0 from all reviewers that did return. Max 3 iterations per CLD-1801.
- **Final round** after commit 6. Same reviewer set and gating rule.

### Acceptance criteria

- All 6 commits land with trinity gates cleared.
- **Commit 3 merge gate (explicit, F7)**: voyager GitHub App's installed permissions verified to include `metadata: read` for the `branch_protected()` REST call. Verification recorded in the Execution Log row for commit 3 before that commit is allowed to merge. Codex r4 P2.
- No unresolved P1/P2 findings from the final CHG trinity round.
- Total test count strictly increases (no test regressions). Baseline is 410 (pre-7C) + 14 (commit 1) and grows commit-by-commit; an approximate target of +43 across commits 2-6 is a planning estimate, not a hard gate.
- `VOY-0000` index regenerated via `af index` after CHG merges.

---

## Approval

- [x] Final CHG trinity review pass: ≥9.0 from all 4 reviewers (Codex 9.4 / GLM 9.3 / DeepSeek 9.15 / Gemini 10.0 — round 5, 2026-05-13)
- [x] Approved by: Frank Xu on 2026-05-14

---

## Execution Log

| Date | Commit | Action | Result | By |
|------|--------|--------|--------|----|
| 2026-05-13 | `37d218a` | CHG proposed (this document) | Status: Proposed | Claude Opus 4.7 |
| 2026-05-14 | `025206e` | CHG approved (trinity r5 ≥9.0 from all 4 + user approval) | Status: Approved | Frank Xu / Claude |
| 2026-05-14 | `544b0e8` + `971e62f` | Commit 1 — severity evaluator (impl + 14 tests) | 424 tests pass | Claude Haiku + Sonnet |
| 2026-05-14 | `19103d7` + `8f596d7` | Commit 2 — extractor (impl + 16 tests) | 440 tests pass | Claude Haiku + Sonnet |
| 2026-05-14 | `11d9a45` + `54c5b9e` | Commit 3 — pipeline wiring α (impl + 10 BDD). F7 permission verified: VOY-1806 line 87 grants `iterwheel-clearance: Metadata: Read`, which is the only scope required by `GET /repos/.../branches/{branch}` to read `.protected`. Administration permission NOT required. | 450 tests pass; F7 cleared | Claude Haiku + Sonnet |
| 2026-05-14 | `2efd57a` + `468c32c` | Commit 4 — β aggregation MVE (impl + 11 unit + 5 fixture realism updates) | 461 tests pass; MVE gate enters | Claude Haiku + Sonnet |
| 2026-05-14 | (this round) | MVE trinity round | PROCEED ≥9.0 from all 4 (Gemini 9.8, DeepSeek 9.1, GLM 9.0, Codex 9.0; average 9.225); 3-way convergent P2s (grammar `need→needs`, S5 title rename, `branch_protected` per-thread→per-webhook memoization) folded into subsequent polish | Claude Opus 4.7 |
| 2026-05-14 | `426173d` + `974220d` | Commit 5 — head_sha in automation (impl + 3 BDD) | 464 tests pass | Claude Haiku + Sonnet |
| | (pending) | Commit 6 — stale-verdict guard | | |
| | (pending) | Final trinity round | | |
| | (pending) | Push + PR + merge | | |

---

## Post-Change Review

To be completed after Wave 7C ships:

- **What worked**: _(pending)_
- **What didn't**: _(pending)_
- **Lessons / SOP gaps surfaced**: _(pending)_
- **Follow-up CHGs filed**: _(track 7C-1b for further β tuning if β verdict thresholds need adjustment; track multi-row §B/§C demotion rules when real PRs exercise them; track 7D for checkbox gate)_
- **Telemetry**: _(severity_extraction_failure rate, severity_demoted frequency, stale_verdict_skip incidence over first 2 weeks post-merge)_

---

## Deferrals to follow-up CHGs

- **Multi-row SWM-1102 §B / §C demotion rules** — add lazily when real PRs exercise them (matches SWM's own approach). Track as `VOY-<next>` when first encountered.
- **7C-3 Checkbox gate** — deferred to Wave 7D per Gemini round-1 P2. Will be filed as `VOY-<next>-CHG-Wave-7D-Checkbox-Gate`.
- **7B-3 hardening #3 + #7** — min_confidence floor under thinking=False + canary threshold on "addresses" verdict. Need empirical telemetry baseline first; file CHG after 2+ weeks of investigator-call structured logs.
- **7B-3 P3 nits** — caplog assertion for `investigator_call` structured log; unit test for `_sanitize_markdown` backtick collapse. Low priority.

---

## Why β was split into its own commit (commit 4)

Commit 3 (α work — populate `Thread.effective_severity` + `demotion_reason`) is **observably zero-impact**: the pipeline behaves identically since `_compute_status` doesn't read these fields. Commit 4 (β work — `_compute_status` reads `effective_severity`) is where user-visible behavior changes. Splitting them:

- Makes commit 3 reviewable as a pure "populate fields we already model" change — no behavior risk.
- Makes commit 4 the single locus of behavior change, easy to revert if β tuning needs adjustment.
- Aligns with the rolling-deploy story (commit 3 can deploy alone; commit 4 carries the observable change).
- Trinity MVE gate fires after commit 4 specifically because that's where behavior risk concentrates.

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-05-13 | Initial proposal — Wave 7C scope, decisions D1-D6, β architecture lock, 6-commit sequence | Claude Opus 4.7 |
| 2026-05-13 | Round-3 format fixes: added `Scheduled` + `Related` frontmatter; restructured Impact section into 5 COR-1101 subheadings; added `Testing / Verification`, `Execution Log`, `Post-Change Review`, `Deferrals` standalone sections; restored β-split rationale paragraph (per GLM r3 P1+P2+P3 + Gemini r3 P1) | Claude Opus 4.7 |
| 2026-05-13 | Round-4 polish: harmonized reviewer-set to 4 reviewers (GLM+Codex+DeepSeek+Gemini) across both cadence sections (DeepSeek r4 P2); added `stale_guard_fail_open` observability counter + structured log (Codex r4 P2); promoted F7 permission check to explicit commit-3 merge gate (Codex r4 P2); softened test-count acceptance criterion + relabeled Approval section to "Final CHG trinity round" (Codex r4 P3) | Claude Opus 4.7 |
| 2026-05-14 | Round-5 trinity verification: all 4 reviewers ≥9.0 (Codex 9.4, GLM 9.3, DeepSeek 9.15, Gemini 10.0, average 9.46). Zero remaining findings. CHG approved by Frank Xu; Status moved to Approved. Implementation begins commit-by-commit. | Claude Opus 4.7 |
