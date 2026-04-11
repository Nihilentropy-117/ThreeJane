from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramAPIError

from handlers.commands import create_commands_router
from handlers.messages import create_messages_router
from middleware.auth import AuthMiddleware
from services.agent_client import AgentClient
from services.conversation import ConversationService
from services.request_manager import RequestManager

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


async def _logout_cloud_api(token: str) -> None:
    """Log the bot out of the cloud Bot API so it can bind to a local server.

    Telegram requires calling logOut against api.telegram.org before a bot token
    can receive updates from a self-hosted Bot API server. This is a one-time
    requirement per token; on subsequent runs the cloud API will return an error
    (e.g. because the token is already logged out or the 10-minute cooldown is
    active), which we log and ignore so startup stays idempotent.
    """
    cloud_session = AiohttpSession()
    cloud_bot = Bot(token=token, session=cloud_session)
    try:
        await cloud_bot.log_out()
        logger.info("Logged out of cloud Bot API; switching to local server.")
    except TelegramAPIError as exc:
        logger.info(
            "Cloud Bot API logOut skipped (%s); assuming token is already "
            "detached from the cloud server.",
            exc,
        )
    finally:
        await cloud_session.close()


async def main() -> None:
    token = _required_env("TELEGRAM_BOT_TOKEN")
    authorized_user_id = int(_required_env("AUTHORIZED_USER_ID"))

    bot_api_url = os.getenv("BOT_API_URL", "http://telegram-bot-api:8081")
    agent_url = os.getenv("AGENT_URL", "http://agent:8082")
    db_path = os.getenv("CONVERSATIONS_DB_PATH", "/data/conversations/conversations.db")

    conversation_service = ConversationService(db_path=db_path)
    await conversation_service.initialize()

    request_manager = RequestManager()
    agent_client = AgentClient(base_url=agent_url)
    await agent_client.start()

    await _logout_cloud_api(token)

    api_server = TelegramAPIServer.from_base(bot_api_url, is_local=True)
    session = AiohttpSession(api=api_server)
    bot = Bot(
        token=token,
        session=session,
        default=DefaultBotProperties(),
    )

    dispatcher = Dispatcher()
    dispatcher.message.middleware(AuthMiddleware(authorized_user_id=authorized_user_id))

    dispatcher.include_router(
        create_commands_router(
            conversation_service=conversation_service,
            agent_client=agent_client,
            request_manager=request_manager,
        )
    )
    dispatcher.include_router(
        create_messages_router(
            conversation_service=conversation_service,
            agent_client=agent_client,
            request_manager=request_manager,
        )
    )

    logger.info("Bot started. Polling updates from %s", bot_api_url)

    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await agent_client.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
