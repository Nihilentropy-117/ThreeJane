from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import aiosqlite


@dataclass(slots=True)
class ArchivedConversation:
    conversation_id: int
    created_at: str
    archived_at: str
    first_message: str | None


class ConversationService:
    def __init__(self, db_path: str) -> None:
        self._db_path = Path(db_path)
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    archived_at TEXT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
                )
                """
            )
            await db.commit()

        await self.get_active_conversation_id()

    async def get_active_conversation_id(self) -> int:
        async with self._lock:
            async with aiosqlite.connect(self._db_path) as db:
                cursor = await db.execute(
                    """
                    SELECT id
                    FROM conversations
                    WHERE archived_at IS NULL
                    ORDER BY id DESC
                    LIMIT 1
                    """
                )
                row = await cursor.fetchone()
                await cursor.close()

                if row:
                    return int(row[0])

                now = _utc_now()
                cursor = await db.execute(
                    "INSERT INTO conversations (created_at, archived_at) VALUES (?, NULL)",
                    (now,),
                )
                await db.commit()
                conversation_id = int(cursor.lastrowid)
                await cursor.close()
                return conversation_id

    async def archive_active_and_create_new(self) -> tuple[int | None, int]:
        async with self._lock:
            async with aiosqlite.connect(self._db_path) as db:
                cursor = await db.execute(
                    """
                    SELECT id
                    FROM conversations
                    WHERE archived_at IS NULL
                    ORDER BY id DESC
                    LIMIT 1
                    """
                )
                row = await cursor.fetchone()
                await cursor.close()

                archived_conversation_id: int | None = None
                if row:
                    archived_conversation_id = int(row[0])
                    await db.execute(
                        "UPDATE conversations SET archived_at = ? WHERE id = ?",
                        (_utc_now(), archived_conversation_id),
                    )

                created_at = _utc_now()
                cursor = await db.execute(
                    "INSERT INTO conversations (created_at, archived_at) VALUES (?, NULL)",
                    (created_at,),
                )
                await db.commit()
                new_conversation_id = int(cursor.lastrowid)
                await cursor.close()

                return archived_conversation_id, new_conversation_id

    async def add_message(self, conversation_id: int, role: str, content: str) -> None:
        async with self._lock:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    """
                    INSERT INTO messages (conversation_id, role, content, timestamp)
                    VALUES (?, ?, ?, ?)
                    """,
                    (conversation_id, role, content, _utc_now()),
                )
                await db.commit()

    async def get_history(self, conversation_id: int, limit: int = 200) -> list[dict[str, str]]:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT role, content
                FROM messages
                WHERE conversation_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            )
            rows = await cursor.fetchall()
            await cursor.close()

        rows.reverse()
        return [{"role": str(row[0]), "content": str(row[1])} for row in rows]

    async def list_archived_conversations(self, limit: int = 20) -> list[ArchivedConversation]:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT
                    c.id,
                    c.created_at,
                    c.archived_at,
                    (
                        SELECT m.content
                        FROM messages m
                        WHERE m.conversation_id = c.id
                        ORDER BY m.id ASC
                        LIMIT 1
                    ) AS first_message
                FROM conversations c
                WHERE c.archived_at IS NOT NULL
                ORDER BY c.archived_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            await cursor.close()

        archived: list[ArchivedConversation] = []
        for row in rows:
            archived.append(
                ArchivedConversation(
                    conversation_id=int(row[0]),
                    created_at=str(row[1]),
                    archived_at=str(row[2]),
                    first_message=str(row[3]) if row[3] is not None else None,
                )
            )
        return archived


async def format_archived_history(conversation_service: ConversationService, limit: int = 20) -> str:
    archived = await conversation_service.list_archived_conversations(limit=limit)
    if not archived:
        return "No archived conversations yet."

    lines: list[str] = ["Archived conversations:"]
    for item in archived:
        preview = _preview(item.first_message)
        lines.append(
            f"#{item.conversation_id} | created {item.created_at} | archived {item.archived_at} | {preview}"
        )
    return "\n".join(lines)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _preview(value: str | None, max_len: int = 80) -> str:
    if not value:
        return "(no messages)"
    one_line = " ".join(value.split())
    if len(one_line) <= max_len:
        return one_line
    return one_line[: max_len - 1] + "…"
