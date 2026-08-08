"""Unit tests for voyager.core.merge_loop."""

from __future__ import annotations

import pytest

from voyager.core.merge_loop import (
    MERGE_ALLOWED_REPOS,
    MergeDecision,
    MergeLoopSummary,
    merge_allowed_repos,
    parse_readiness,
)
from voyager.core.resolve_conversation import ResolveConversationError


class TestMergeAllowedRepos:
    def test_builtin_is_sandbox_only(self):
        assert frozenset({"iterwheel/voyager-sandbox"}) == MERGE_ALLOWED_REPOS

    def test_env_extras_extend_ceiling(self, monkeypatch):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "frankyxhl/fx_bin")
        assert "frankyxhl/fx_bin" in merge_allowed_repos()
        assert "iterwheel/voyager-sandbox" in merge_allowed_repos()

    def test_extras_normalize_case(self, monkeypatch):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "Frankyxhl/FX_bin")
        assert "frankyxhl/fx_bin" in merge_allowed_repos()

    def test_malformed_extra_fails_closed(self, monkeypatch):
        monkeypatch.setenv("VOYAGER_MERGE_EXTRA_REPOS", "not-a-repo-path")
        with pytest.raises(ResolveConversationError):
            merge_allowed_repos()


class TestDecisionRedaction:
    def test_sandbox_repo_is_raw(self):
        d = MergeDecision(repo="iterwheel/voyager-sandbox", pr=7, action="merged")
        assert d.public() == {
            "repo": "iterwheel/voyager-sandbox",
            "action": "merged",
            "pr": 7,
            "reason": "",
        }

    def test_other_repo_is_redacted(self):
        d = MergeDecision(repo="frankyxhl/fx_bin", pr=88, action="merged")
        assert d.public() == {"repo": "frankyxhl/fx_bin", "action": "merged", "redacted": True}


class TestSummary:
    def test_counts_and_public_dict(self):
        ds = (
            MergeDecision(repo="frankyxhl/fx_bin", pr=1, action="merged"),
            MergeDecision(repo="frankyxhl/fx_bin", pr=2, action="would_merge"),
            MergeDecision(repo="frankyxhl/fx_bin", pr=3, action="skipped", reason="draft"),
        )
        s = MergeLoopSummary(
            repos_scanned=("frankyxhl/fx_bin",),
            repos_skipped=(),
            prs_scanned=3,
            decisions=ds,
            capped=False,
            dry_run=False,
            errors=(),
        )
        assert s.merged == 1
        assert s.would_merge == 1
        pub = s.to_public_dict()
        assert pub["merged"] == 1
        assert pub["decision_count"] == 3
        assert pub["errors"] == []


READINESS_BODY = """<!-- iterwheel:clearance-readiness -->
## Clearance

🚦 Stage: 3 - Ready for approval (`clearance-3-ready-for-approval`)
✅ Threads: 0 blocking

<details>
<summary>Details</summary>

- Head SHA: `a96782f4e41207e63d63bd552f9b4fa5399c7eb8`
</details>
"""


class TestParseReadiness:
    def test_parses_stage_and_head(self):
        assert parse_readiness(READINESS_BODY) == (
            3,
            "a96782f4e41207e63d63bd552f9b4fa5399c7eb8",
        )

    def test_stage_2_parses_as_2(self):
        body = READINESS_BODY.replace("Stage: 3", "Stage: 2")
        parsed = parse_readiness(body)
        assert parsed is not None
        assert parsed[0] == 2

    def test_missing_marker_returns_none(self):
        assert parse_readiness(READINESS_BODY.split("\n", 1)[1]) is None

    def test_missing_head_sha_returns_none(self):
        body = READINESS_BODY.replace("Head SHA", "Head Something")
        assert parse_readiness(body) is None

    def test_short_sha_returns_none(self):
        body = READINESS_BODY.replace("a96782f4e41207e63d63bd552f9b4fa5399c7eb8", "a96782f")
        assert parse_readiness(body) is None
