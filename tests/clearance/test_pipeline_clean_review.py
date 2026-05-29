from __future__ import annotations

import pytest

from voyager.bots.clearance.pipeline import (
    _is_clean_current_codex_review,
    _latest_clean_codex_issue_comment_after_thread,
    _latest_clean_codex_review_after_thread,
    _latest_clean_codex_signal_after_thread,
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


def _issue_comment(
    body: str,
    *,
    created_at: str = "2026-05-11T13:00:00Z",
    login: str = "chatgpt-codex-connector[bot]",
) -> dict:
    return {
        "id": 456,
        "user": {"login": login},
        "body": body,
        "created_at": created_at,
    }


@pytest.mark.parametrize(
    "body",
    [
        "Codex Review: Didn't find any major issues.",
        "Codex Review: Did not find any major issues.",
        "Codex Review: No major issues found.",
        "Codex Review: No major issues found in this PR.",
        (
            "Codex Review: Didn't find any major issues. Nice work!\n\n"
            "<details><summary>About Codex</summary>Boilerplate.</details>"
        ),
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


def test_latest_clean_issue_comment_accepts_current_head_clean_signal() -> None:
    clean = _issue_comment(
        "Codex Review: Didn't find any major issues. Nice work!\n\n"
        "<details><summary>About Codex</summary>Boilerplate.</details>"
    )

    assert (
        _latest_clean_codex_issue_comment_after_thread(
            [clean],
            current_head_updated_at="2026-05-11T12:45:00Z",
            thread_dict=_thread(),
        )
        == clean
    )


def test_latest_clean_issue_comment_requires_comment_after_current_head_update() -> None:
    clean_before_update = _issue_comment(
        "Codex Review: No major issues found.",
        created_at="2026-05-11T12:30:00Z",
    )

    assert (
        _latest_clean_codex_issue_comment_after_thread(
            [clean_before_update],
            current_head_updated_at="2026-05-11T12:45:00Z",
            thread_dict=_thread(),
        )
        is None
    )


def test_latest_clean_issue_comment_rejects_when_newer_codex_comment_is_nonclean() -> None:
    older_clean = _issue_comment(
        "Codex Review: No major issues found.",
        created_at="2026-05-11T13:00:00Z",
    )
    newer_nonclean = _issue_comment(
        "Codex Review: Didn't find any major issues. However, P1 remains.",
        created_at="2026-05-11T13:30:00Z",
    )

    assert (
        _latest_clean_codex_issue_comment_after_thread(
            [older_clean, newer_nonclean],
            current_head_updated_at="2026-05-11T12:45:00Z",
            thread_dict=_thread(),
        )
        is None
    )


def test_latest_clean_signal_rejects_newer_nonclean_issue_comment_after_clean_review() -> None:
    older_clean_review = {
        **_review("Codex Review: No major issues found."),
        "submitted_at": "2026-05-11T13:00:00Z",
    }
    newer_nonclean_comment = _issue_comment(
        "Codex Review: Didn't find any major issues. However, P1 remains.",
        created_at="2026-05-11T13:30:00Z",
    )

    assert (
        _latest_clean_codex_signal_after_thread(
            reviews=[older_clean_review],
            issue_comments=[newer_nonclean_comment],
            head_sha="head-sha",
            current_head_updated_at="2026-05-11T12:45:00Z",
            thread_dict=_thread(),
        )
        is None
    )
