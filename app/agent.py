import json

from .conversation import trim_to_budget
from .openrouter import OpenRouterError, stream_chat
from .prompts import SUBAGENT_PROMPTS
from .tools import build_toolset, execute_tool


class Ctx:
    """Shared execution context passed to tools and loops."""

    def __init__(self, config, shell, conversation, log, session, cancel_event,
                 bot=None, chat_id=None):
        self.config = config
        self.shell = shell
        self.conversation = conversation
        self.log = log
        self.session = session
        self.cancel_event = cancel_event
        self.bot = bot          # aiogram Bot, for sending files back to the user
        self.chat_id = chat_id  # chat to deliver files to
        self.run_subagent = None  # assigned after construction


def _parse_args(tc):
    try:
        return json.loads(tc["function"]["arguments"] or "{}")
    except Exception:
        return {}


async def run_agent(ctx, system_prompt):
    """Main agent loop. Returns the final answer string."""
    log = ctx.log
    while True:
        if ctx.cancel_event.is_set():
            return "⏹️ Cancelled."

        messages = ctx.conversation.build(system_prompt)
        log.start_turn()
        try:
            assistant = await stream_chat(
                ctx.session, ctx.config, ctx.config.model_main, messages,
                tools=build_toolset("main"), on_text=log.append_text,
            )
        except OpenRouterError as e:
            return f"⚠️ Model error: {e}"

        ctx.conversation.add_assistant(assistant)
        tool_calls = assistant.get("tool_calls")
        if not tool_calls:
            return log.take_final()

        log.commit_turn_narration()
        extra_messages = []
        for tc in tool_calls:
            if ctx.cancel_event.is_set():
                # Still answer the tool call so history stays valid.
                ctx.conversation.add_tool_result(tc["id"], "Cancelled by user.")
                continue
            name = tc["function"]["name"]
            args = _parse_args(tc)
            log.tool_call(name, args)
            result, extra = await execute_tool(name, args, ctx)
            log.tool_result(name, result)
            ctx.conversation.add_tool_result(tc["id"], result)
            extra_messages.extend(extra)

        # Image (and other) follow-up messages go AFTER all tool results.
        for m in extra_messages:
            ctx.conversation.add_message(m)


async def run_subagent(ctx, subagent_type, prompt):
    """Run a subagent to completion and return its final message. No recursion."""
    cfg = ctx.config
    model = {
        "explore": cfg.model_explore,
        "general": cfg.model_general,
        "web": cfg.model_web,
    }[subagent_type]
    tools = build_toolset(subagent_type)
    plugins = [{"id": "web"}] if subagent_type == "web" else None
    log = ctx.log

    sub_msgs = [
        {"role": "system", "content": SUBAGENT_PROMPTS[subagent_type]},
        {"role": "user", "content": prompt},
    ]
    log.note(f"🛰️ [{subagent_type}] subagent started")

    while True:
        if ctx.cancel_event.is_set():
            return "Cancelled by user."
        sub_msgs = trim_to_budget(sub_msgs, cfg.token_limit, keep_head=2)
        try:
            assistant = await stream_chat(
                ctx.session, cfg, model, sub_msgs, tools=tools, plugins=plugins,
                on_text=log.append_text,
            )
        except OpenRouterError as e:
            log.note(f"🛰️ [{subagent_type}] error")
            return f"[{subagent_type} subagent error] {e}"

        sub_msgs.append(assistant)
        tool_calls = assistant.get("tool_calls")
        if not tool_calls:
            log.note(f"🛰️ [{subagent_type}] done")
            return assistant.get("content") or "(no result)"

        extra = []
        for tc in tool_calls:
            name = tc["function"]["name"]
            args = _parse_args(tc)
            log.tool_call(name, args, prefix=f"[{subagent_type}] ")
            result, ex = await execute_tool(name, args, ctx)
            log.tool_result(name, result, prefix=f"[{subagent_type}] ")
            sub_msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
            extra.extend(ex)
        sub_msgs.extend(extra)
