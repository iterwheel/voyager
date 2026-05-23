"""Cross-test for Assembly job contract — spec vs implementation verification.

Verifies FORBIDDEN_OPERATIONS matches VOY-1805 §5 Deny column verbatim,
and VERIFICATION_COMMANDS includes the three commands from CHG-1817 D9.
"""

from __future__ import annotations

from voyager.bots.assembly.constants import FORBIDDEN_OPERATIONS, VERIFICATION_COMMANDS
from voyager.bots.assembly.job_contract import AssemblyJobContract, build_job_contract

# ---------------------------------------------------------------------------
# FORBIDDEN_OPERATIONS — exact match with VOY-1805 §5 Deny column
# ---------------------------------------------------------------------------

# The Deny column from VOY-1805 §5 (read directly from the SOP):
#   - Merge pull requests
#   - Approve its own pull requests
#   - Resolve review threads as a reviewer
#   - Apply `clearance-4-ready-for-merge` or `countdown-go` labels
#   - Modify branch protection rules
#   - Close issues directly without a linked PR
#   - Override Static Fire, Clearance, or Countdown verdicts

_EXPECTED_FORBIDDEN = (
    "Merge pull requests",
    "Approve its own pull requests",
    "Resolve review threads as a reviewer",
    "Apply `clearance-4-ready-for-merge` or `countdown-go` labels",
    "Modify branch protection rules",
    "Close issues directly without a linked PR",
    "Override Static Fire, Clearance, or Countdown verdicts",
)


class TestForbiddenOperationsMatchVOY1805:
    def test_count_matches(self) -> None:
        assert len(FORBIDDEN_OPERATIONS) == len(_EXPECTED_FORBIDDEN)

    def test_exact_match_element_by_element(self) -> None:
        for i, (actual, expected) in enumerate(
            zip(FORBIDDEN_OPERATIONS, _EXPECTED_FORBIDDEN, strict=True)
        ):
            assert actual == expected, f"Element {i}: {actual!r} != {expected!r}"

    def test_order_preserved(self) -> None:
        assert list(FORBIDDEN_OPERATIONS) == list(_EXPECTED_FORBIDDEN)


class TestForbiddenOperationsAreTuples:
    def test_is_tuple(self) -> None:
        assert isinstance(FORBIDDEN_OPERATIONS, tuple)

    def test_is_immutable(self) -> None:
        with __import__("pytest").raises(TypeError):
            FORBIDDEN_OPERATIONS[0] = "changed"  # type: ignore[index]


# ---------------------------------------------------------------------------
# VERIFICATION_COMMANDS — D9: pytest, ruff check, mypy voyager
# ---------------------------------------------------------------------------

_EXPECTED_VERIFICATION = (
    "pytest tests/",
    "ruff check .",
    "mypy voyager",
)


class TestVerificationCommandsMatchD9:
    def test_count_matches(self) -> None:
        assert len(VERIFICATION_COMMANDS) == len(_EXPECTED_VERIFICATION)

    def test_exact_match(self) -> None:
        assert set(VERIFICATION_COMMANDS) == set(_EXPECTED_VERIFICATION)

    def test_pytest_present(self) -> None:
        assert "pytest tests/" in VERIFICATION_COMMANDS

    def test_ruff_present(self) -> None:
        assert "ruff check ." in VERIFICATION_COMMANDS

    def test_mypy_present(self) -> None:
        assert "mypy voyager" in VERIFICATION_COMMANDS


# ---------------------------------------------------------------------------
# AssemblyJobContract dataclass shape
# ---------------------------------------------------------------------------


class TestAssemblyJobContractDataclass:
    def test_frozen(self) -> None:
        contract = AssemblyJobContract(
            repository="o/r",
            issue_number=1,
            issue_url="https://github.com/o/r/issues/1",
            issue_title="Test",
            issue_body="body",
            branch_name="1-test",
            base_branch="main",
            task_summary="summary",
            acceptance_criteria=["ac1"],
            forbidden_operations=(),
            verification_commands=(),
            delivery_id="d1",
            requested_at="2026-01-01T00:00:00Z",
        )
        with __import__("pytest").raises(__import__("dataclasses").FrozenInstanceError):
            contract.issue_number = 2  # type: ignore[misc]

    def test_to_dict_includes_all_fields(self) -> None:
        contract = AssemblyJobContract(
            repository="o/r",
            issue_number=1,
            issue_url="u",
            issue_title="t",
            issue_body="b",
            branch_name="1-t",
            base_branch="main",
            task_summary="s",
            acceptance_criteria=["ac1"],
            forbidden_operations=("Merge pull requests",),
            verification_commands=("pytest tests/",),
            delivery_id="d1",
            requested_at="2026-01-01T00:00:00Z",
        )
        d = contract.to_dict()
        assert d["repository"] == "o/r"
        assert d["issue_number"] == 1
        assert d["forbidden_operations"] == ["Merge pull requests"]
        assert d["verification_commands"] == ["pytest tests/"]
        assert isinstance(d["forbidden_operations"], list)
        assert isinstance(d["verification_commands"], list)


# ---------------------------------------------------------------------------
# build_job_contract integration
# ---------------------------------------------------------------------------


class TestBuildJobContract:
    def test_basic_contract(self) -> None:
        contract = build_job_contract(
            issue={
                "number": 42,
                "title": "Add feature X",
                "body": "## Problem / Goal\nBuild X\n\n## Acceptance Criteria\n- It works\n",
                "html_url": "https://github.com/o/r/issues/42",
            },
            repository="o/r",
            branch_name="42-add-feature-x",
            delivery_id="abc123",
        )
        assert contract.issue_number == 42
        assert contract.repository == "o/r"
        assert contract.branch_name == "42-add-feature-x"
        assert contract.base_branch == "main"
        assert contract.delivery_id == "abc123"
        assert contract.forbidden_operations == FORBIDDEN_OPERATIONS
        assert contract.verification_commands == VERIFICATION_COMMANDS

    def test_forbidden_ops_are_canonical_constants(self) -> None:
        contract = build_job_contract(
            issue={"number": 1, "title": "T", "body": ""},
            repository="o/r",
            branch_name="1-t",
            delivery_id="d",
        )
        assert contract.forbidden_operations is FORBIDDEN_OPERATIONS

    def test_verification_commands_are_canonical_constants(self) -> None:
        contract = build_job_contract(
            issue={"number": 1, "title": "T", "body": ""},
            repository="o/r",
            branch_name="1-t",
            delivery_id="d",
        )
        assert contract.verification_commands is VERIFICATION_COMMANDS

    def test_acceptance_criteria_title_fallback(self) -> None:
        contract = build_job_contract(
            issue={"number": 1, "title": "My feature", "body": "no sections here"},
            repository="o/r",
            branch_name="1-my-feature",
            delivery_id="d",
        )
        assert contract.acceptance_criteria_source == "title_fallback"
        assert contract.acceptance_criteria == ["My feature"]

    def test_task_summary_title_fallback(self) -> None:
        contract = build_job_contract(
            issue={"number": 1, "title": "My feature", "body": "no sections here"},
            repository="o/r",
            branch_name="1-my-feature",
            delivery_id="d",
        )
        assert contract.task_summary_source == "title_fallback"
        assert contract.task_summary == "My feature"
