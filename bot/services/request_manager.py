from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(slots=True)
class RunningRequest:
    request_id: str
    task: asyncio.Task[object]


class RequestManager:
    def __init__(self) -> None:
        self._running_by_chat: dict[int, RunningRequest] = {}
        self._lock = asyncio.Lock()

    async def start(self, chat_id: int, request_id: str, task: asyncio.Task[object]) -> bool:
        async with self._lock:
            if chat_id in self._running_by_chat:
                return False
            self._running_by_chat[chat_id] = RunningRequest(request_id=request_id, task=task)
            return True

    async def get(self, chat_id: int) -> RunningRequest | None:
        async with self._lock:
            return self._running_by_chat.get(chat_id)

    async def finish(self, chat_id: int, request_id: str) -> None:
        async with self._lock:
            current = self._running_by_chat.get(chat_id)
            if current and current.request_id == request_id:
                self._running_by_chat.pop(chat_id, None)

    async def cancel(self, chat_id: int) -> str | None:
        async with self._lock:
            current = self._running_by_chat.get(chat_id)
            if current is None:
                return None
            current.task.cancel()
            return current.request_id
