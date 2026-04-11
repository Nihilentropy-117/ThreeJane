from __future__ import annotations

import json
from typing import AsyncIterator

import aiohttp


class AgentClient:
    def __init__(self, base_url: str, timeout_seconds: int = 660) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def stream_prompt(
        self,
        *,
        prompt: str,
        conversation_history: list[dict[str, str]],
        conversation_id: str,
        request_id: str,
        session_id: str | None = None,
    ) -> AsyncIterator[dict[str, object]]:
        session = await self._ensure_session()
        payload = {
            "prompt": prompt,
            "conversation_history": conversation_history,
            "conversation_id": conversation_id,
            "request_id": request_id,
            "session_id": session_id,
        }

        async with session.post(
            f"{self._base_url}/prompt",
            json=payload,
            headers={"Accept": "text/event-stream"},
        ) as response:
            response.raise_for_status()
            async for event in _iter_sse(response):
                yield event

    async def reset(self, conversation_id: str | None = None) -> None:
        session = await self._ensure_session()
        payload: dict[str, object] = {}
        if conversation_id is not None:
            payload["conversation_id"] = conversation_id
        async with session.post(f"{self._base_url}/reset", json=payload) as response:
            response.raise_for_status()
            await response.read()

    async def cancel(self, request_id: str) -> bool:
        session = await self._ensure_session()
        async with session.post(f"{self._base_url}/cancel", json={"request_id": request_id}) as response:
            if response.status == 404:
                return False
            response.raise_for_status()
            await response.read()
            return True

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            await self.start()
        assert self._session is not None
        return self._session


async def _iter_sse(response: aiohttp.ClientResponse) -> AsyncIterator[dict[str, object]]:
    buffer = ""

    async for chunk in response.content.iter_chunked(2048):
        text = chunk.decode("utf-8", errors="replace")
        buffer += text

        while "\n\n" in buffer:
            raw_event, buffer = buffer.split("\n\n", 1)
            parsed = _parse_sse_event(raw_event)
            if parsed is not None:
                yield parsed

    if buffer.strip():
        parsed = _parse_sse_event(buffer)
        if parsed is not None:
            yield parsed


def _parse_sse_event(raw: str) -> dict[str, object] | None:
    event_name: str | None = None
    data_lines: list[str] = []

    for line in raw.splitlines():
        if not line or line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].strip())

    if event_name is None:
        return None

    payload: object = {}
    if data_lines:
        joined = "\n".join(data_lines)
        try:
            payload = json.loads(joined)
        except json.JSONDecodeError:
            payload = {"raw": joined}

    if not isinstance(payload, dict):
        payload = {"value": payload}

    return {
        "event": event_name,
        "data": payload,
    }
