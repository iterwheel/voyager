"""Unit tests for ``voyager.build_info`` (CHG-1820 Surface 11).

Both cases use ``monkeypatch.setitem(sys.modules, ...)`` to inject the
fake ``_build_info`` module, then ``importlib.reload`` to re-execute
the top-level import. An autouse fixture restores the module after every
test so subsequent tests see the on-disk state.
"""

from __future__ import annotations

import importlib
import sys
import types

import pytest


@pytest.fixture(autouse=True)
def _restore_build_info() -> None:
    yield
    sys.modules.pop("voyager._build_info", None)
    import voyager.build_info as bi

    importlib.reload(bi)


def test_build_info_falls_back_to_dev_when_generated_module_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "voyager._build_info", None)
    import voyager.build_info as bi

    importlib.reload(bi)
    from voyager import __version__ as VERSION  # noqa: N812

    assert bi.BUILD_COMMIT == "dev"
    assert bi.get_info() == {"version": VERSION, "build_commit": "dev"}


def test_build_info_reads_generated_module_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = types.SimpleNamespace(BUILD_COMMIT="abc1234")
    monkeypatch.setitem(sys.modules, "voyager._build_info", fake)
    import voyager.build_info as bi

    importlib.reload(bi)
    from voyager import __version__ as VERSION  # noqa: N812

    assert bi.BUILD_COMMIT == "abc1234"
    assert bi.get_info() == {"version": VERSION, "build_commit": "abc1234"}
