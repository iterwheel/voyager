from __future__ import annotations

import json
from pathlib import Path

import httpx

from voyager.llm.deepseek import Message, SyncDeepSeekClient

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "llm"


def test_sync_deepseek_client_complete_uses_sync_http_client() -> None:
    captured: list[httpx.Request] = []
    fixture = json.loads((FIXTURES_DIR / "deepseek_thinking_enabled.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            status_code=200,
            headers={"Content-Type": "application/json"},
            json=fixture,
        )

    transport = httpx.MockTransport(handler)

    class _PatchedClient(SyncDeepSeekClient):
        def _sync_client(self) -> httpx.Client:
            if self._client is None or self._client.is_closed:
                self._client = httpx.Client(transport=transport, timeout=10)
            return self._client

    client = _PatchedClient(api_key="sk-test-key", model="deepseek-v4-pro")
    turn = client.complete([Message(role="user", content="What is the capital of France?")])

    assert turn.content == "The capital of France is Paris."
    assert turn.reasoning_content
    body = json.loads(captured[0].content)
    assert body["model"] == "deepseek-v4-pro"
    assert body["thinking"] == {"type": "enabled"}

    client.close()
    assert client._client is None
