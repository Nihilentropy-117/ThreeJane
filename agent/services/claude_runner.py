from __future__ import annotations

import dataclasses
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Mapping

from claude_agent_sdk import ClaudeAgentOptions, query

from services.session_manager import SessionManager

OUTGOING_DIR = Path("/shared-files/outgoing")
SYSTEM_PROMPT_PATH = Path("/home/claude/.claude/CLAUDE.md")


@dataclass(slots=True)
class ParsedMessage:
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    session_id: str | None = None
    result_text: str | None = None
    total_cost_usd: float | None = None
    duration_ms: int | None = None


class ClaudeRunner:
    def __init__(self, session_manager: SessionManager) -> None:
        self._session_manager = session_manager

    async def stream(
        self,
        *,
        prompt: str,
        conversation_history: list[dict[str, str]],
        conversation_id: str | None,
        provided_session_id: str | None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        OUTGOING_DIR.mkdir(parents=True, exist_ok=True)
        outgoing_before = _snapshot_outgoing()

        previous_session_id = provided_session_id or await self._session_manager.get(conversation_id)

        prompt_for_query = _build_prompt(prompt, conversation_history, include_history=previous_session_id is None)
        options = _build_options(resume_session_id=previous_session_id)

        result_text: str | None = None
        total_cost_usd: float | None = None
        duration_ms: int | None = None
        observed_session_id: str | None = previous_session_id

        started_at = time.perf_counter()

        async for message in query(prompt=prompt_for_query, options=options):
            parsed = _parse_message(message)

            if parsed.session_id:
                observed_session_id = parsed.session_id

            for event_name, payload in parsed.events:
                yield event_name, payload

            if parsed.result_text is not None:
                result_text = parsed.result_text
            if parsed.total_cost_usd is not None:
                total_cost_usd = parsed.total_cost_usd
            if parsed.duration_ms is not None:
                duration_ms = parsed.duration_ms

        if observed_session_id and conversation_id:
            await self._session_manager.set(conversation_id, observed_session_id)

        if duration_ms is None:
            duration_ms = int((time.perf_counter() - started_at) * 1000)

        output_files = _diff_outgoing(outgoing_before)
        yield "result", {
            "text": (result_text or "").strip(),
            "session_id": observed_session_id,
            "cost_usd": total_cost_usd,
            "duration_ms": duration_ms,
            "output_files": output_files,
        }


def _build_options(resume_session_id: str | None) -> ClaudeAgentOptions:
    max_turns = int(os.getenv("AGENT_MAX_TURNS", "10"))
    allowed_tools = [
        tool.strip()
        for tool in os.getenv(
            "AGENT_ALLOWED_TOOLS",
            "Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,Agent",
        ).split(",")
        if tool.strip()
    ]

    option_kwargs: dict[str, Any] = {
        "model": os.getenv("AGENT_MODEL", "claude-sonnet-4-20250514"),
        "max_turns": max_turns,
        "permission_mode": os.getenv("AGENT_PERMISSION_MODE", "acceptEdits"),
        "cwd": "/workspace",
        "allowed_tools": allowed_tools,
    }

    system_prompt = _load_system_prompt()
    if system_prompt:
        option_kwargs["system_prompt"] = system_prompt

    if resume_session_id:
        option_kwargs["resume"] = resume_session_id

    for keys_to_drop in ((), ("system_prompt",), ("system_prompt", "cwd")):
        kwargs = dict(option_kwargs)
        for key in keys_to_drop:
            kwargs.pop(key, None)
        try:
            return ClaudeAgentOptions(**kwargs)
        except TypeError:
            continue

    return ClaudeAgentOptions(**option_kwargs)


def _load_system_prompt() -> str:
    if SYSTEM_PROMPT_PATH.exists():
        return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()

    return (
        "You are a sandboxed coding agent. Your working directory is /workspace. "
        "User-uploaded files are available under /shared-files/. "
        "Write files that should be sent back to the user under /shared-files/outgoing/."
    )


def _build_prompt(prompt: str, history: list[dict[str, str]], include_history: bool) -> str:
    if not include_history or not history:
        return prompt

    lines: list[str] = ["Conversation history:"]
    for item in history:
        role = item.get("role", "user").upper()
        content = item.get("content", "")
        lines.append(f"{role}: {content}")

    lines.extend(["", "Latest user message:", prompt])
    return "\n".join(lines)


def _parse_message(message: object) -> ParsedMessage:
    parsed = ParsedMessage()

    message_map = _to_mapping(message)
    message_type = _get_field(message, message_map, "type")

    if message_type == "system":
        subtype = _get_field(message, message_map, "subtype")
        if subtype == "init":
            parsed.session_id = (
                _get_field(message, message_map, "session_id")
                or _deep_get(message_map, "data", "session_id")
            )

    if message_type == "assistant":
        content_blocks = _get_field(message, message_map, "content")
        if isinstance(content_blocks, list):
            for block in content_blocks:
                block_map = _to_mapping(block)
                block_type = _get_field(block, block_map, "type")
                if block_type == "tool_use":
                    parsed.events.append(
                        (
                            "tool_use",
                            {
                                "tool": _get_field(block, block_map, "name") or "Tool",
                                "input": _get_field(block, block_map, "input") or {},
                            },
                        )
                    )
                elif block_type == "thinking":
                    text = _get_field(block, block_map, "text")
                    if isinstance(text, str):
                        parsed.events.append(("thinking", {"text": text}))
                elif block_type == "text":
                    text = _get_field(block, block_map, "text")
                    if isinstance(text, str):
                        parsed.events.append(("text", {"text": text}))

    if message_type == "result" or _get_field(message, message_map, "result") is not None:
        parsed.result_text = _coerce_result_text(message, message_map)
        parsed.total_cost_usd = _to_float(
            _get_field(message, message_map, "total_cost_usd")
            or _get_field(message, message_map, "cost_usd")
            or _deep_get(message_map, "cost", "total_cost_usd")
        )
        parsed.duration_ms = _to_int(_get_field(message, message_map, "duration_ms"))

    return parsed


def _coerce_result_text(message: object, message_map: Mapping[str, Any]) -> str:
    direct_result = _get_field(message, message_map, "result")
    if isinstance(direct_result, str):
        return direct_result

    text_result = _get_field(message, message_map, "text")
    if isinstance(text_result, str):
        return text_result

    if isinstance(direct_result, Mapping):
        value = direct_result.get("text") or direct_result.get("result")
        if isinstance(value, str):
            return value

    return ""


def _to_mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        if isinstance(dumped, Mapping):
            return dumped
    if hasattr(value, "dict"):
        dumped = value.dict()
        if isinstance(dumped, Mapping):
            return dumped
    if hasattr(value, "__dict__"):
        data = getattr(value, "__dict__", None)
        if isinstance(data, Mapping):
            return data
    return {}


def _get_field(value: object, value_map: Mapping[str, Any], key: str) -> Any:
    if key in value_map:
        return value_map.get(key)
    return getattr(value, key, None)


def _deep_get(source: Mapping[str, Any], *keys: str) -> Any:
    current: Any = source
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _snapshot_outgoing() -> dict[str, int]:
    state: dict[str, int] = {}
    if not OUTGOING_DIR.exists():
        return state

    for path in OUTGOING_DIR.rglob("*"):
        if path.is_file():
            state[str(path)] = path.stat().st_mtime_ns
    return state


def _diff_outgoing(before_state: dict[str, int]) -> list[str]:
    after_state = _snapshot_outgoing()
    changed = [
        path for path, mtime in after_state.items() if before_state.get(path) != mtime
    ]
    changed.sort(key=lambda path: after_state[path])
    return changed
