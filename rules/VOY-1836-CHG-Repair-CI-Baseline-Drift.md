# CHG-1836: Repair CI Baseline Drift

**Applies to:** VOY project
**Last updated:** 2026-08-02
**Last reviewed:** 2026-08-02
**Status:** In Progress
**Date:** 2026-08-02
**Requested by:** Frank Xu
**Priority:** Medium
**Change Type:** Normal
**Scheduled:** 2026-08-02
**Related:** PR #286, COR-1101, COR-1503, COR-1600

---

## What

Restore a green pull-request CI baseline by removing two redundant names from
the Assembly package's `__all__` declaration and ensuring the security job
upgrades `setuptools` to the first non-vulnerable release before running
`pip-audit`. Normalize Ruff 0.16.1 formatting in the three existing CHG
documents reached after the lint error is cleared: `VOY-1817`, `VOY-1818`, and
`VOY-1820`.

## Why

PR #286 changes only `VOY-1812`, but CI run `30714818130` exposed two failures
already present on `origin/main`:

- The unpinned CI Ruff install now enables `RUF068` and rejects duplicate
  `AssemblyAuditManifest` and `PhaseMode` entries in
  `voyager/bots/assembly/__init__.py`.
- The GitHub Actions Python 3.11 environment contains `setuptools 79.0.1`,
  which `pip-audit` reports under `PYSEC-2026-3447`; the reported fixed version
  is `83.0.0`.
- Once the duplicate-export lint failure is removed, Ruff 0.16.1 reaches the
  format gate and reports outdated formatting in Python-like fenced examples
  in `VOY-1817`, `VOY-1818`, and `VOY-1820`.

Until the baseline is repaired, unrelated documentation PRs cannot obtain a
green CI result.

## Impact Analysis

- **Systems affected:** Assembly package export metadata, GitHub Actions CI,
  and formatting-only examples in three existing CHG documents.
- **Channels affected:** Pull-request and `main` CI runs.
- **Downtime required:** No.
- **Runtime impact:** None expected. Removing repeated `__all__` strings does
  not change the exported name set, and the dependency upgrade is scoped to
  the security-analysis job.
- **Rollback plan:** Revert the implementation commit. This restores the prior
  files but also restores the two known CI failures; no data migration or
  runtime rollback is required.

## Implementation Plan

1. Preserve the failing CI log excerpts for `RUF068` and
   `PYSEC-2026-3447` as the RED evidence.
2. Remove the second occurrence of `AssemblyAuditManifest` and `PhaseMode`
   from `voyager/bots/assembly/__init__.py`.
3. Apply Ruff 0.16.1 formatting only to `VOY-1817`, `VOY-1818`, and
   `VOY-1820`; inspect the diff to confirm that code-example meaning is
   unchanged. Because these are existing documents, update each document's
   `Last updated` date and append a formatting-only Change History entry per
   COR-1300.
4. In the security job, upgrade `setuptools>=83.0.0` alongside pip before
   installing project development dependencies.
5. Run current Ruff, formatting, type checking, tests, `pip-audit`, document
   validation, and diff checks.
6. Complete a COR-1600 direct review with one independent reviewer; approval
   requires an overall score of at least 9/10 and no blocking finding.
7. Publish an independent PR, wait for all GitHub Actions checks, and merge it.
   Then fetch `origin/main`, merge it into PR #286's documentation branch,
   push the updated branch to the fork, and wait for PR #286's rerun before
   merging it.

## Testing / Verification

- `uvx ruff@latest check .`
- `uv run ruff format --check .`
- `uv run mypy voyager`
- `uv run pytest -q`
- In a temporary Python 3.11 virtual environment matching CI, install `pip-audit` plus
  `setuptools==79.0.1`, run `pip-audit --local` to reproduce
  `PYSEC-2026-3447`, then run
  `python -m pip install --upgrade "setuptools>=83.0.0"` and repeat the audit;
  the setuptools finding must disappear.
- `af validate --root .`
- `git diff --check`
- All checks on the repair PR must pass before merge.

## Approval

- [x] Reviewed by: Frank Xu (explicit chat authorization, 2026-08-02)
- [x] Approved on: 2026-08-02
- [x] COR-1600 plan review: PASS 9.7/10, no blockers (independent reviewer,
  2026-08-02)
- [x] COR-1600 expanded-scope plan review: PASS 9.9/10, no blockers after
  Round 2 remediation (independent reviewer, 2026-08-02)
- [x] COR-1600 implementation review: PASS 9.9/10, no blockers (independent
  reviewer, 2026-08-02)

## Execution Log

| Date | Action | Result |
|------|--------|--------|
| 2026-08-02 | Diagnosed PR #286 CI run `30714818130`. | Confirmed both failures are independent of the documentation-only diff and already exist on `origin/main`. |
| 2026-08-02 | Ran `uvx ruff@latest check voyager/bots/assembly/__init__.py` with Ruff 0.16.1. | Reproduced both `RUF068` duplicate-export failures. |
| 2026-08-02 | Audited an isolated environment containing `setuptools 79.0.1`. | Reproduced `PYSEC-2026-3447`; audit reports `83.0.0` as the fixed version. |
| 2026-08-02 | Ran the full Ruff 0.16.1 lint and format checks after the first fixes. | Lint passed; format exposed three pre-existing CHG code-example formatting drifts, now added to the approved repair scope. |
| 2026-08-02 | COR-1600 Round 2 review scored 9.3/10 FIX. | Added the required COR-1300 metadata and Change History updates for all three formatted CHGs. |
| 2026-08-02 | COR-1600 Round 3 review scored 9.9/10 PASS. | Expanded implementation plan approved with no blockers. |
| 2026-08-02 | Implemented the approved repair. | Removed two duplicate exports, upgraded CI security-job setuptools, and normalized the three approved CHG code-example surfaces with COR-1300 metadata/history. |
| 2026-08-02 | Ran local verification. | Ruff 0.16.1 lint/format passed; Python 3.11 audit moved from one duplicated advisory ID on setuptools 79.0.1 to zero setuptools findings on 83.0.0; mypy passed; pytest passed (`2016 passed`); changed-files pre-commit passed; Alfred validation reported 145 documents and 0 issues. |
| 2026-08-02 | COR-1600 implementation review scored 9.9/10 PASS. | Independent review found no blockers; remote CI and merge remain. |

## Post-Change Review

- Local lint, audit, type checking, tests, document validation, and independent
  review confirm the implementation meets the approved plan.
- The newly exposed Ruff format drift required the planned scope expansion to
  three existing CHGs; all changes are formatting-only and traceable.
- GitHub Actions, merge, and the subsequent PR #286 refresh remain pending.

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-08-02 | Initial approved change plan for repairing the CI baseline. | Codex |
| 2026-08-02 | Expanded the repair scope to the three formatting-only CHG updates exposed after the lint gate and added COR-1300 compliance. | Codex |
