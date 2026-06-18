from __future__ import annotations

import stat
from pathlib import Path

from voyager.bots.assembly.adapters import _latest_omp_session_jsonl
from voyager.bots.assembly.audit import (
    AssemblyAuditManifest,
    _estimate_tokens_from_session,
    audit_manifest_path,
    find_audit_manifest,
    generate_audit_id,
    is_audit_id,
    load_audit_manifest,
    lookup_hint,
    write_audit_manifest,
)
from voyager.bots.assembly.comment import build_assembly_comment
from voyager.bots.assembly.constants import ASSEMBLY_AUDIT_DIR_ENV, ASSEMBLY_AUDIT_SOP


def test_generate_audit_id_is_stable_and_non_secret() -> None:
    audit_id = generate_audit_id(
        delivery_id="delivery-1",
        repository="iterwheel/voyager",
        issue_number=92,
    )

    assert audit_id == generate_audit_id(
        delivery_id="delivery-1",
        repository="iterwheel/voyager",
        issue_number=92,
    )
    assert audit_id != generate_audit_id(
        delivery_id="delivery-2",
        repository="iterwheel/voyager",
        issue_number=92,
    )
    assert is_audit_id(audit_id)


def test_manifest_path_write_load_and_lookup(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ASSEMBLY_AUDIT_DIR_ENV, str(tmp_path))
    audit_id = "asmb-0123456789abcdef"
    manifest = AssemblyAuditManifest(
        audit_id=audit_id,
        repository="iterwheel/voyager",
        issue_number=92,
        delivery_id="delivery-1",
        backend_name="pi-oh-my-pi-deepseek",
        branch_name="92-private-assembly-omp-audit-manifests",
        checkout_dir="/Users/frank/.voyager/state/assembly/assembly-omp-x/repo",
        omp_session_jsonl_path="/Users/frank/.omp/agent/sessions/x/run.jsonl",
        verification_commands=("uv run pytest tests/",),
        adapter_status="executed",
        commit_shas=("a" * 40,),
    )

    expected_path = tmp_path / "iterwheel" / "voyager" / "92" / f"{audit_id}.json"
    assert (
        audit_manifest_path(
            audit_id=audit_id,
            repository="iterwheel/voyager",
            issue_number=92,
        )
        == expected_path
    )

    path = write_audit_manifest(manifest)

    assert path == expected_path
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert find_audit_manifest(audit_id, root=tmp_path) == expected_path
    loaded = load_audit_manifest(path)
    assert loaded.audit_id == audit_id
    assert loaded.repository == "iterwheel/voyager"
    assert loaded.issue_number == 92
    assert loaded.commit_shas == ("a" * 40,)


def test_manifest_redacts_token_values_and_secret_keys() -> None:
    manifest = AssemblyAuditManifest(
        audit_id="asmb-0123456789abcdef",
        repository="iterwheel/voyager",
        issue_number=92,
        delivery_id="delivery-1",
        backend_name="pi-oh-my-pi-deepseek",
        adapter_summary="token ghp_super_secret should not survive",
        extra={
            "installation_token": "ghs_super_secret",
            "nested": {
                "api_key": "plain-secret",
                "message": "saw ghp_nested_secret in output",
            },
        },
    )

    data = manifest.to_dict()

    assert data["adapter_summary"] == "token [redacted] should not survive"
    assert data["extra"]["installation_token"] == "[redacted]"
    assert data["extra"]["nested"]["api_key"] == "[redacted]"
    assert data["extra"]["nested"]["message"] == "saw [redacted] in output"


def test_manifest_carries_sanitized_failure_diagnostic() -> None:
    manifest = AssemblyAuditManifest(
        audit_id="asmb-0123456789abcdef",
        repository="iterwheel/voyager",
        issue_number=93,
        delivery_id="delivery-1",
        backend_name="pi-oh-my-pi-deepseek",
        failure_debug_bundle_path="/Users/frank/.voyager/state/assembly/failures/x",
        failure_diagnostic={
            "phase": "git_push",
            "command_category": "git",
            "exit_code": 128,
            "timed_out": False,
            "stderr_tail": (
                "remote: invalid "
                "ASSEMBLY_GITHUB_TOKEN=ghs_manifest_secret "
                "OPENAI_API_KEY=sk-proj-secret"
            ),
        },
    )

    data = manifest.to_dict()

    assert data["failure_debug_bundle_path"].endswith("/failures/x")
    assert data["failure_diagnostic"]["phase"] == "git_push"
    serialized = str(data)
    assert "ghs_manifest_secret" not in serialized
    assert "sk-proj-secret" not in serialized
    assert "ASSEMBLY_GITHUB_TOKEN=" not in serialized


