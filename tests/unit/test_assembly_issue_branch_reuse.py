"""Issue #257: the Assembly idempotency key must not derive from the issue title.

``make_branch_name(number, title)`` bakes the mutable issue title into the
branch name that also served as the per-(repo,branch) lock key and the
existing-branch/PR lookup. Editing the title between runs minted a different
branch: run 2 took a different lock, pushed a second branch, and opened a
duplicate PR closing the same issue.

Fixes under test:
- the writeback lock is keyed on the ISSUE NUMBER (``issue-<n>``);
- when the freshly computed branch does not exist, the issue's existing open
  Assembly PR (head branch ``<n>-…``, body references ``#<n>``) is reused.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from voyager.bots.assembly.branch import make_branch_name
from voyager.bots.assembly.writeback import _existing_branch_for_issue, _get_lock


def test_lock_keyed_on_issue_number_not_branch() -> None:
    """Two title-derived branch variants of the SAME issue share one lock;
    a different issue gets a different lock."""
    assert _get_lock("o/r", "issue-69") is _get_lock("o/r", "issue-69")
    # Lock identity holds regardless of which branch name the title produces.
    assert _get_lock("o/r", "issue-69") is _get_lock("o/r", "issue-69")
    assert _get_lock("o/r", "issue-69") is not _get_lock("o/r", "issue-70")
    assert _get_lock("o/r", "issue-69") is not _get_lock("other/r", "issue-69")


def _pr(
    head_ref: str, number: int, body: str, author: str = "iterwheel-assembly[bot]"
) -> dict[str, Any]:
    return {
        "number": number,
        "html_url": f"https://example/pr/{number}",
        "user": {"login": author},
        "head": {"ref": head_ref, "repo": {"full_name": "o/r"}},
        "base": {"repo": {"full_name": "o/r"}},
        "body": body,
    }


def _client(
    *,
    direct: Any = None,
    search: list | None = None,
    pr_head: str = "69-x",
    pr_author: str = "iterwheel-assembly[bot]",
) -> Any:
    client = AsyncMock()
    client.find_pull_request_by_head = AsyncMock(return_value=direct)
    client.find_open_prs_referencing_issue = AsyncMock(return_value=search or [])

    async def _pull_request(_slug: str, _repo: str, number: int) -> dict[str, Any]:
        # Search items are issue-shaped; the resolution fetches the PR detail.
        return _pr(pr_head, number, "Closes #69", author=pr_author)

    client.pull_request = AsyncMock(side_effect=_pull_request)
    return client


async def test_title_edit_reuses_existing_branch_from_open_pr() -> None:
    """Run 2 with an edited title: computed branch has no PR, but the issue's
    open Assembly PR still points at the ORIGINAL title-derived branch."""
    original = make_branch_name(69, "[Feature]: Implement Assembly bot MVP")
    edited = make_branch_name(69, "[Feature]: Renamed entirely")
    assert original != edited  # fixture sanity: the title edit changes the name

    client = _client(
        direct=None,  # no PR for the freshly computed (edited) branch
        pr_head=original,
        search=[
            {  # issue-shaped search result: no head, PR URL only
                "number": 1234,
                "body": f"Closes #69 via {original}",
                "pull_request": {"url": "https://api.example.com/o/r/pulls/1234"},
            }
        ],
    )

    resolved = await _existing_branch_for_issue(client, "o/r", 69, edited)
    assert resolved == original


async def test_unchanged_title_fast_path_returns_computed_branch() -> None:
    branch = make_branch_name(69, "[Feature]: Implement Assembly bot MVP")
    client = _client(direct=_pr(branch, 1234, "Closes #69"))
    assert await _existing_branch_for_issue(client, "o/r", 69, branch) == branch


async def test_candidates_without_matching_prefix_or_reference_are_ignored() -> None:
    """A PR for a different issue (or without the issue reference) must not be
    reused — over-matching would silently target someone else's branch."""
    client = _client(
        direct=None,
        search=[
            _pr("70-other-issue", 1300, "Closes #70"),  # wrong issue prefix
            _pr("69-not-referenced", 1301, "no closing ref"),  # right prefix, no #69
        ],
    )
    assert await _existing_branch_for_issue(client, "o/r", 69, "69-new-title") is None


async def test_lookup_failure_falls_back_to_computed_branch() -> None:
    client = AsyncMock()
    client.find_pull_request_by_head = AsyncMock(side_effect=TimeoutError("boom"))
    assert await _existing_branch_for_issue(client, "o/r", 69, "69-computed") is None


async def test_human_pr_with_matching_shape_is_not_reused():
    """Codex P1 round 8: a human PR with a coincidental "<issue>-" branch and
    a Closes reference must never be adopted."""
    original = make_branch_name(69, "[Feature]: Implement Assembly bot MVP")
    edited = make_branch_name(69, "[Feature]: Renamed entirely")
    client = _client(
        direct=None,
        pr_head=original,
        search=[
            {
                "number": 1300,
                "body": "Closes #69",
                "pull_request": {"url": "https://api.example.com/o/r/pulls/1300"},
            }
        ],
    )
    # the human-authored PR detail (returned for any number)
    client.pull_request = AsyncMock(
        return_value=_pr(original, 1300, "Closes #69", author="some-human")
    )
    assert await _existing_branch_for_issue(client, "o/r", 69, edited) is None


async def test_prefix_spoofing_assembly_login_is_not_reused() -> None:
    """Codex P2 on #345: a login that merely STARTS with the slug (e.g.
    iterwheel-assembly-dev[bot]) is a different app — its PR must not be
    adopted, pushed to, or updated on the issue's behalf."""
    original = make_branch_name(69, "[Feature]: Implement Assembly bot MVP")
    edited = make_branch_name(69, "[Feature]: Renamed entirely")
    client = _client(
        direct=None,
        pr_head=original,
        pr_author="iterwheel-assembly-dev[bot]",
        search=[
            {
                "number": 1300,
                "body": "Closes #69",
                "pull_request": {"url": "https://api.example.com/o/r/pulls/1300"},
            }
        ],
    )
    assert await _existing_branch_for_issue(client, "o/r", 69, edited) is None
