from __future__ import annotations

import pytest

from voyager.bots.clearance.pipeline import (
    _is_clean_current_codex_review,
    _latest_clean_codex_review_after_thread,
)


def _review(body: str, *, commit_id: str = "head-sha") -> dict:
    return {
        "id": 123,
        "user": {"login": "chatgpt-codex-connector[bot]"},
        "body": body,
        "submitted_at": "2026-05-11T13:00:00Z",
        "commit_id": commit_id,
        "state": "COMMENTED",
    }


def _thread() -> dict:
    return {
        "comments": {
            "nodes": [
                {
                    "databaseId": 100001,
                    "author": {"login": "chatgpt-codex-connector"},
                    "body": "**P1** old finding.",
                    "createdAt": "2026-05-11T12:00:00Z",
                }
            ]
        }
    }


@pytest.mark.parametrize(
    "body",
    [
        "Codex Review: Didn't find any major issues.",
        "Codex Review: Did not find any major issues.",
        "Codex Review: No major issues found.",
        "Codex Review: No major issues found in this PR.",
    ],
)
def test_clean_current_codex_review_accepts_exact_canonical_verdicts(body: str) -> None:
    assert _is_clean_current_codex_review(_review(body), head_sha="head-sha") is True


@pytest.mark.parametrize(
    "body",
    [
        "Codex Review: No major issues in docs, but P1 remains in pipeline.py.",
        "Codex Review: Didn't find any major issues. However, P1 remains.",
        "Codex Review: Looks clean, no issues.",
        "### Codex Review\n\nNo major issues found.",
    ],
)
def test_clean_current_codex_review_rejects_mixed_or_noncanonical_text(body: str) -> None:
    assert _is_clean_current_codex_review(_review(body), head_sha="head-sha") is False


def test_clean_current_codex_review_requires_current_head() -> None:
    assert (
        _is_clean_current_codex_review(
            _review("Codex Review: No major issues found."), head_sha="other"
        )
        is False
    )


def test_latest_clean_review_rejects_when_newer_current_head_codex_review_is_nonclean() -> None:
    older_clean = {
        **_review("Codex Review: No major issues found."),
        "submitted_at": "2026-05-11T13:00:00Z",
    }
    newer_nonclean = {
        **_review("### Codex Review\n\nHere are some automated review suggestions."),
        "submitted_at": "2026-05-11T13:30:00Z",
    }

    assert (
        _latest_clean_codex_review_after_thread(
            [older_clean, newer_nonclean],
            head_sha="head-sha",
            thread_dict=_thread(),
        )
        is None
    )


def test_latest_clean_review_accepts_when_latest_current_head_codex_review_is_clean() -> None:
    older_nonclean = {
        **_review("### Codex Review\n\nHere are some automated review suggestions."),
        "submitted_at": "2026-05-11T13:00:00Z",
    }
    newer_clean = {
        **_review("Codex Review: No major issues found."),
        "submitted_at": "2026-05-11T13:30:00Z",
    }

    assert (
        _latest_clean_codex_review_after_thread(
            [older_nonclean, newer_clean],
            head_sha="head-sha",
            thread_dict=_thread(),
        )
        == newer_clean
    )
