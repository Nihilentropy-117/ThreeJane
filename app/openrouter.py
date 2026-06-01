import json

import aiohttp

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterError(Exception):
    pass


async def stream_chat(session, config, model, messages, tools=None, plugins=None,
                      on_text=None, timeout=600):
    """Stream a chat completion. Calls on_text(delta) for each text delta and returns
    the fully assembled assistant message dict {role, content, tool_calls?}."""
    headers = {
        "Authorization": f"Bearer {config.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": config.referer,
        "X-Title": config.title,
    }
    body = {"model": model, "messages": messages, "stream": True}
    if tools:
        body["tools"] = tools
    if plugins:
        body["plugins"] = plugins

    text_parts = []
    tool_calls = {}

    async with session.post(
        OPENROUTER_URL, headers=headers, json=body,
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as resp:
        if resp.status != 200:
            detail = await resp.text()
            raise OpenRouterError(f"HTTP {resp.status}: {detail[:600]}")

        async for raw in resp.content:
            line = raw.decode("utf-8", "replace").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}

            if delta.get("content"):
                text_parts.append(delta["content"])
                if on_text:
                    on_text(delta["content"])

            for tcd in delta.get("tool_calls") or []:
                idx = tcd.get("index", 0)
                slot = tool_calls.setdefault(
                    idx, {"id": None, "type": "function",
                          "function": {"name": "", "arguments": ""}},
                )
                if tcd.get("id"):
                    slot["id"] = tcd["id"]
                fn = tcd.get("function") or {}
                if fn.get("name"):
                    slot["function"]["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["function"]["arguments"] += fn["arguments"]

    msg = {"role": "assistant"}
    content = "".join(text_parts)
    msg["content"] = content if content else None
    if tool_calls:
        ordered = [tool_calls[i] for i in sorted(tool_calls)]
        for j, tc in enumerate(ordered):
            if not tc["id"]:
                tc["id"] = f"call_{j}"
        msg["tool_calls"] = ordered
    return msg
