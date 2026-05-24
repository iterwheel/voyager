# SOP-1823: Assembly OMP Audit Lookup

**Applies to:** Voyager Assembly bot operators and managed repositories
**Last updated:** 2026-05-24
**Last reviewed:** 2026-05-24
**Status:** Active
**Date:** 2026-05-24
**Requested by:** Frank Xu (via issue #92)
**Priority:** P2
**Related:** VOY-1817, VOY-1821, VOY-1822, #92, #93

---

## What Is It?

This SOP defines the private audit lookup procedure for Assembly OMP runs.
GitHub progress comments expose a stable public audit ID such as
`asmb-0123456789abcdef`. Wukong stores the private manifest that maps that ID
to local execution records, including the OMP transcript JSONL path and the
Assembly checkout path.

The default manifest path convention is:

`~/.voyager/state/assembly/audit/<owner>/<repo>/<issue>/<audit-id>.json`

Operators may override the root with `ASSEMBLY_AUDIT_DIR`. GitHub comments
must not publish local transcript contents, credential-bearing environment
values, or GitHub installation tokens.

## Why

Assembly can produce useful private evidence while implementing an issue:

- the local checkout used by the OMP backend
- the OMP session JSONL under `~/.omp/agent/sessions/`
- verification command outcomes and commit SHAs
- the GitHub issue and PR linked to the run

A public audit ID lets future agents connect a GitHub-visible Assembly comment
to the private local record without exposing sensitive local data on GitHub.

## When to Use

Use this SOP when:

- A GitHub Assembly progress comment shows an audit ID.
- An operator needs to inspect a past OMP run from Wukong.
- A failed Assembly run needs private transcript review before retry.
- A reviewer needs to confirm which local checkout or transcript corresponds
  to a PR.

## When NOT to Use

Do not use this SOP to:

- Publish OMP transcript contents to GitHub.
- Recover or inspect credentials, API keys, installation tokens, private keys,
  or credential-bearing environment values.
- Approve, merge, or resolve review threads for Assembly.
- Treat the manifest as an authorization record. Authorization still follows
  VOY-1805, VOY-1818, and repository branch protection.

## Preconditions

Before lookup:

- You are on Wukong or another operator machine that owns the Assembly state
  directory.
- You have the GitHub-visible audit ID from an Assembly progress comment.
- You know the target repository and issue number from the same comment.
- Local filesystem access to `~/.voyager/state/assembly/audit` is available.

## Steps

1. Copy the audit ID, repository, and issue number from the GitHub Assembly
   progress comment.
2. Check the deterministic manifest path:

   ```sh
   ls -l ~/.voyager/state/assembly/audit/<owner>/<repo>/<issue>/<audit-id>.json
   ```

3. If the exact path is not known, search by audit ID:

   ```sh
   find ~/.voyager/state/assembly/audit -name '<audit-id>.json' -print
   ```

4. Inspect the manifest without editing it:

   ```sh
   python -m json.tool ~/.voyager/state/assembly/audit/<owner>/<repo>/<issue>/<audit-id>.json
   ```

5. Confirm the manifest fields:

   - `repository`
   - `issue_number`
   - `pr_number`
   - `branch_name`
   - `delivery_id`
   - `backend_name`
   - `checkout_dir`
   - `omp_session_jsonl_path`
   - `exported_html_path`
   - `verification_commands`
   - `adapter_status`
   - `commit_shas`
   - `session_mode`
   - `resume_requested`
   - `resume_fallback_reason`
   - `session_id`
   - `expected_head_sha`
   - `created_at`
   - `completed_at`

6. If `omp_session_jsonl_path` is present, inspect the JSONL transcript
   locally. Keep it private.
7. If `exported_html_path` is present, open the HTML file locally. Keep it
   private.
8. If an HTML export is needed and no export path exists, create a local
   read-only export from the JSONL transcript and store it beside the manifest
   or in another private operator directory. Record the path in local notes;
   do not paste transcript contents into GitHub.

## Examples

Given this GitHub progress comment line:

`Audit ID asmb-0123456789abcdef. Private lookup: ~/.voyager/state/assembly/audit/iterwheel/voyager/92/asmb-0123456789abcdef.json. SOP: rules/VOY-1823-SOP-Assembly-OMP-Audit-Lookup.md.`

Run:

```sh
python -m json.tool \
  ~/.voyager/state/assembly/audit/iterwheel/voyager/92/asmb-0123456789abcdef.json
```

To locate a manifest when only the audit ID is known:

```sh
find ~/.voyager/state/assembly/audit -name 'asmb-0123456789abcdef.json' -print
```

To create a simple local HTML export from a JSONL transcript:

```sh
python - <<'PY'
import html
import json
from pathlib import Path

jsonl = Path("TRANSCRIPT.jsonl").expanduser()
out = jsonl.with_suffix(".html")
parts = ["<html><body><pre>"]
for line in jsonl.read_text(encoding="utf-8").splitlines():
    try:
        line = json.dumps(json.loads(line), indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        pass
    parts.append(html.escape(line))
parts.append("</pre></body></html>")
out.write_text("\n".join(parts), encoding="utf-8")
print(out)
PY
```

## Privacy Boundary

GitHub may contain:

- the audit ID
- the deterministic private lookup path
- the SOP filename
- non-sensitive status summary, branch name, PR number, and issue number

Wukong local storage may contain:

- the full audit manifest
- local checkout path
- OMP session JSONL path
- optional exported HTML path
- verification commands and adapter summary
- commit SHAs

The manifest writer redacts known token-shaped values and secret-keyed fields.
Operators must still treat manifests and transcripts as private local records.

## Missing-Record Troubleshooting

If the manifest is missing:

- Confirm the audit ID was copied exactly.
- Confirm `ASSEMBLY_AUDIT_DIR` was not set to a non-default root.
- Search the full audit tree with `find ~/.voyager/state/assembly/audit -name '<audit-id>.json' -print`.
- Check bridge logs for `writeAssemblyAuditManifest`.
- If the Assembly run predated this SOP, the GitHub comment may not have a
  manifest-backed audit ID.

If the manifest exists but `omp_session_jsonl_path` is empty:

- The OMP session directory may not have existed when the backend finished.
- The OMP process may have failed before creating a transcript.
- Search `~/.omp/agent/sessions` for the Assembly checkout directory name.
- Use the checkout path, issue number, branch name, delivery ID, and timestamp
  to correlate the run manually.

If the transcript exists but the checkout path is missing:

- The checkout may have been cleaned by an older backend or operator cleanup.
- Use the commit SHA and PR branch as the durable GitHub-side record.

## Change History

| Date | Change | By |
|------|--------|----|
| 2026-05-24 | Added private Assembly OMP audit lookup procedure | Codex |
