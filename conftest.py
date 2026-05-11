"""Root conftest: establish a persistent event loop for sync BDD step functions.

pytest-bdd step functions are synchronous and use asyncio.get_event_loop().run_until_complete().
In Python 3.12+, get_event_loop() raises RuntimeError when no loop is set in the current
thread. This fixture sets one for the duration of the session.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(scope="session", autouse=True)
def _session_event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()
