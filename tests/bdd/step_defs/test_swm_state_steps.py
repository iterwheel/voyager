"""Step definitions for SWM state store BDD scenarios."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pytest_bdd import given, parsers, scenarios, then, when

scenarios("../features/swm_state.feature")

REPO = "owner/repo"
PR = 49
THREAD_ID = "PRRT_t1"


def _ts() -> datetime:
    return datetime(2026, 5, 8, 12, 0, 0, tzinfo=UTC)


def _make_poll(
    *, repo: str = REPO, pr: int = PR, status: str = "pending", head_sha: str = "abc1234"
):
    from voyager.bots.clearance.models import PollRecord, Status
    from voyager.core.state import StateStore  # noqa: F401 — imported for module check

    return PollRecord(ts=_ts(), repo=repo, pr=pr, head_sha=head_sha, status=Status(status))


def _make_snapshot(
    *,
    repo: str = REPO,
    pr: int = PR,
    thread_id: str = THREAD_ID,
    verdict: str = "RESOLVED",
):
    from voyager.bots.clearance.models import Evidence, Severity, ThreadSnapshot, Verdict

    return ThreadSnapshot(
        thread_id=thread_id,
        repo=repo,
        pr=pr,
        first_seen=_ts(),
        last_polled=_ts(),
        codex_comment_id=1001,
        path="app.py",
        codex_severity=Severity.P2,
        effective_severity=Severity.P2,
        verdict=Verdict(verdict),
        evidence=Evidence(code_change_commit="abc12345"),
    )


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------


@given("a temporary StateStore", target_fixture="store")
def temp_state_store(tmp_path: Path):
    from voyager.core.state import StateStore

    return StateStore(tmp_path / "state")


# ---------------------------------------------------------------------------
# Polls — given
# ---------------------------------------------------------------------------


@given(
    parsers.parse('a PollRecord for repo "{repo}" PR {pr:d} with status "{status}"'),
    target_fixture="poll",
)
def poll_record(repo: str, pr: int, status: str):
    return _make_poll(repo=repo, pr=pr, status=status)


@given('polls for repos "owner/repo" and "other/repo"', target_fixture="poll_pair")
def two_repo_polls() -> list:
    return [_make_poll(repo=REPO), _make_poll(repo="other/repo")]


@given('polls for PR 49 and PR 50 in "owner/repo"', target_fixture="poll_pair")
def two_pr_polls() -> list:
    return [_make_poll(pr=49), _make_poll(pr=50)]


@given(
    'two polls for repo "owner/repo" PR 49 with different head_sha values',
    target_fixture="poll_pair",
)
def two_sha_polls() -> list:
    return [_make_poll(head_sha="sha_first"), _make_poll(head_sha="sha_second")]


@given("no polls have been written")
def no_polls_written() -> None:
    pass


@given("two polls written with blank lines injected between them", target_fixture="poll_pair")
def polls_with_blanks(store) -> list:
    polls = [_make_poll(head_sha="sha_a"), _make_poll(head_sha="sha_b")]
    for p in polls:
        store.append_poll(p)
    polls_path = store._polls_path(REPO, PR)
    with polls_path.open("a") as f:
        f.write("\n   \n")
    return polls


# ---------------------------------------------------------------------------
# Polls — when
# ---------------------------------------------------------------------------


@when("the poll is appended and all polls are read", target_fixture="polls_result")
def append_and_read_polls(store, poll):
    store.append_poll(poll)
    return list(store.read_polls())


@when('polls are read filtered by repo "owner/repo"', target_fixture="polls_result")
def read_polls_by_repo(store, poll_pair):
    for p in poll_pair:
        store.append_poll(p)
    return list(store.read_polls(repo=REPO))


@when("polls are read filtered by PR 50", target_fixture="polls_result")
def read_polls_by_pr(store, poll_pair):
    for p in poll_pair:
        store.append_poll(p)
    return list(store.read_polls(pr=50))


@when('latest_poll is called for "owner/repo" PR 49', target_fixture="latest_poll_result")
def call_latest_poll(store, poll_pair):
    for p in poll_pair:
        store.append_poll(p)
    return store.latest_poll(repo=REPO, pr=PR)


@when(
    "the poll is appended and latest_poll is called for PR 9999",
    target_fixture="latest_poll_result",
)
def latest_poll_unknown(store, poll):
    store.append_poll(poll)
    return store.latest_poll(repo=REPO, pr=9999)


@when('latest_per_pr is called for "owner/repo"', target_fixture="per_pr_result")
def call_latest_per_pr(store, poll_pair):
    for p in poll_pair:
        store.append_poll(p)
    return store.latest_per_pr(REPO)


@when("all polls are read without filters", target_fixture="polls_result")
def read_all_polls(store):
    return list(store.read_polls())


# ---------------------------------------------------------------------------
# Polls — then
# ---------------------------------------------------------------------------


@then(parsers.parse("exactly {n:d} poll is returned"))
def exactly_n_polls(polls_result, n: int) -> None:
    assert len(polls_result) == n


@then(parsers.parse("exactly {n:d} polls are returned"))
def exactly_n_polls_plural(polls_result, n: int) -> None:
    assert len(polls_result) == n


@then(parsers.parse('the poll repo is "{repo}"'))
def poll_repo_is(polls_result, repo: str) -> None:
    assert polls_result[0].repo == repo


@then('all returned polls belong to repo "owner/repo"')
def all_polls_owner_repo(polls_result) -> None:
    assert all(p.repo == REPO for p in polls_result)


@then("all returned polls have PR number 50")
def all_polls_pr50(polls_result) -> None:
    assert all(p.pr == 50 for p in polls_result)


@then("the returned poll has the second head_sha")
def latest_poll_second_sha(latest_poll_result) -> None:
    assert latest_poll_result is not None
    assert latest_poll_result.head_sha == "sha_second"


@then("the latest poll is None")
def latest_poll_none(latest_poll_result) -> None:
    assert latest_poll_result is None


@then("the result has keys 49 and 50")
def per_pr_has_both_keys(per_pr_result) -> None:
    assert 49 in per_pr_result
    assert 50 in per_pr_result


@then("the poll list is empty")
def poll_list_empty(polls_result) -> None:
    assert polls_result == []


# ---------------------------------------------------------------------------
# Threads — given
# ---------------------------------------------------------------------------


@given(
    parsers.parse('a ThreadSnapshot for repo "{repo}" PR {pr:d} thread "{tid}"'),
    target_fixture="snapshot",
)
def thread_snapshot_fixture(repo: str, pr: int, tid: str):
    return _make_snapshot(repo=repo, pr=pr, thread_id=tid)


@given("two snapshots for the same thread_id but PR 49 and PR 50", target_fixture="snapshot_pair")
def two_pr_snapshots() -> list:
    return [
        _make_snapshot(pr=49, verdict="RESOLVED"),
        _make_snapshot(pr=50, verdict="OPEN"),
    ]


# ---------------------------------------------------------------------------
# Threads — when
# ---------------------------------------------------------------------------


@when("the thread is written and then read", target_fixture="thread_result")
def write_and_read_thread(store, snapshot):
    store.write_thread(snapshot)
    return store.read_thread(snapshot.repo, snapshot.pr, snapshot.thread_id)


@when("read_thread is called for a non-existent thread", target_fixture="thread_result")
def read_missing_thread(store):
    return store.read_thread("owner/repo", 99, "nonexistent_thread_id")


@when("three snapshots with different verdicts are written", target_fixture="history_result")
def write_three_snapshots(store, snapshot):
    from voyager.bots.clearance.models import Verdict

    snaps = [
        snapshot.model_copy(update={"verdict": Verdict.OPEN}),
        snapshot.model_copy(update={"verdict": Verdict.NEEDS_HUMAN_JUDGMENT}),
        snapshot.model_copy(update={"verdict": Verdict.RESOLVED}),
    ]
    for s in snaps:
        store.write_thread(s)
    history = store.read_thread_history(snapshot.repo, snapshot.pr, snapshot.thread_id)
    latest = store.read_thread(snapshot.repo, snapshot.pr, snapshot.thread_id)
    return {"history": history, "latest": latest}


@when("each is written and read back", target_fixture="snapshot_reads")
def write_and_read_two_snapshots(store, snapshot_pair):
    for s in snapshot_pair:
        store.write_thread(s)
    pr49 = store.read_thread(REPO, 49, THREAD_ID)
    pr50 = store.read_thread(REPO, 50, THREAD_ID)
    return {"pr49": pr49, "pr50": pr50}


# ---------------------------------------------------------------------------
# Threads — then
# ---------------------------------------------------------------------------


@then("the read snapshot matches the written snapshot")
def snapshot_matches(thread_result, snapshot) -> None:
    assert thread_result == snapshot


@then("the thread result is None")
def thread_result_none(thread_result) -> None:
    assert thread_result is None


@then(parsers.parse("the thread history length is {n:d}"))
def history_length(history_result, n: int) -> None:
    assert len(history_result["history"]) == n


@then("the latest thread has the third verdict")
def latest_is_third_verdict(history_result) -> None:
    from voyager.bots.clearance.models import Verdict

    assert history_result["latest"].verdict is Verdict.RESOLVED


@then(parsers.parse('pr {pr:d} snapshot has verdict "{verdict}"'))
def pr_snapshot_verdict(snapshot_reads, pr: int, verdict: str) -> None:
    key = f"pr{pr}"
    snap = snapshot_reads[key]
    assert snap is not None
    assert snap.verdict.value == verdict


# ---------------------------------------------------------------------------
# Ledger — given
# ---------------------------------------------------------------------------


@given(
    parsers.parse('two LedgerEntries for repo "{repo}" PR {pr:d}'),
    target_fixture="ledger_entries",
)
def two_ledger_entries(repo: str, pr: int):
    from voyager.bots.clearance.models import LedgerAction, LedgerEntry

    base = {
        "ts": _ts(),
        "repo": repo,
        "pr": pr,
        "head_sha": "abc",
        "actor": "frankyxhl",
        "authorized_by": "maintainer",
        "reason": "CI green",
    }
    return [
        LedgerEntry(action=LedgerAction.SUBMIT_REVIEW_APPROVE, **base),
        LedgerEntry(action=LedgerAction.EDIT_PR_BODY_CHECK_BOXES, **base),
    ]


@given(
    parsers.parse('a raw ledger JSON line with extra fields for "{repo}" PR {pr:d}'),
    target_fixture="raw_ledger_info",
)
def raw_ledger_json(repo: str, pr: int) -> dict:
    return {
        "repo": repo,
        "pr": pr,
        "json": (
            '{"ts":"2026-05-08T00:57:42Z","repo":"'
            + repo
            + '","pr":'
            + str(pr)
            + ',"head_sha":"abc",'
            '"action":"submit_review_approve","actor":"frankyxhl",'
            '"authorized_by":"maintainer","reason":"r","boxes_flipped":["A12"]}\n'
        ),
    }


# ---------------------------------------------------------------------------
# Ledger — when
# ---------------------------------------------------------------------------


@when("both ledger entries are appended", target_fixture="ledger_result")
def append_two_ledger_entries(store, ledger_entries):
    for e in ledger_entries:
        store.append_ledger(e)
    return store.read_ledger(ledger_entries[0].repo, ledger_entries[0].pr)


@when("the ledger is read for an unknown repo", target_fixture="ledger_result")
def read_unknown_ledger(store):
    return store.read_ledger("unknown/repo", 1)


@when(
    parsers.parse('the ledger is read for "{repo}" PR {pr:d}'),
    target_fixture="ledger_result",
)
def read_specific_ledger(store, raw_ledger_info, repo: str, pr: int):
    ledger_path = store._ledger_path(repo, pr)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(raw_ledger_info["json"])
    return store.read_ledger(repo, pr)


# ---------------------------------------------------------------------------
# Ledger — then
# ---------------------------------------------------------------------------


@then(parsers.parse('the ledger has {n:d} entries for "{repo}" PR {pr:d}'))
def ledger_entry_count(ledger_result, n: int, repo: str, pr: int) -> None:
    assert len(ledger_result) == n


@then("the ledger list is empty")
def ledger_empty(ledger_result) -> None:
    assert ledger_result == []


@then("the ledger entry has the extra field preserved")
def ledger_extra_preserved(ledger_result) -> None:
    assert len(ledger_result) == 1
    assert getattr(ledger_result[0], "boxes_flipped", None) == ["A12"]


# ---------------------------------------------------------------------------
# Box misses — given
# ---------------------------------------------------------------------------


@given(parsers.parse('a BoxMiss for repo "{repo}" PR {pr:d}'), target_fixture="box_miss")
def box_miss_fixture(repo: str, pr: int):
    from voyager.bots.clearance.models import BoxMiss

    return BoxMiss(ts=_ts(), repo=repo, pr=pr, head_sha="abc", box_text="x", reason="r")


@given('box misses for "owner/repo" and "other/repo"', target_fixture="miss_pair")
def two_repo_box_misses():
    from voyager.bots.clearance.models import BoxMiss

    return [
        BoxMiss(ts=_ts(), repo=REPO, pr=1, head_sha="abc", box_text="x", reason="r"),
        BoxMiss(ts=_ts(), repo="other/repo", pr=2, head_sha="abc", box_text="y", reason="r"),
    ]


# ---------------------------------------------------------------------------
# Box misses — when
# ---------------------------------------------------------------------------


@when("the box miss is appended and read back", target_fixture="miss_result")
def append_and_read_box_miss(store, box_miss):
    store.append_box_miss(box_miss)
    return list(store.read_box_misses(box_miss.repo))


@when('box misses are read for "owner/repo"', target_fixture="miss_result")
def read_box_misses_for_repo(store, miss_pair):
    for m in miss_pair:
        store.append_box_miss(m)
    return list(store.read_box_misses(REPO))


@when('box misses are read for "never/ran"', target_fixture="miss_result")
def read_box_misses_missing(store):
    return list(store.read_box_misses("never/ran"))


# ---------------------------------------------------------------------------
# Box misses — then
# ---------------------------------------------------------------------------


@then(parsers.parse('exactly {n:d} box miss is returned for "{repo}"'))
def box_miss_count(miss_result, n: int, repo: str) -> None:
    assert len(miss_result) == n


@then('only box misses for "owner/repo" are returned')
def only_owner_repo_misses(miss_result) -> None:
    assert all(m.repo == REPO for m in miss_result)
    assert len(miss_result) == 1


@then("the box miss list is empty")
def box_miss_empty(miss_result) -> None:
    assert miss_result == []


# ---------------------------------------------------------------------------
# Path layout
# ---------------------------------------------------------------------------


@given(
    'a PollRecord and ThreadSnapshot for "owner/repo" PR 49',
    target_fixture="layout_fixtures",
)
def layout_poll_and_snapshot() -> dict:
    return {
        "poll": _make_poll(),
        "snapshot": _make_snapshot(),
    }


@when("both are written", target_fixture="pr_dir")
def write_poll_and_snapshot(store, layout_fixtures) -> Path:
    store.append_poll(layout_fixtures["poll"])
    store.write_thread(layout_fixtures["snapshot"])
    return store._pr_dir(REPO, PR)


@then("the PR directory contains polls.jsonl and a thread file")
def pr_dir_has_files(pr_dir: Path) -> None:
    assert (pr_dir / "polls.jsonl").exists()
    assert (pr_dir / "threads" / f"{THREAD_ID}.jsonl").exists()
