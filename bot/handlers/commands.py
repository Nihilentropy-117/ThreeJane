from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.agent_client import AgentClient
from services.conversation import ConversationService, format_archived_history
from services.request_manager import RequestManager
from services.telegram_helpers import split_for_telegram

logger = logging.getLogger(__name__)


def create_commands_router(
    *,
    conversation_service: ConversationService,
    agent_client: AgentClient,
    request_manager: RequestManager,
) -> Router:
    router = Router(name="commands")

    @router.message(Command("new"))
    async def new_conversation(message: Message) -> None:
        archived_id, new_id = await conversation_service.archive_active_and_create_new()
        if archived_id is not None:
            try:
                await agent_client.reset(conversation_id=str(archived_id))
            except Exception:
                logger.exception("Failed to reset agent session for archived conversation %s", archived_id)

        await message.answer(f"Started a new conversation (#{new_id}).")

    @router.message(Command("history"))
    async def history(message: Message) -> None:
        history_text = await format_archived_history(conversation_service, limit=30)
        for chunk in split_for_telegram(history_text):
            await message.answer(chunk)

    @router.message(Command("cancel"))
    async def cancel(message: Message) -> None:
        chat_id = message.chat.id
        request_id = await request_manager.cancel(chat_id)
        if request_id is None:
            await message.answer("No active request to cancel.")
            return

        try:
            await agent_client.cancel(request_id)
        except Exception:
            logger.exception("Failed to propagate cancel for request %s", request_id)

        await message.answer("Cancel requested.")

    return router
