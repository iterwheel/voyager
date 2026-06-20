# SOP-1824: Assembly Failure Diagnostics

**Applies to:** Voyager Assembly bot operators and managed repositories
**Last updated:** 2026-06-20
**Last reviewed:** 2026-06-20
**Status:** Active
**Date:** 2026-05-25
**Requested by:** Frank Xu (via issue #93)
**Priority:** P1
**Related:** VOY-1817, VOY-1821, VOY-1822, VOY-1823, VOY-1825, #93

---

## What Is It?

This SOP defines how Assembly records and investigates failures from the
subprocess-backed OMP adapter. It complements VOY-1823 audit lookup: VOY-1823
finds the private audit manifest; this SOP explains the failure diagnostic and
retained debug bundle stored by failed real-backend runs.

When a failure investigation turns into a repeated fix loop or retry/stop
decision, use VOY-1825 as the policy reference for convergence boundaries.

On failure, Assembly records:

- `phase`: where the backend failed, such as `clone`, `git_config`,
  `branch_start_fetch`, `checkout`, `omp_execution`, `git_status`, `git_add`,
  `git_commit`, `verification`, or `git_push`
- `command_category`: `git`, `omp`, `verification`, or `subprocess`
- `command`: bounded command label without credential-bearing argv
- `exit_code`
- `timed_out`
- bounded `stdout_tail` and `stderr_tail`
- `failure_debug_bundle_path` when a checkout was retained locally

The default failure bundle path convention is:

`~/.voyager/state/assembly/failures/<owner>/<repo>/<issue>/<run-id>/`

`<run-id>` is the Assembly audit ID when available.

## Why

Assembly failures must be debuggable without re-running destructive or expensive
work and without publishing secrets to GitHub. A failed push, verification
command, checkout, or OMP subprocess can otherwise disappear when the temporary
checkout is cleaned up.

## When to Use

Use this SOP when:

- an Assembly progress comment shows a backend failure diagnostics panel
- an Assembly audit manifest contains `failure_diagnostic`
- an Assembly run retained `failure_debug_bundle_path`
- a real OMP backend run failed before opening or updating a PR
- an operator needs to decide whether retrying Assembly is justified

## When NOT to Use

Do not use this SOP to:

- debug dry-run or fake-subprocess adapter behavior unless it produced the same
  failure fields
- publish retained checkout contents, transcripts, tokens, or API keys to
  GitHub
- bypass VOY-1822 review, Clearance, approval, or merge gates
- approve, merge, or resolve review threads for Assembly

## Public Boundary

GitHub comments may show:

- phase
- command category
- exit code
- timeout flag
- a short sanitized stdout or stderr tail
- a statement that a private debug bundle was recorded
- the VOY-1823 audit lookup hint

GitHub comments must not show:

- GitHub installation tokens
- `ASSEMBLY_GITHUB_TOKEN` values or assignments
- API key values such as `sk-...`
- credential-bearing URLs
- full local transcripts
- full retained checkout paths

## Private Records

The audit manifest may include:

- `failure_diagnostic`
- `failure_debug_bundle_path`
- normal VOY-1823 fields such as branch, PR, commit, session, and transcript
  metadata

The retained failure bundle contains:

- `repo/`: the failed checkout state
- `assembly-failure.json`: sanitized metadata for the failure

Operators must treat both the manifest and bundle as private local records.

## Steps

1. Open the Assembly progress comment on the issue or PR.
2. Read the backend failure panel and identify `phase`, `command_category`, and
   `exit_code`.
3. Use the audit ID and VOY-1823 lookup path to open the private manifest.
4. Confirm `failure_diagnostic` matches the public failure panel.
5. Open `failure_debug_bundle_path` locally when present.
6. Inspect `assembly-failure.json` first, then inspect the retained `repo/`
   checkout only as needed.
7. If the failure was `git_push`, verify the GitHub App installation token path
   and repository allow-list before retrying Assembly.
8. If the failure was `verification`, run the listed verification command from
   the retained checkout before deciding whether to retry or patch Voyager.
9. If the failure was `omp_execution`, inspect the OMP transcript path from the
   audit manifest if one exists.

## Examples

Public progress comment excerpt:

```text
Backend failure diagnostics:
- Phase: git_push
- Command: git
- Exit code: 128
- Debug bundle: recorded in the private audit manifest.
```

Private manifest fields:

```json
{
  "failure_diagnostic": {
    "phase": "git_push",
    "command_category": "git",
    "command": "git push",
    "exit_code": 128,
    "timed_out": false
  },
  "failure_debug_bundle_path": "~/.voyager/state/assembly/failures/owner/repo/93/asmb-0123456789abcdef"
}
```

## Retry Rules

- Retry Assembly only after the failure phase has a plausible external fix or a
  code fix has shipped.
- Do not retry solely to recover evidence; use the retained bundle and audit
  manifest first.
- Do not paste private bundle contents or transcript excerpts into GitHub unless
  they have been manually reviewed and redacted.
- Keep happy-path cleanup unchanged: successful runs and no-change runs should
  not leave `assembly-omp-*` temporary checkouts behind.

## Troubleshooting

If the public comment has no backend failure panel:

- Confirm the run used the real `pi-oh-my-pi-deepseek` backend.
- Confirm the run used a Voyager version that includes #93.
- Check bridge logs for `Assembly backend failure diagnostic`.
- Use VOY-1823 audit lookup and inspect `adapter_status` and
  `adapter_summary`.

If `failure_debug_bundle_path` is missing:

- The failure may have happened before a temporary checkout existed.
- The temp checkout may already have been removed by an older Voyager version.
- Check bridge logs for local filesystem errors during failure retention.

If the retained bundle exists but `repo/` is missing:

- Treat the bundle as incomplete.
- Use `assembly-failure.json`, the audit manifest, branch state, and bridge
  logs as the remaining evidence.

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-06-20 | Added an in-body VOY-1825 reference for repeated failure-loop stop decisions. | Codex |
| 2026-06-20 | Added VOY-1825 as the loop-convergence policy reference for failure-loop stop decisions. | Codex |
| 2026-05-25 | Added Assembly subprocess backend failure diagnostics and retention SOP | Codex |
