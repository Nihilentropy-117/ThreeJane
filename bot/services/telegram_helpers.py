from __future__ import annotations

import asyncio
import re
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

TELEGRAM_LIMIT = 4096


def render_tool_line(tool_name: str, tool_input: dict[str, object] | None) -> str:
    tool_input = tool_input or {}

    if tool_name == "Bash":
        command = _compact(str(tool_input.get("command", "")), 220)
        return f"🔧 Bash: {command}" if command else "🔧 Bash"

    path_key_candidates = ("file_path", "path", "target_file", "directory")
    for key in path_key_candidates:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return f"🔧 {tool_name}: {value}"

    if "pattern" in tool_input and isinstance(tool_input["pattern"], str):
        return f"🔧 {tool_name}: pattern={_compact(tool_input['pattern'], 140)}"

    summary = _compact(str(tool_input), 220)
    return f"🔧 {tool_name}: {summary}" if summary != "{}" else f"🔧 {tool_name}"


def render_thinking_line(text: str) -> str:
    return f"💭 {_compact(' '.join(text.split()), 260)}"


def build_thinking_text(lines: list[str]) -> str:
    content = ["⏳ Thinking...", *lines]
    while True:
        joined = "\n".join(content)
        if len(joined) <= TELEGRAM_LIMIT:
            return joined
        if len(content) <= 1:
            return joined[-TELEGRAM_LIMIT:]
        del content[1]


async def safe_edit_message_text(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
) -> None:
    while True:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
            return
        except TelegramRetryAfter as exc:
            await asyncio.sleep(float(exc.retry_after))
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            raise


def split_for_telegram(text: str, max_len: int = TELEGRAM_LIMIT) -> list[str]:
    if not text:
        return [""]
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    paragraphs = text.split("\n\n")
    current = ""

    for paragraph in paragraphs:
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= max_len:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(paragraph) <= max_len:
            current = paragraph
            continue

        sentence_chunks = _split_by_sentences(paragraph, max_len=max_len)
        chunks.extend(sentence_chunks[:-1])
        current = sentence_chunks[-1] if sentence_chunks else ""

    if current:
        chunks.append(current)

    return chunks or [text[:max_len]]


def normalize_shared_path(file_path: str) -> str:
    if not file_path:
        return file_path
    if file_path.startswith("/shared-files/"):
        return file_path
    if file_path.startswith("/"):
        return file_path
    return str(Path("/shared-files") / file_path.lstrip("/"))


def is_safe_shared_file(path: str) -> bool:
    path_obj = Path(path)
    try:
        resolved = path_obj.resolve(strict=True)
    except FileNotFoundError:
        return False
    return str(resolved).startswith("/shared-files/") and resolved.is_file()


def _split_by_sentences(text: str, max_len: int) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text)
    if len(parts) == 1:
        return _hard_split(text, max_len)

    chunks: list[str] = []
    current = ""

    for part in parts:
        candidate = part if not current else f"{current} {part}"
        if len(candidate) <= max_len:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(part) <= max_len:
            current = part
        else:
            hard_chunks = _hard_split(part, max_len)
            chunks.extend(hard_chunks[:-1])
            current = hard_chunks[-1]

    if current:
        chunks.append(current)

    return chunks


def _hard_split(text: str, max_len: int) -> list[str]:
    return [text[idx : idx + max_len] for idx in range(0, len(text), max_len)]


def _compact(text: str, max_len: int) -> str:
    one_line = " ".join(text.split())
    if len(one_line) <= max_len:
        return one_line
    return one_line[: max_len - 1] + "…"