def test_comment_renders_public_audit_hint_only() -> None:
    audit_id = "asmb-0123456789abcdef"

    body = build_assembly_comment(
        status="applied",
        contract={
            "repository": "iterwheel/voyager",
            "issue_number": 92,
            "acceptance_criteria": [],
        },
        adapter_result={"status": "executed", "summary": "done"},
        branch={"name": "92-private-assembly-omp-audit-manifests"},
        pull_request={"number": 123, "action": "opened"},
        audit_id=audit_id,
    )

    assert f"Audit ID `{audit_id}`" in body
    assert f"rules/{ASSEMBLY_AUDIT_SOP}" in body
    assert "/Users/frank/.omp/agent/sessions" not in body
    assert "ghp_" not in body


def test_comment_renders_public_backend_failure_diagnostics_safely() -> None:
    body = build_assembly_comment(
        status="failed",
        contract={
            "repository": "iterwheel/voyager",
            "issue_number": 93,
            "acceptance_criteria": [],
        },
        adapter_result={
            "status": "failed",
            "summary": "Git push failed for Assembly OMP backend.",
            "details": {
                "failure_debug_bundle_path": "/Users/frank/.voyager/state/assembly/failures/x",
                "failure_diagnostic": {
                    "phase": "git_push",
                    "command_category": "git",
                    "exit_code": 128,
                    "timed_out": False,
                    "stderr_tail": (
                        "remote: invalid "
                        "ASSEMBLY_GITHUB_TOKEN=ghs_comment_secret "
                        "DEEPSEEK_API_KEY=sk-live-secret"
                    ),
                },
            },
        },
        branch={"name": "93-failure-diagnostics"},
        pull_request={"action": "skipped_no_changes"},
        audit_id="asmb-0123456789abcdef",
    )

    assert "**Backend failure diagnostics:**" in body
    assert "- Phase: `git_push`" in body
    assert "- Command: `git`" in body
    assert "- Exit code: `128`" in body
    assert "- Debug bundle: recorded in the private audit manifest." in body
    assert "ghs_comment_secret" not in body
    assert "sk-live-secret" not in body
    assert "ASSEMBLY_GITHUB_TOKEN=" not in body


def test_comment_does_not_claim_publish_continued_after_failed_l1_advisory() -> None:
    body = build_assembly_comment(
        status="failed",
        contract={
            "repository": "iterwheel/voyager",
            "issue_number": 161,
            "acceptance_criteria": [],
        },
        adapter_result={
            "status": "failed",
            "summary": "Git push failed after advisory findings.",
            "details": {
                "ac_spotcheck_maturity": "L1",
                "ac_spotcheck": {
                    "ok": False,
                    "findings": [
                        {
                            "source": "acceptance_criterion",
                            "criterion": "Add value `mandatory-bind`",
                            "missing_tokens": ["mandatory-bind"],
                        }
                    ],
                },
            },
        },
        branch={"name": "161-maturity-level-gate-field"},
        pull_request={"action": "skipped_no_changes"},
        audit_id="asmb-0123456789abcdef",
    )

    assert "**Advisory gate findings:**" in body
    assert "publish did not complete" in body
    assert "publish continued" not in body


def test_comment_wraps_failure_tail_with_backtick_safe_code_span() -> None:
    body = build_assembly_comment(
        status="failed",
        contract={
            "repository": "iterwheel/voyager",
            "issue_number": 93,
            "acceptance_criteria": [],
        },
        adapter_result={
            "status": "failed",
            "summary": "Verification failed.",
            "details": {
                "failure_diagnostic": {
                    "phase": "verification",
                    "command_category": "verification",
                    "exit_code": 2,
                    "timed_out": False,
                    "stderr_tail": "parser saw `bad` token @iterwheel",
                },
            },
        },
        branch={"name": "93-failure-diagnostics"},
        pull_request={"action": "skipped_no_changes"},
        audit_id="asmb-0123456789abcdef",
    )

    assert "- Stderr tail: `` parser saw `bad` token @iterwheel ``" in body
    assert "- Stderr tail: `parser saw `bad` token @iterwheel`" not in body


def test_lookup_hint_names_path_and_sop() -> None:
    audit_id = "asmb-0123456789abcdef"

    hint = lookup_hint(audit_id, "iterwheel/voyager", 92)

    assert hint == (
        "Audit ID `asmb-0123456789abcdef`. Private lookup: "
        "`~/.voyager/state/assembly/audit/iterwheel/voyager/92/"
        "asmb-0123456789abcdef.json`. SOP: "
        "`rules/VOY-1823-SOP-Assembly-OMP-Audit-Lookup.md`."
    )


def test_lookup_hint_respects_audit_dir_override(monkeypatch, tmp_path: Path) -> None:
    audit_root = tmp_path / "custom-audit"
    monkeypatch.setenv(ASSEMBLY_AUDIT_DIR_ENV, str(audit_root))

    hint = lookup_hint("asmb-0123456789abcdef", "iterwheel/voyager", 92)

    assert (
        f"Private lookup: `{audit_root}/iterwheel/voyager/92/asmb-0123456789abcdef.json`."
    ) in hint


def test_missing_omp_session_fallback(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    checkout_dir = tmp_path / ".voyager" / "state" / "assembly" / "assembly-omp-x" / "repo"

    assert _latest_omp_session_jsonl(checkout_dir) is None


def test_latest_omp_session_jsonl_skips_transient_stat_failures(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    session_dir = tmp_path / ".omp" / "agent" / "sessions" / "x-assembly-omp-x-repo"
    session_dir.mkdir(parents=True)
    stale = session_dir / "stale.jsonl"
    newest = session_dir / "newest.jsonl"
    stale.write_text("stale\n", encoding="utf-8")
    newest.write_text("newest\n", encoding="utf-8")
    checkout_dir = tmp_path / ".voyager" / "state" / "assembly" / "assembly-omp-x" / "repo"

    original_stat = Path.stat

    def fake_stat(self: Path, *args, **kwargs):
        if self == stale:
            raise OSError("rotated")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)

    assert _latest_omp_session_jsonl(checkout_dir) == str(newest)


def test_estimate_tokens_from_pi_session_uses_nested_usage_total_tokens(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "session.jsonl"
    session_path.write_text(
        "\n".join(
            [
                '{"type":"session","version":3}',
                (
                    '{"type":"message","message":{"role":"assistant",'
                    '"content":[{"type":"text","text":"not double counted"}],'
                    '"usage":{"input":100,"output":23,"totalTokens":123}}}'
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert _estimate_tokens_from_session(str(session_path)) == 123


def test_estimate_tokens_from_pi_session_uses_snake_case_total_tokens(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "session.jsonl"
    session_path.write_text(
        (
            '{"type":"message","message":{"role":"assistant",'
            '"content":[{"type":"text","text":"not double counted"}],'
            '"usage":{"prompt_tokens":200,"completion_tokens":45,"total_tokens":245}}}\n'
        ),
        encoding="utf-8",
    )

    assert _estimate_tokens_from_session(str(session_path)) == 245


def test_estimate_tokens_from_session_ignores_invalid_utf8_transcripts(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "session.jsonl"
    session_path.write_bytes(b"\xff\xfe\x00not-json")

    assert _estimate_tokens_from_session(str(session_path)) == 0


def test_estimate_tokens_from_pi_session_reads_nested_content_blocks(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "session.jsonl"
    text = "a" * 40
    thinking = "b" * 20
    output = "c" * 16
    session_path.write_text(
        "\n".join(
            [
                (
                    '{"type":"message","message":{"role":"assistant",'
                    f'"content":[{{"type":"text","text":"{text}"}},'
                    f'{{"type":"thinking","thinking":"{thinking}"}}]}}}}'
                ),
                (
                    '{"type":"message","message":{"role":"toolResult",'
                    f'"content":[{{"type":"text","text":"{output}"}}],'
                    '"isError":false}}'
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert _estimate_tokens_from_session(str(session_path)) == (40 + 20 + 16) // 4


# ---------------------------------------------------------------------------
# Loop Summary telemetry (VOY-1817 Surface 20 / per-PR round tracking)
# ---------------------------------------------------------------------------


def test_loop_summary_round_trip(monkeypatch, tmp_path: Path) -> None:
    """A LoopSummary written to JSONL can be read back with identical fields."""
    from voyager.bots.assembly.audit import (
        LoopSummary,
        append_loop_summary,
        load_loop_summaries,
    )

    monkeypatch.setenv(ASSEMBLY_AUDIT_DIR_ENV, str(tmp_path / "audit"))
    summary = LoopSummary(
        repository="iterwheel/voyager",
        issue_number=42,
        pr_number=42,
        rounds=1,
        commits=3,
        est_tokens=1500,
        timestamp="2026-06-17T12:00:00",
        audit_id="asmb-0011223344556677",
    )
    append_loop_summary(summary)

    loaded = load_loop_summaries(repository="iterwheel/voyager", issue_number=42)
    assert len(loaded) == 1
    assert loaded[0] == summary


def test_loop_summary_round_counts_increment(tmp_path: Path) -> None:
    """After N simulated runs, each record has the expected round number."""
    from voyager.bots.assembly.audit import (
        LoopSummary,
        append_loop_summary,
        load_loop_summaries,
    )

    root = tmp_path / "audit-root"
    repo = "iterwheel/voyager"
    issue = 42

    for i in range(1, 5):
        summary = LoopSummary(
            repository=repo,
            issue_number=issue,
            pr_number=issue,
            rounds=i,
            commits=i,
            est_tokens=100 * i,
            timestamp=f"2026-06-17T12:0{i}:00",
        )
        append_loop_summary(summary, root=root)

    loaded = load_loop_summaries(repository=repo, issue_number=issue, root=root)
    assert len(loaded) == 4
    for idx, record in enumerate(loaded, start=1):
        assert record.rounds == idx
        assert record.commits == idx


def test_loop_summary_round_assignment_ignores_stale_pre_append_reads(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Round assignment happens under the append lock, not from a stale read."""
    import voyager.bots.assembly.writeback as writeback
    from voyager.bots.assembly.audit import load_loop_summaries

    root = tmp_path / "audit-root"
    monkeypatch.setattr(writeback, "load_loop_summaries", lambda **_kwargs: [], raising=False)

    for audit_id in ("asmb-0011223344556677", "asmb-8899aabbccddeeff"):
        writeback._record_loop_summary(
            repository="iterwheel/voyager",
            issue_number=42,
            pr_number=172,
            adapter_result={"commit_shas": ["a" * 40]},
            audit_id=audit_id,
            root=root,
        )

    loaded = load_loop_summaries(repository="iterwheel/voyager", issue_number=42, root=root)
    assert [record.rounds for record in loaded] == [1, 2]


def test_loop_summary_file_is_jsonl(tmp_path: Path) -> None:
    """The underlying file is valid JSONL — one JSON object per line."""
    from voyager.bots.assembly.audit import (
        LoopSummary,
        append_loop_summary,
        loop_summary_path,
    )

    repo = "iterwheel/voyager"
    issue = 99
    root = tmp_path / "audit-root"

    for i in range(1, 4):
        summary = LoopSummary(
            repository=repo,
            issue_number=issue,
            pr_number=issue,
            rounds=i,
            commits=0,
            est_tokens=0,
            timestamp="2026-06-17T00:00:00",
        )
        append_loop_summary(summary, root=root)

    path = loop_summary_path(repository=repo, issue_number=issue, root=root)
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3

    import json

    for idx, raw in enumerate(lines, start=1):
        data = json.loads(raw)
        assert data["rounds"] == idx
        assert data["commits"] == 0


def test_loop_summary_file_permissions_are_private(tmp_path: Path) -> None:
    """Loop summary telemetry is private audit state and must be 0600."""
    from voyager.bots.assembly.audit import (
        LoopSummary,
        append_loop_summary_with_next_round,
        loop_summary_path,
    )

    repo = "iterwheel/voyager"
    issue = 100
    root = tmp_path / "audit-root"

    append_loop_summary_with_next_round(
        LoopSummary(
            repository=repo,
            issue_number=issue,
            pr_number=issue,
            rounds=0,
            commits=0,
            est_tokens=0,
            timestamp="2026-06-17T00:00:00",
        ),
        root=root,
    )

    path = loop_summary_path(repository=repo, issue_number=issue, root=root)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_loop_summary_append_chmods_existing_file(tmp_path: Path) -> None:
    """Appending also tightens older loop-summary files created with a broad umask."""
    from voyager.bots.assembly.audit import (
        LoopSummary,
        append_loop_summary_with_next_round,
        loop_summary_path,
    )

    repo = "iterwheel/voyager"
    issue = 101
    root = tmp_path / "audit-root"
    path = loop_summary_path(repository=repo, issue_number=issue, root=root)
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    path.chmod(0o644)

    append_loop_summary_with_next_round(
        LoopSummary(
            repository=repo,
            issue_number=issue,
            pr_number=issue,
            rounds=0,
            commits=0,
            est_tokens=0,
            timestamp="2026-06-17T00:00:00",
        ),
        root=root,
    )

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
