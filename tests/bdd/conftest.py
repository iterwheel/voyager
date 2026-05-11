"""Shared BDD fixtures — webhook payloads, LLM responses."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def webhook_fixture():
    """Load a webhook payload fixture by name (without .json suffix)."""

    def _load(name: str) -> dict:
        path = FIXTURES_DIR / "webhooks" / f"{name}.json"
        return json.loads(path.read_text())

    return _load


@pytest.fixture
def llm_response_fixture():
    """Load an LLM response fixture by name (without .json suffix)."""

    def _load(name: str) -> dict:
        path = FIXTURES_DIR / "llm" / f"{name}.json"
        return json.loads(path.read_text())

    return _load
