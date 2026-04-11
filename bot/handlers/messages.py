from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Iterable

import aiohttp
from aiogram import Bot, F, Router
from aiogram.types import FSInputFile, Message

from services.agent_client import AgentClient
from services.conversation import ConversationService
from services.request_manager import RequestManager
from services.telegram_helpers import (
    build_thinking_text,
    is_safe_shared_file,
    normalize_shared_path,
    render_thinking_line,
    render_tool_line,
    safe_edit_message_text,
    split_for_telegram,
)

logger = logging.getLogger(__name__)


def create_messages_router(
    *,
    conversation_service: ConversationService,
    agent_client: AgentClient,
    request_manager: RequestManager,
) -> Router:
    router = Router(name="messages")

    @router.message(F.text | F.caption | F.document | F.photo | F.video | F.audio | F.voice)
    async def on_message(message: Message, bot: Bot) -> None:
        if message.text and message.text.strip().startswith("/"):
            return

        chat_id = message.chat.id
        request_id = uuid.uuid4().hex
        task = asyncio.current_task()
        if task is None:
            await message.answer("Internal error: missing request task.")
            return

        started = await request_manager.start(chat_id, request_id, task)
        if not started:
            await message.answer("A request is already running. Use /cancel to stop it first.")
            return

        thinking_message = await message.answer("⏳ Thinking...")
        thinking_lines: list[str] = []
        pending_thinking_update = False
        last_thinking_flush = time.monotonic()

        try:
            file_paths = await _collect_file_paths(message=message, bot=bot)
            prompt = _build_prompt(message=message, file_paths=file_paths)
            if not prompt:
                await safe_edit_message_text(
                    bot=bot,
                    chat_id=chat_id,
                    message_id=thinking_message.message_id,
                    text="No text or supported files were provided.",
                )
                return

            conversation_id = await conversation_service.get_active_conversation_id()
            history = await conversation_service.get_history(conversation_id)
            await conversation_service.add_message(conversation_id, "user", prompt)

            streamed_text_blocks: list[str] = []
            final_payload: dict[str, object] | None = None

            async for event in agent_client.stream_prompt(
                prompt=prompt,
                conversation_history=history,
                conversation_id=str(conversation_id),
                request_id=request_id,
            ):
                event_type = str(event.get("event", ""))
                data = event.get("data", {})
                if not isinstance(data, dict):
                    data = {}

                if event_type == "tool_use":
                    tool_name = str(data.get("tool", "Tool"))
                    tool_input = data.get("input")
                    thinking_lines.append(
                        render_tool_line(
                            tool_name=tool_name,
                            tool_input=tool_input if isinstance(tool_input, dict) else None,
                        )
                    )
                    pending_thinking_update = True
                elif event_type == "thinking":
                    text = data.get("text")
                    if isinstance(text, str) and text.strip():
                        thinking_lines.append(render_thinking_line(text))
                        pending_thinking_update = True
                elif event_type == "text":
                    text = data.get("text")
                    if isinstance(text, str) and text:
                        streamed_text_blocks.append(text)
                elif event_type == "result":
                    final_payload = data
                elif event_type == "error":
                    raise RuntimeError(str(data.get("error") or data.get("message") or "Unknown agent error"))

                now = time.monotonic()
                if pending_thinking_update and now - last_thinking_flush >= 1.0:
                    await safe_edit_message_text(
                        bot=bot,
                        chat_id=chat_id,
                        message_id=thinking_message.message_id,
                        text=build_thinking_text(thinking_lines),
                    )
                    pending_thinking_update = False
                    last_thinking_flush = now

            if pending_thinking_update:
                await safe_edit_message_text(
                    bot=bot,
                    chat_id=chat_id,
                    message_id=thinking_message.message_id,
                    text=build_thinking_text(thinking_lines),
                )

            if final_payload is None:
                final_text = "".join(streamed_text_blocks).strip() or "No final result was returned by the agent."
                output_files: list[str] = []
            else:
                final_text = str(final_payload.get("text") or "").strip()
                if not final_text:
                    final_text = "".join(streamed_text_blocks).strip() or "No final text returned."
                output_files = [
                    str(path)
                    for path in final_payload.get("output_files", [])
                    if isinstance(path, str)
                ]

            await conversation_service.add_message(conversation_id, "assistant", final_text)

            await safe_edit_message_text(
                bot=bot,
                chat_id=chat_id,
                message_id=thinking_message.message_id,
                text="✅ Complete",
            )

            for chunk in split_for_telegram(final_text):
                await message.answer(chunk)
                await asyncio.sleep(0.05)

            for output_path in _unique_paths(output_files):
                if not is_safe_shared_file(output_path):
                    continue
                try:
                    await message.answer_document(
                        FSInputFile(output_path, filename=Path(output_path).name)
                    )
                except Exception:
                    logger.exception("Failed to send output file %s", output_path)

        except asyncio.CancelledError:
            await safe_edit_message_text(
                bot=bot,
                chat_id=chat_id,
                message_id=thinking_message.message_id,
                text="⛔ Cancelled",
            )
        except aiohttp.ClientError:
            logger.exception("Agent service unavailable")
            await safe_edit_message_text(
                bot=bot,
                chat_id=chat_id,
                message_id=thinking_message.message_id,
                text="❌ Agent unavailable",
            )
        except Exception as exc:
            logger.exception("Unhandled error while processing message")
            await safe_edit_message_text(
                bot=bot,
                chat_id=chat_id,
                message_id=thinking_message.message_id,
                text=f"❌ {exc}",
            )
        finally:
            await request_manager.finish(chat_id, request_id)

    return router


async def _collect_file_paths(message: Message, bot: Bot) -> list[str]:
    file_ids: list[str] = []

    if message.document:
        file_ids.append(message.document.file_id)
    if message.photo:
        file_ids.append(message.photo[-1].file_id)
    if message.video:
        file_ids.append(message.video.file_id)
    if message.audio:
        file_ids.append(message.audio.file_id)
    if message.voice:
        file_ids.append(message.voice.file_id)

    resolved_paths: list[str] = []
    for file_id in file_ids:
        file = await bot.get_file(file_id)
        if file.file_path:
            resolved_paths.append(normalize_shared_path(file.file_path))

    return list(_unique_paths(resolved_paths))


def _build_prompt(message: Message, file_paths: list[str]) -> str:
    user_text = (message.text or message.caption or "").strip()

    file_prefix = "\n".join(f"[File: {path}]" for path in file_paths)

    if file_prefix and user_text:
        return f"{file_prefix}\n\n{user_text}"
    if file_prefix and not user_text:
        return f"{file_prefix}\n\nPlease analyze these files."
    return user_text


def _unique_paths(paths: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique
