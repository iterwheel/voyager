# SOP-1810: Voyager Release Process

**Applies to:** Voyager (`iterwheel/voyager`) public releases
**Last updated:** 2026-05-17
**Last reviewed:** 2026-05-17
**Status:** Active
**Related:** `.github/workflows/release.yml` (the automation that backs this SOP), VOY-1801 (Mission Charter), CHANGELOG.md (the canonical release-notes record)

---

## What Is It?

The operator runbook for cutting a Voyager release. The mechanical work is
done by `.github/workflows/release.yml` — a `push: main` (+ manual
`workflow_dispatch`) workflow that:

1. Detects when `pyproject.toml` changes the `version =` line on `main`.
2. Verifies that `voyager/__init__.py` `__version__` is pinned to the same string.
3. Extracts the matching `## [<version>] — <date>` section from `CHANGELOG.md`.
4. Creates the `v<version>` tag and a GitHub release with that section as the body.

Operators only handle steps 1–3 of the *preparation* (PR-side). The
workflow handles tagging and publishing.

## Why

`ryosaeba1985` (the agent's `gh` write identity per WUK-2100) does not
have direct write access to `iterwheel/voyager`. `gh release create`
from the agent's shell returns HTTP 404 (GitHub's permission-cloaking
404). Routing the release through a workflow that runs as
`github-actions[bot]` with `contents: write` works around this without
granting the agent extra rights.

It also forces the discipline of every release having a `CHANGELOG.md`
entry — the workflow refuses to release if the section is missing.

---

## When to Use

- Cutting any tagged release of `iterwheel-voyager`.
- Re-running a release after a workflow failure (use `workflow_dispatch`).

## When NOT to Use

- Local sandbox testing — don't open release PRs to verify the
  workflow; iterate the workflow file under a feature branch and use
  `gh workflow run` against a test version once it's on `main`.
- Documentation-only changes — no version bump needed.

---

## Steps

### 1. Author the release PR

On a feature branch (e.g. `release/vX.Y.Z`):

```bash
git checkout -b release/vX.Y.Z
```

Edit:

- `pyproject.toml` — bump `version = "X.Y.Z"`
- `voyager/__init__.py` — bump `__version__ = "X.Y.Z"`
- `CHANGELOG.md` — prepend a new section:

  ```markdown
  ## [X.Y.Z] — YYYY-MM-DD

  ### Added — <feature>
  ...
  ```

Pin everything that downstream operators need to know in the section:
breaking changes, env var additions, migration steps, known
limitations (with linked issues), tooling updates. The workflow uses
this section verbatim as the release body.

### 2. Commit + push + open PR

```bash
git add pyproject.toml voyager/__init__.py CHANGELOG.md
git commit -m "release: vX.Y.Z — <one-line summary>"
git push fork release/vX.Y.Z
gh pr create --repo iterwheel/voyager --base main --head ryosaeba1985:release/vX.Y.Z \
  --title "release: vX.Y.Z — <one-line summary>" --body-file <(...)
```

Per WUK-2100, `gh` must be authenticated as `ryosaeba1985`. The
pre-push hook validates lint + format + tests locally before the push.

### 3. Wait for CI; the org owner merges

The release PR runs the same CI matrix as any other PR. The agent
cannot merge into `iterwheel/voyager`; the org owner does.

### 4. The workflow auto-releases on merge

Once `release/vX.Y.Z` merges into `main`, `.github/workflows/release.yml`
fires:

- If the merge commit changed `pyproject.toml`'s `version =` line, the
  workflow proceeds.
- Otherwise it skips (logged in the workflow run).

The workflow validates the version pins are in sync, refuses if the
tag or release already exists, extracts the CHANGELOG section, and
creates the tag + release.

Verify by visiting `https://github.com/iterwheel/voyager/releases` and
running `git fetch origin --tags`. The `v<version>` tag should point at
the merge commit.

### 5. Manual re-run (if step 4 didn't fire)

Use `workflow_dispatch`:

```bash
gh workflow run release.yml --repo iterwheel/voyager --ref main \
  -f version=X.Y.Z
```

Or via the GitHub UI: Actions → Release → Run workflow → enter version.

The workflow re-runs the same validation chain and creates the release.

---

## Pre-flight checklist

Before merging the release PR:

- [ ] `pyproject.toml` and `voyager/__init__.py` versions match.
- [ ] `CHANGELOG.md` has a `## [X.Y.Z] — YYYY-MM-DD` heading EXACTLY
      matching the version string (the workflow's `awk` is exact-match).
- [ ] The CHANGELOG section lists every operator-visible change: env
      vars, migrations, breaking changes, follow-up issues.
- [ ] CI is green on the release PR.

---

## Pitfalls

- **CHANGELOG heading drift.** The workflow extracts by `awk` regex
  `^## \\[X.Y.Z\\]`. A typo in the version string OR a different
  bracket style in the heading silently produces an empty release
  body. The workflow then exits non-zero — visible in the workflow log
  but the operator sees only a missing release.
- **Forgetting the `__init__.py` bump.** The workflow refuses to
  release if `voyager/__init__.py` `__version__` doesn't match
  `pyproject.toml`. Catches half-finished bumps.
- **Tagging mid-flight (stranded tag).** If the workflow creates the
  tag but then fails before creating the release (e.g. CHANGELOG
  section missing), re-runs are idempotent: the "Verify tag/release
  state" step detects that the stranded tag already points at the
  expected SHA and proceeds to create only the release. If the tag
  points at a *different* SHA, the workflow exits with an error
  requiring manual recovery (`git push origin :refs/tags/vX.Y.Z` then
  re-run). The SHA comparison works for both lightweight and annotated
  tags: the step queries the peeled ref (`refs/tags/X^{}`) first, which
  dereferences an annotated tag to its underlying commit SHA; it falls
  back to the direct ref for lightweight tags (which already point at
  the commit SHA).
- **Version input must match strict semver format.** When using
  `workflow_dispatch`, the `version` input is validated against
  `^[0-9]+\.[0-9]+\.[0-9]+([-+][A-Za-z0-9.-]+)?$` before any
  downstream step runs. Inputs like `v0.2.0` (leading `v`), `0.2`
  (missing patch), or `0.2.0_rc1` (underscore) will fail immediately.
  Use bare `X.Y.Z` or `X.Y.Z-rc.1` / `X.Y.Z+build.1` forms.
- **All merge strategies are supported.** The workflow uses
  `github.event.before` (the tip of `main` immediately before this
  push) to compare pyproject.toml versions — not `HEAD~1`. This is
  robust across squash-merge, merge-commit, and rebase-merge: in all
  three cases `github.event.before` is the previous main HEAD, not an
  intermediate rebased commit. The workflow fetches that SHA explicitly
  (`git fetch origin <before_sha> --depth=1`) to compare.
- **Pre-1.0 minor bumps may include breaking changes.** Document them
  loudly in the CHANGELOG section. The release process itself is
  agnostic; downstream operators rely on the CHANGELOG.

---

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-05-17 | Initial version — accompanies `.github/workflows/release.yml` for v0.2.0+ releases. | Claude Opus 4.7 |
| 2026-05-17 | Security/correctness hardening: replace HEAD~1 with github.event.before, shell-injection fix via env vars, strict version regex, idempotent tag-stranded recovery, awk literal index match, --latest=auto, commit-comment on failure. | Claude Sonnet 4.6 |
| 2026-05-17 | Fix annotated-tag SHA comparison: query peeled ref (refs/tags/X^{}) then fall back to direct ref; update §Pitfalls "Tagging mid-flight" note. Codex bot PR #33 review. | Claude Sonnet 4.6 |
