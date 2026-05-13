"""Step definitions for swm_diff_excerpt BDD scenarios."""

from __future__ import annotations

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("../features/swm_diff_excerpt.feature")

# ---------------------------------------------------------------------------
# Diff fixture constants
# ---------------------------------------------------------------------------

_SINGLE_HUNK_DIFF = """\
diff --git a/app.py b/app.py
index 111..222 100644
--- a/app.py
+++ b/app.py
@@ -1,3 +1,4 @@
 context line one
-removed line
+added line
+extra added
 context line two"""

_MULTI_HUNK_DIFF = """\
diff --git a/app.py b/app.py
index abc..def 100644
--- a/app.py
+++ b/app.py
@@ -3,4 +3,4 @@
 first context line
-removed first
+added first
 second context line
@@ -15,3 +15,3 @@
 third context line
-removed second
+added second
 fourth context line"""

_THREE_FILE_DIFF = """\
diff --git a/alpha.py b/alpha.py
index 000..111 100644
--- a/alpha.py
+++ b/alpha.py
@@ -1,2 +1,2 @@
-old alpha
+new alpha
diff --git a/beta.py b/beta.py
index 000..222 100644
--- a/beta.py
+++ b/beta.py
@@ -1,2 +1,2 @@
-old beta
+new beta
diff --git a/gamma.py b/gamma.py
index 000..333 100644
--- a/gamma.py
+++ b/gamma.py
@@ -1,2 +1,2 @@
-old gamma
+new gamma"""

_RENAMED_DIFF = """\
diff --git a/old.py b/new.py
similarity index 80%
rename from old.py
rename to new.py
--- a/old.py
+++ b/new.py
@@ -1,3 +1,3 @@
 ctx
-old impl
+new impl
 ctx"""

_HUGE_BODY = "\n".join(f" padding line {i}" for i in range(300))

_TRUNCATION_DIFF_TWO_HUNKS = (
    "diff --git a/app.py b/app.py\n"
    "index 111..222 100644\n"
    "--- a/app.py\n"
    "+++ b/app.py\n"
    "@@ -1,3 +1,4 @@\n"
    " ctx\n"
    "-old\n"
    "+new\n"
    " ctx\n"
    "@@ -200,3 +201,3 @@\n" + _HUGE_BODY
)

_TRUNCATION_DIFF_SINGLE_HUGE = (
    "diff --git a/app.py b/app.py\n"
    "index 111..222 100644\n"
    "--- a/app.py\n"
    "+++ b/app.py\n"
    "@@ -1,3 +1,4 @@\n" + _HUGE_BODY
)

# ---------------------------------------------------------------------------
# Per-scenario state fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def excerpt_ctx() -> dict:
    return {"diff_text": "", "path": "", "line": None, "max_chars": 20000, "result": None}


# ---------------------------------------------------------------------------
# Given steps
# ---------------------------------------------------------------------------


@given("an empty diff text", target_fixture="excerpt_ctx")
def given_empty_diff() -> dict:
    return {"diff_text": "", "path": "app.py", "line": 1, "max_chars": 20000, "result": None}


@given(
    parsers.parse('a single-file diff for "{filename}"'),
    target_fixture="excerpt_ctx",
)
def given_single_file_diff(filename: str) -> dict:
    diff = (
        f"diff --git a/{filename} b/{filename}\n"
        f"index 000..111 100644\n"
        f"--- a/{filename}\n"
        f"+++ b/{filename}\n"
        "@@ -1,2 +1,2 @@\n"
        "-old\n"
        "+new\n"
    )
    return {"diff_text": diff, "path": filename, "line": 1, "max_chars": 20000, "result": None}


@given(
    parsers.parse('a single-hunk diff for "app.py" covering new lines 1 to 4'),
    target_fixture="excerpt_ctx",
)
def given_single_hunk_diff_app_py() -> dict:
    return {
        "diff_text": _SINGLE_HUNK_DIFF,
        "path": "app.py",
        "line": None,
        "max_chars": 20000,
        "result": None,
    }


@given(
    parsers.parse(
        'a multi-hunk diff for "app.py" with hunk1 at new lines 3 to 7 and hunk2 at new lines 15 to 18'
    ),
    target_fixture="excerpt_ctx",
)
def given_multi_hunk_diff() -> dict:
    return {
        "diff_text": _MULTI_HUNK_DIFF,
        "path": "app.py",
        "line": None,
        "max_chars": 20000,
        "result": None,
    }


@given(
    parsers.parse('a three-file diff containing "alpha.py", "beta.py", and "gamma.py"'),
    target_fixture="excerpt_ctx",
)
def given_three_file_diff() -> dict:
    return {
        "diff_text": _THREE_FILE_DIFF,
        "path": "beta.py",
        "line": None,
        "max_chars": 20000,
        "result": None,
    }


@given(
    parsers.parse('a renamed-file diff from "old.py" to "new.py"'),
    target_fixture="excerpt_ctx",
)
def given_renamed_diff() -> dict:
    return {
        "diff_text": _RENAMED_DIFF,
        "path": "new.py",
        "line": None,
        "max_chars": 20000,
        "result": None,
    }


