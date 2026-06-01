import time

import tiktoken

_ENC = tiktoken.get_encoding("cl100k_base")


def count_tokens(messages) -> int:
    """Approximate token count for a list of chat messages."""
    total = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            total += len(_ENC.encode(c))
        elif isinstance(c, list):
            for blk in c:
                if blk.get("type") == "text":
                    total += len(_ENC.encode(blk.get("text", "")))
                elif blk.get("type") == "image_url":
                    total += 800  # rough fixed cost per image
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function", {})
            total += len(_ENC.encode(fn.get("name", "") + fn.get("arguments", "")))
        total += 4
    return total


def trim_to_budget(messages, limit, keep_head=1):
    """Drop oldest non-protected messages until under `limit`. The first
    `keep_head` messages (the system prompt, and for subagents the task) are never
    dropped, and a leading orphan tool message is never left at the trim point
    (which would be invalid)."""
    while count_tokens(messages) > limit and len(messages) > keep_head + 1:
        del messages[keep_head]
        while len(messages) > keep_head and messages[keep_head].get("role") == "tool":
            del messages[keep_head]
    return messages


class Conversation:
    def __init__(self, token_limit: int):
        self.token_limit = token_limit
        self.history = []          # chat messages, excluding the system prompt
        self.read_files = set()    # abspaths read this session (read-before-edit gate)
        self.last_activity = time.time()

    def touch(self):
        self.last_activity = time.time()

    def add_user_text(self, text: str):
        self.history.append({"role": "user", "content": text})
        self.touch()

    def add_message(self, msg: dict):
        self.history.append(msg)
        self.touch()

    def add_assistant(self, msg: dict):
        self.history.append(msg)
        self.touch()

    def add_tool_result(self, tool_call_id: str, content: str):
        self.history.append({"role": "tool", "tool_call_id": tool_call_id, "content": content})
        self.touch()

    def build(self, system_prompt: str):
        """Assemble the messages for an API call, applying the sliding window to a
        copy so stored history stays intact."""
        msgs = [{"role": "system", "content": system_prompt}] + list(self.history)
        return trim_to_budget(msgs, self.token_limit)

    def clear(self):
        self.history = []
        self.read_files = set()
        self.touch()
