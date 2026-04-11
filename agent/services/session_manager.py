from __future__ import annotations

import asyncio


class SessionManager:
    def __init__(self) -> None:
        self._session_ids: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def get(self, conversation_id: str | None) -> str | None:
        if not conversation_id:
            return None
        async with self._lock:
            return self._session_ids.get(conversation_id)

    async def set(self, conversation_id: str | None, session_id: str) -> None:
        if not conversation_id:
            return
        async with self._lock:
            self._session_ids[conversation_id] = session_id

    async def reset(self, conversation_id: str | None = None) -> bool:
        async with self._lock:
            if conversation_id:
                return self._session_ids.pop(conversation_id, None) is not None

            had_any = bool(self._session_ids)
            self._session_ids.clear()
            return had_any