@given(
    parsers.parse(
        'a diff for "app.py" with a small matching hunk at line 2 and a huge distant hunk'
    ),
    target_fixture="excerpt_ctx",
)
def given_truncation_diff_two_hunks() -> dict:
    return {
        "diff_text": _TRUNCATION_DIFF_TWO_HUNKS,
        "path": "app.py",
        "line": None,
        "max_chars": 20000,
        "result": None,
    }


@given(
    parsers.parse('a diff for "app.py" with a single huge hunk'),
    target_fixture="excerpt_ctx",
)
def given_truncation_diff_single_huge() -> dict:
    return {
        "diff_text": _TRUNCATION_DIFF_SINGLE_HUGE,
        "path": "app.py",
        "line": None,
        "max_chars": 20000,
        "result": None,
    }


# ---------------------------------------------------------------------------
# When steps
# ---------------------------------------------------------------------------


@when(
    parsers.parse('extract_anchor_excerpt is called with path "app.py" and line 1'),
    target_fixture="excerpt_ctx",
)
def when_call_with_line_1(excerpt_ctx: dict) -> dict:
    from voyager.bots.clearance.diff_excerpt import extract_anchor_excerpt

    result = extract_anchor_excerpt(excerpt_ctx["diff_text"], path="app.py", line=1)
    return {**excerpt_ctx, "result": result}


@when(
    parsers.parse('extract_anchor_excerpt is called with path "app.py" and line 2'),
    target_fixture="excerpt_ctx",
)
def when_call_with_line_2(excerpt_ctx: dict) -> dict:
    from voyager.bots.clearance.diff_excerpt import extract_anchor_excerpt

    result = extract_anchor_excerpt(excerpt_ctx["diff_text"], path="app.py", line=2)
    return {**excerpt_ctx, "result": result}


@when(
    parsers.parse('extract_anchor_excerpt is called with path "app.py" and line 5'),
    target_fixture="excerpt_ctx",
)
def when_call_with_line_5(excerpt_ctx: dict) -> dict:
    from voyager.bots.clearance.diff_excerpt import extract_anchor_excerpt

    result = extract_anchor_excerpt(excerpt_ctx["diff_text"], path="app.py", line=5)
    return {**excerpt_ctx, "result": result}


@when(
    parsers.parse('extract_anchor_excerpt is called with path "app.py" and line 99'),
    target_fixture="excerpt_ctx",
)
def when_call_with_line_99(excerpt_ctx: dict) -> dict:
    from voyager.bots.clearance.diff_excerpt import extract_anchor_excerpt

    result = extract_anchor_excerpt(excerpt_ctx["diff_text"], path="app.py", line=99)
    return {**excerpt_ctx, "result": result}


@when(
    parsers.parse('extract_anchor_excerpt is called with path "app.py" and line None'),
    target_fixture="excerpt_ctx",
)
def when_call_with_line_none_app_py(excerpt_ctx: dict) -> dict:
    from voyager.bots.clearance.diff_excerpt import extract_anchor_excerpt

    result = extract_anchor_excerpt(excerpt_ctx["diff_text"], path="app.py", line=None)
    return {**excerpt_ctx, "result": result}


@when(
    parsers.parse('extract_anchor_excerpt is called with path "beta.py" and line 2'),
    target_fixture="excerpt_ctx",
)
def when_call_with_beta_py_line_2(excerpt_ctx: dict) -> dict:
    from voyager.bots.clearance.diff_excerpt import extract_anchor_excerpt

    result = extract_anchor_excerpt(excerpt_ctx["diff_text"], path="beta.py", line=2)
    return {**excerpt_ctx, "result": result}


@when(
    parsers.parse('extract_anchor_excerpt is called with path "new.py" and line 2'),
    target_fixture="excerpt_ctx",
)
def when_call_with_new_py_line_2(excerpt_ctx: dict) -> dict:
    from voyager.bots.clearance.diff_excerpt import extract_anchor_excerpt

    result = extract_anchor_excerpt(excerpt_ctx["diff_text"], path="new.py", line=2)
    return {**excerpt_ctx, "result": result}


@when(
    parsers.parse(
        'extract_anchor_excerpt is called with path "app.py" and line 2 and max_chars 200'
    ),
    target_fixture="excerpt_ctx",
)
def when_call_with_line_2_max_chars_200(excerpt_ctx: dict) -> dict:
    from voyager.bots.clearance.diff_excerpt import extract_anchor_excerpt

    result = extract_anchor_excerpt(excerpt_ctx["diff_text"], path="app.py", line=2, max_chars=200)
    return {**excerpt_ctx, "result": result}


# ---------------------------------------------------------------------------
# Then steps
# ---------------------------------------------------------------------------


@then(parsers.parse('the excerpt is ""'))
def then_excerpt_is_empty(excerpt_ctx: dict) -> None:
    assert excerpt_ctx["result"] == "", f"Expected empty string but got: {excerpt_ctx['result']!r}"


@then(parsers.parse('the excerpt contains "{text}"'))
def then_excerpt_contains(excerpt_ctx: dict, text: str) -> None:
    result = excerpt_ctx["result"]
    assert text in result, f"Expected {text!r} in excerpt:\n{result!r}"


@then(parsers.parse('the excerpt does not contain "{text}"'))
def then_excerpt_not_contains(excerpt_ctx: dict, text: str) -> None:
    result = excerpt_ctx["result"]
    assert text not in result, f"Expected {text!r} NOT in excerpt:\n{result!r}"
