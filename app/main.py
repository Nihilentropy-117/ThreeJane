import asyncio
import datetime
import signal
import time

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.filters import Command
from aiogram.types import Message

from .actions import build_index, scan_actions
from .agent import Ctx, run_agent, run_subagent
from .config import load_config
from .conversation import Conversation
from .prompts import build_system_prompt
from .shell import PersistentShell
from .telegram_ui import ThinkingLog, ThinkingRenderer, send_result

config = load_config()

state = {
    "conversation": Conversation(config.token_limit),
    "shell": PersistentShell(config.workspace_dir),
    "busy": False,
    "cancel": asyncio.Event(),
    "current_task": None,
    "http": None,  # aiohttp.ClientSession for OpenRouter; created in main()
}

api_server = TelegramAPIServer.from_base(config.api_url, is_local=True)
session = AiohttpSession(api=api_server)
bot = Bot(token=config.bot_token, session=session)
dp = Dispatcher()


def authorized(m: Message) -> bool:
    return bool(m.from_user) and m.from_user.id == config.allowed_user_id


def _reset_shell():
    state["shell"].close()
    state["shell"] = PersistentShell(config.workspace_dir)


# --- commands --------------------------------------------------------------

@dp.message(Command("start"))
async def cmd_start(m: Message):
    if not authorized(m):
        return
    await m.answer("3Jane online. Send a task, or a file with a caption. /help for commands.")


@dp.message(Command("help"))
async def cmd_help(m: Message):
    if not authorized(m):
        return
    await m.answer(
        "/new — clear memory and reset the shell\n"
        "/cancel — stop the current task\n"
        "/status — show current state\n\n"
        "Send text or a file to start a task."
    )


@dp.message(Command("new"))
async def cmd_new(m: Message):
    if not authorized(m):
        return
    state["cancel"].set()
    if state["current_task"]:
        try:
            await state["current_task"]
        except Exception:
            pass
    _reset_shell()
    state["conversation"].clear()
    state["cancel"] = asyncio.Event()
    state["busy"] = False
    await m.answer("🧹 New session. Memory cleared, shell reset.")


@dp.message(Command("cancel"))
async def cmd_cancel(m: Message):
    if not authorized(m):
        return
    if state["busy"]:
        state["cancel"].set()
        await m.answer("⏹️ Cancelling…")
    else:
        await m.answer("Nothing running.")


@dp.message(Command("status"))
async def cmd_status(m: Message):
    if not authorized(m):
        return
    conv = state["conversation"]
    idle = int(time.time() - conv.last_activity)
    await m.answer(
        f"model: {config.model_main}\n"
        f"busy: {state['busy']}\n"
        f"history msgs: {len(conv.history)}\n"
        f"idle: {idle}s\n"
        f"workspace: {config.workspace_dir}"
    )


# --- task handling ---------------------------------------------------------

async def handle_task(m: Message, user_text: str, file_paths):
    if state["busy"]:
        await m.answer("⏳ Busy with a task. Send /cancel to stop it.")
        return
    state["busy"] = True
    state["cancel"] = asyncio.Event()
    conv = state["conversation"]

    parts = []
    if user_text:
        parts.append(user_text)
    if file_paths:
        parts.append("Files provided (read with the read tool):\n" + "\n".join(file_paths))
    conv.add_user_text("\n\n".join(parts) if parts else "(empty message)")

    log = ThinkingLog()
    renderer = ThinkingRenderer(bot, m.chat.id, config.thinking_update_interval)
    renderer.attach(log)
    init = await bot.send_message(m.chat.id, "🧠 Thinking…")
    renderer.message_ids = [init.message_id]
    renderer.rendered = ["🧠 Thinking…"]
    renderer.start()

    actions = scan_actions(config.actions_dir)
    system_prompt = build_system_prompt(
        config, build_index(actions), datetime.date.today().strftime("%a %b %d %Y"),
    )
    ctx = Ctx(config, state["shell"], conv, log, state["http"], state["cancel"])
    ctx.run_subagent = lambda st, prompt: run_subagent(ctx, st, prompt)

    async def go():
        try:
            result = await run_agent(ctx, system_prompt)
        except Exception as e:
            import traceback
            result = f"⚠️ Internal error: {e}\n{traceback.format_exc()[-800:]}"
        await renderer.stop()
        await send_result(bot, m.chat.id, result)
        state["busy"] = False

    state["current_task"] = asyncio.create_task(go())


@dp.message(F.document | F.photo | F.video | F.audio | F.voice)
async def on_file(m: Message):
    if not authorized(m):
        return
    file_ids = []
    if m.document:
        file_ids.append(m.document.file_id)
    if m.photo:
        file_ids.append(m.photo[-1].file_id)  # largest size
    if m.video:
        file_ids.append(m.video.file_id)
    if m.audio:
        file_ids.append(m.audio.file_id)
    if m.voice:
        file_ids.append(m.voice.file_id)
    paths = []
    for fid in file_ids:
        f = await bot.get_file(fid)
        paths.append(f.file_path)  # local Bot API mode -> absolute path on shared volume
    await handle_task(m, m.caption or "", paths)


@dp.message(F.text & ~F.text.startswith("/"))
async def on_text(m: Message):
    if not authorized(m):
        return
    await handle_task(m, m.text, [])


# --- background + lifecycle ------------------------------------------------

async def inactivity_watch():
    while True:
        await asyncio.sleep(60)
        conv = state["conversation"]
        if (not state["busy"] and conv.history
                and time.time() - conv.last_activity > config.inactivity_timeout):
            _reset_shell()
            conv.clear()
            try:
                await bot.send_message(config.allowed_user_id, "🧹 Session cleared after 1h of inactivity.")
            except Exception:
                pass


async def main():
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    state["http"] = aiohttp.ClientSession()

    watcher = asyncio.create_task(inactivity_watch())
    polling = asyncio.create_task(
        dp.start_polling(bot, handle_signals=False, drop_pending_updates=True)
    )

    await asyncio.wait(
        {polling, asyncio.create_task(stop.wait())},
        return_when=asyncio.FIRST_COMPLETED,
    )

    # graceful, docker-aware shutdown
    state["cancel"].set()
    watcher.cancel()
    polling.cancel()
    for t in (watcher, polling):
        try:
            await t
        except Exception:
            pass
    try:
        await bot.send_message(config.allowed_user_id, "🔌 3Jane shutting down.")
    except Exception:
        pass
    if state["current_task"]:
        try:
            await asyncio.wait_for(state["current_task"], timeout=5)
        except Exception:
            pass
    state["shell"].close()
    if state["http"]:
        try:
            await state["http"].close()
        except Exception:
            pass
    try:
        await bot.session.close()
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
