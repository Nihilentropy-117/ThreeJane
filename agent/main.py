from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import suppress
from typing import Any

from aiohttp import web

from services.claude_runner import ClaudeRunner
from services.session_manager import SessionManager

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> web.Application:
    session_manager = SessionManager()
    claude_runner = ClaudeRunner(session_manager=session_manager)

    app = web.Application()
    app["session_manager"] = session_manager
    app["claude_runner"] = claude_runner
    app["active_requests"] = {}
    app["active_requests_lock"] = asyncio.Lock()

    app.router.add_get("/health", health_handler)
    app.router.add_post("/prompt", prompt_handler)
    app.router.add_post("/reset", reset_handler)
    app.router.add_post("/cancel", cancel_handler)

    return app


async def health_handler(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def prompt_handler(request: web.Request) -> web.StreamResponse:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        return web.json_response({"error": "Field 'prompt' is required"}, status=400)

    conversation_id = str(body.get("conversation_id") or "").strip() or None
    provided_session_id = str(body.get("session_id") or "").strip() or None
    request_id = str(body.get("request_id") or uuid.uuid4().hex)

    history_raw = body.get("conversation_history")
    conversation_history: list[dict[str, str]] = []
    if isinstance(history_raw, list):
        for item in history_raw:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user")
            content = str(item.get("content") or "")
            conversation_history.append({"role": role, "content": content})

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)

    active_requests: dict[str, asyncio.Task[Any]] = request.app["active_requests"]
    active_requests_lock: asyncio.Lock = request.app["active_requests_lock"]

    current_task = asyncio.current_task()
    if current_task is None:
        await _safe_sse_write(response, "error", {"error": "Internal request task missing"})
        with suppress(Exception):
            await response.write_eof()
        return response

    async with active_requests_lock:
        active_requests[request_id] = current_task

    claude_runner: ClaudeRunner = request.app["claude_runner"]

    try:
        async for event_name, payload in claude_runner.stream(
            prompt=prompt,
            conversation_history=conversation_history,
            conversation_id=conversation_id,
            provided_session_id=provided_session_id,
        ):
            await _safe_sse_write(response, event_name, payload)
    except asyncio.CancelledError:
        await _safe_sse_write(response, "error", {"error": "Request cancelled"})
    except Exception as exc:
        logger.exception("Prompt execution failed")
        await _safe_sse_write(response, "error", {"error": str(exc)})
    finally:
        async with active_requests_lock:
            active_requests.pop(request_id, None)

        with suppress(Exception):
            await response.write_eof()

    return response


async def reset_handler(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        body = {}

    conversation_id = str(body.get("conversation_id") or "").strip() or None
    session_manager: SessionManager = request.app["session_manager"]

    changed = await session_manager.reset(conversation_id)
    return web.json_response({"ok": True, "reset": changed, "conversation_id": conversation_id})


async def cancel_handler(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    request_id = str(body.get("request_id") or "").strip()
    if not request_id:
        return web.json_response({"error": "Field 'request_id' is required"}, status=400)

    active_requests: dict[str, asyncio.Task[Any]] = request.app["active_requests"]
    active_requests_lock: asyncio.Lock = request.app["active_requests_lock"]

    async with active_requests_lock:
        task = active_requests.get(request_id)
        if task is None:
            return web.json_response({"error": "Request not found"}, status=404)
        task.cancel()

    return web.json_response({"ok": True, "request_id": request_id})


async def _safe_sse_write(response: web.StreamResponse, event: str, payload: dict[str, Any]) -> None:
    data = _format_sse(event, payload)
    with suppress(ConnectionResetError, RuntimeError):
        await response.write(data)


def _format_sse(event: str, payload: dict[str, Any]) -> bytes:
    serialized = json.dumps(payload, ensure_ascii=False)
    return f"event: {event}\ndata: {serialized}\n\n".encode("utf-8")


def main() -> None:
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", "8082")))


if __name__ == "__main__":
    main()
