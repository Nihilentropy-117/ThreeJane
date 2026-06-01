import asyncio
import json

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import FSInputFile

THINKING_HEADER = "🧠 Thinking…"
CONT = "\n\n…(continued in next message)"
PAGE_LIMIT = 4000
RESULT_DISPLAY_CAP = 1500
CAPTION_LIMIT = 1024  # Telegram caption max length


# ---------------------------------------------------------------------------
# Thinking log: a single growing buffer rendered to the thinking message(s)
# ---------------------------------------------------------------------------

class ThinkingLog:
    def __init__(self):
        self.buf = ""
        self.mark = 0  # buffer position where the current model turn's text began

    def start_turn(self):
        self.mark = len(self.buf)

    def append_text(self, t, prefix=""):
        self.buf += t

    def _block(self, s):
        if self.buf and not self.buf.endswith("\n"):
            self.buf += "\n"
        self.buf += s
        if not self.buf.endswith("\n"):
            self.buf += "\n"

    def tool_call(self, name, args, prefix=""):
        disp = args.get("command", "") if name == "bash" else json.dumps(args, ensure_ascii=False)
        disp = disp.replace("\n", " ")
        if len(disp) > 300:
            disp = disp[:300] + " …"
        self._block(f"🔧 {prefix}{name}: {disp}")

    def tool_result(self, name, result, prefix=""):
        if name == "load_action":
            # The loaded action text is instructions fed into the model (an input),
            # not output worth showing. A "📂 Loaded action: …" note was already
            # emitted; skip dumping the full file contents to the chat.
            return
        r = result if isinstance(result, str) else str(result)
        if len(r) > RESULT_DISPLAY_CAP:
            r = r[:RESULT_DISPLAY_CAP] + f" …[+{len(result) - RESULT_DISPLAY_CAP} chars]"
        r = "\n".join("  " + ln for ln in r.splitlines()) or "  (no output)"
        self._block(f"↪ {prefix}result:\n{r}")

    def note(self, text):
        self._block(text)

    def commit_turn_narration(self):
        pass

    def take_final(self):
        """Slice out the final turn's text as the result, and replace it in the
        thinking buffer with a completion marker."""
        final = self.buf[self.mark:].strip()
        self.buf = self.buf[:self.mark].rstrip()
        self.buf += ("\n\n" if self.buf else "") + "✅ Done."
        return final if final else "(done)"


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def paginate(text, limit=PAGE_LIMIT):
    """Split text into pages <= limit chars; non-final pages end with a continued marker."""
    if len(text) <= limit:
        return [text]
    pages = []
    rest = text
    body_limit = limit - len(CONT)
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, body_limit)
        if cut < body_limit // 2:
            cut = body_limit
        pages.append(rest[:cut].rstrip() + CONT)
        rest = rest[cut:].lstrip("\n")
    pages.append(rest)
    return pages


# ---------------------------------------------------------------------------
# Safe send/edit wrappers
# ---------------------------------------------------------------------------

async def safe_send(bot, chat_id, text):
    try:
        return await bot.send_message(chat_id, text)
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)
        return await bot.send_message(chat_id, text)


async def send_document(bot, chat_id, path, caption=None):
    """Send a local file to the chat as a document. A fresh FSInputFile is built per
    attempt because the input stream is consumed on send."""
    if caption and len(caption) > CAPTION_LIMIT:
        caption = caption[:CAPTION_LIMIT - 1] + "…"
    try:
        return await bot.send_document(chat_id, FSInputFile(path), caption=caption)
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)
        return await bot.send_document(chat_id, FSInputFile(path), caption=caption)


async def safe_edit(bot, chat_id, message_id, text):
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id)
    except TelegramBadRequest:
        pass  # e.g. "message is not modified"
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id)
        except TelegramBadRequest:
            pass


# ---------------------------------------------------------------------------
# Throttled renderer for the thinking message chain
# ---------------------------------------------------------------------------

class ThinkingRenderer:
    def __init__(self, bot, chat_id, interval, header=THINKING_HEADER):
        self.bot = bot
        self.chat_id = chat_id
        self.interval = interval
        self.header = header
        self.message_ids = []
        self.rendered = []
        self._log = None
        self._task = None
        self._stop = False

    def attach(self, log):
        self._log = log

    def start(self):
        self._task = asyncio.create_task(self._loop())

    async def _loop(self):
        while not self._stop:
            await asyncio.sleep(self.interval)
            await self.flush()

    async def stop(self):
        self._stop = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        await self.flush()

    async def flush(self):
        text = (self.header + "\n\n" + self._log.buf).strip()
        pages = paginate(text)
        for i, page in enumerate(pages):
            if i < len(self.message_ids):
                if self.rendered[i] != page:
                    await safe_edit(self.bot, self.chat_id, self.message_ids[i], page)
                    self.rendered[i] = page
            else:
                msg = await safe_send(self.bot, self.chat_id, page)
                self.message_ids.append(msg.message_id)
                self.rendered.append(page)


# ---------------------------------------------------------------------------
# Final result (separate message chain, also paginated)
# ---------------------------------------------------------------------------

async def send_result(bot, chat_id, text):
    if not text or not text.strip():
        text = "(no output)"
    for page in paginate(text):
        try:
            await bot.send_message(chat_id, page, parse_mode="Markdown")
        except TelegramBadRequest:
            await bot.send_message(chat_id, page)  # fall back to plain text
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            await bot.send_message(chat_id, page)
