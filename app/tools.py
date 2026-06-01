import asyncio
import base64
import json
import os
import subprocess
import tempfile

from .actions import load_action
from .telegram_ui import send_document

# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

SCHEMAS = {
    "bash": {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "Run a bash command in the persistent shell (cwd/env persist across calls). "
                "Use for running programs, git, builds, installs, and system tasks. Do NOT use it "
                "for reading/writing/searching files — use the dedicated tools."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command to run."},
                    "workdir": {"type": "string", "description": "Optional directory to run in."},
                    "timeout": {"type": "integer", "description": "Timeout in ms (default 120000)."},
                },
                "required": ["command"],
            },
        },
    },
    "read": {
        "type": "function",
        "function": {
            "name": "read",
            "description": (
                "Read a file or directory. Text returns numbered lines. Images and PDFs are read "
                "and made available to you. Required before editing a file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "offset": {"type": "integer", "description": "1-indexed start line."},
                    "limit": {"type": "integer", "description": "Max lines (default 2000)."},
                },
                "required": ["file_path"],
            },
        },
    },
    "write": {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Create or overwrite a file with the given content. Parent dirs are created.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["file_path", "content"],
            },
        },
    },
    "edit": {
        "type": "function",
        "function": {
            "name": "edit",
            "description": (
                "Exact string replacement in a file. old_string must appear exactly once unless "
                "replace_all is true. You must read the file first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        },
    },
    "glob": {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files by glob pattern (supports **), sorted by modification time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Base directory (default workspace)."},
                },
                "required": ["pattern"],
            },
        },
    },
    "grep": {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents with a regular expression. Returns file:line matches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Directory or file (default workspace)."},
                    "include": {"type": "string", "description": "Glob filter, e.g. *.py."},
                },
                "required": ["pattern"],
            },
        },
    },
    "task": {
        "type": "function",
        "function": {
            "name": "task",
            "description": (
                "Delegate a self-contained subtask to a cheaper subagent. Subagents start fresh, "
                "return one final message, and cannot spawn subagents. Provide a detailed prompt."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subagent_type": {"type": "string", "enum": ["explore", "general", "web"]},
                    "description": {"type": "string", "description": "Short label for the subtask."},
                    "prompt": {"type": "string", "description": "Full self-contained instructions."},
                },
                "required": ["subagent_type", "prompt"],
            },
        },
    },
    "send_file": {
        "type": "function",
        "function": {
            "name": "send_file",
            "description": (
                "Send a file from disk to the user in Telegram as a document, with an optional "
                "caption. Use this to deliver artifacts you produced or files the user asked for — "
                "images, PDFs, archives, code, anything. The file is sent as-is (not recompressed). "
                "This is for handing over files; your final text answer is still delivered separately."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path of the file to send."},
                    "caption": {"type": "string", "description": "Optional caption (max 1024 chars)."},
                },
                "required": ["file_path"],
            },
        },
    },
    "load_action": {
        "type": "function",
        "function": {
            "name": "load_action",
            "description": (
                "Load an action file's instructions into context by its key (see the action list "
                "in your system prompt). Dependencies in its AlsoLoad are loaded automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
}

SUBSETS = {
    "main": ["bash", "read", "write", "edit", "glob", "grep", "send_file", "task", "load_action"],
    "explore": ["read", "glob", "grep"],
    "general": ["bash", "read", "write", "edit", "glob", "grep"],
    "web": [],  # web subagent uses the OpenRouter web plugin, no function tools
}


def build_toolset(kind: str):
    return [SCHEMAS[n] for n in SUBSETS[kind]] or None


# ---------------------------------------------------------------------------
# Output truncation
# ---------------------------------------------------------------------------

def _truncate(s: str, limit: int = 48000) -> str:
    if len(s) <= limit:
        return s
    fd, path = tempfile.mkstemp(prefix="3jane_out_", suffix=".txt", dir="/tmp")
    with os.fdopen(fd, "w") as f:
        f.write(s)
    half = limit // 2
    return (f"{s[:half]}\n…[output truncated — {len(s)} bytes total; full output saved to "
            f"{path}; read or grep it]…\n{s[-half:]}")


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------

_IMG_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def tool_read(args, ctx):
    path = args["file_path"]
    key = os.path.abspath(path)
    if not os.path.exists(path):
        return f"Error: path does not exist: {path}", []

    if os.path.isdir(path):
        entries = []
        for e in sorted(os.listdir(path)):
            full = os.path.join(path, e)
            entries.append(e + ("/" if os.path.isdir(full) else ""))
        ctx.conversation.read_files.add(key)
        return ("\n".join(entries) if entries else "(empty directory)"), []

    ext = os.path.splitext(path)[1].lower()
    if ext in _IMG_EXTS:
        return _read_image(path, key, ctx)
    if ext == ".pdf":
        ctx.conversation.read_files.add(key)
        return _read_pdf(path), []

    try:
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return f"Error reading file: {e}", []

    offset = max(1, int(args.get("offset", 1)))
    limit = int(args.get("limit", 2000))
    sel = lines[offset - 1: offset - 1 + limit]
    numbered = []
    for i, ln in enumerate(sel, start=offset):
        ln = ln.rstrip("\n")
        if len(ln) > 2000:
            ln = ln[:2000] + " …[truncated]"
        numbered.append(f"{i}: {ln}")
    ctx.conversation.read_files.add(key)
    body = "\n".join(numbered)
    remaining = len(lines) - (offset - 1 + limit)
    if remaining > 0:
        body += f"\n…[{remaining} more lines; use offset to continue]"
    return (body if body else "(empty file)"), []


def _read_image(path, key, ctx):
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
            fmt = (im.format or "").lower()
    except Exception:
        w = h = 0
        fmt = os.path.splitext(path)[1][1:]
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    mime = {"jpg": "jpeg"}.get(fmt, fmt) or "png"
    data_url = f"data:image/{mime};base64,{b64}"
    ctx.conversation.read_files.add(key)
    extra = [{
        "role": "user",
        "content": [
            {"type": "text", "text": f"[Image loaded from {path} ({w}x{h})]"},
            {"type": "image_url", "image_url": {"url": data_url}},
        ],
    }]
    return f"Image read: {path} ({w}x{h}). Provided in the following message.", extra


def _read_pdf(path):
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        pages = []
        for i, page in enumerate(reader.pages, 1):
            pages.append(f"--- page {i} ---\n{page.extract_text() or ''}")
        out = "\n".join(pages).strip()
        return _truncate(out) if out else "(no extractable text in PDF)"
    except Exception as e:
        return f"Error reading PDF: {e}"


def tool_write(args, ctx):
    path = args["file_path"]
    content = args.get("content", "")
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
    except Exception as e:
        return f"Error writing file: {e}", []
    ctx.conversation.read_files.add(os.path.abspath(path))
    return f"Wrote {len(content)} bytes to {path}", []


def tool_edit(args, ctx):
    path = args["file_path"]
    old = args["old_string"]
    new = args.get("new_string", "")
    replace_all = bool(args.get("replace_all", False))
    key = os.path.abspath(path)

    if key not in ctx.conversation.read_files:
        return f"Error: you must read {path} with the read tool before editing it.", []
    if not os.path.exists(path):
        return f"Error: file does not exist: {path}", []
    if old == "":
        return "Error: old_string must not be empty.", []

    with open(path, "r", errors="replace") as f:
        content = f.read()
    count = content.count(old)
    if count == 0:
        return "Error: old_string not found in content.", []
    if count > 1 and not replace_all:
        return (f"Error: found {count} matches for old_string. Add surrounding context to make it "
                f"unique, or set replace_all=true."), []
    content = content.replace(old, new) if replace_all else content.replace(old, new, 1)
    with open(path, "w") as f:
        f.write(content)
    detail = f"all {count} occurrences" if replace_all else "1 occurrence"
    return f"Edited {path} ({detail}).", []


def tool_glob(args, ctx):
    import glob as g
    pattern = args["pattern"]
    base = args.get("path", ctx.config.workspace_dir)
    full = pattern if os.path.isabs(pattern) else os.path.join(base, pattern)
    matches = [m for m in g.glob(full, recursive=True) if os.path.isfile(m)]
    matches.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    if not matches:
        return "(no matches)", []
    return "\n".join(matches[:500]), []


def tool_grep(args, ctx):
    pattern = args["pattern"]
    path = args.get("path", ctx.config.workspace_dir)
    include = args.get("include")
    cmd = ["rg", "--line-number", "--no-heading", "--color", "never"]
    if include:
        cmd += ["--glob", include]
    cmd += ["-e", pattern, path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        out = r.stdout
        return (_truncate(out) if out.strip() else "(no matches)"), []
    except FileNotFoundError:
        return _grep_python(pattern, path, include), []
    except subprocess.TimeoutExpired:
        return "Error: grep timed out", []


def _grep_python(pattern, path, include):
    import fnmatch
    import re as _re
    try:
        rx = _re.compile(pattern)
    except _re.error as e:
        return f"Error: invalid regex: {e}"
    hits = []
    targets = [path] if os.path.isfile(path) else []
    if os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for name in files:
                if include and not fnmatch.fnmatch(name, include):
                    continue
                targets.append(os.path.join(root, name))
    for fp in targets:
        try:
            with open(fp, "r", errors="replace") as f:
                for n, line in enumerate(f, 1):
                    if rx.search(line):
                        hits.append(f"{fp}:{n}:{line.rstrip()}")
                        if len(hits) >= 1000:
                            return _truncate("\n".join(hits))
        except Exception:
            continue
    return "\n".join(hits) if hits else "(no matches)"


# ---------------------------------------------------------------------------
# Action + subagent tools
# ---------------------------------------------------------------------------

def tool_load_action(args, ctx):
    key = args["name"].strip()
    visited = set()
    text = load_action(ctx.config.actions_dir, key, visited)
    deps = sorted(k for k in visited if k != key)
    ctx.log.note(f"📂 Loaded action: {key}" + (f" (+ {', '.join(deps)})" if deps else ""))
    return text, []


async def tool_send_file(args, ctx):
    path = args["file_path"]
    if ctx.bot is None or ctx.chat_id is None:
        return "Error: no chat is available to send files to.", []
    if not os.path.exists(path):
        return f"Error: path does not exist: {path}", []
    if os.path.isdir(path):
        return f"Error: {path} is a directory, not a file.", []
    caption = args.get("caption") or None
    try:
        await send_document(ctx.bot, ctx.chat_id, path, caption)
    except Exception as e:
        return f"Error sending file: {e}", []
    size = os.path.getsize(path)
    name = os.path.basename(path)
    ctx.log.note(f"📤 Sent file: {name} ({size} bytes)")
    return f"Sent {name} to the user ({size} bytes).", []


async def tool_task(args, ctx):
    st = args["subagent_type"]
    prompt = args["prompt"]
    if st not in ("explore", "general", "web"):
        return f"Error: unknown subagent_type {st}", []
    result = await ctx.run_subagent(st, prompt)
    return result, []


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

async def execute_tool(name, args, ctx):
    """Execute a tool. Returns (result_text, extra_messages). Errors are returned as
    text so the agent can see and react to them."""
    try:
        if name == "bash":
            timeout_ms = int(args.get("timeout", 120000))
            out, code, timed = await asyncio.to_thread(
                ctx.shell.run, args["command"], args.get("workdir"), timeout_ms,
            )
            out = _truncate(out)
            suffix = ""
            if timed:
                suffix += "\n[command timed out and was interrupted]"
            elif code != 0:
                suffix += f"\n[exit code {code}]"
            return ((out + suffix).strip() or f"(no output) [exit code {code}]"), []
        if name == "read":
            return await asyncio.to_thread(tool_read, args, ctx)
        if name == "write":
            return await asyncio.to_thread(tool_write, args, ctx)
        if name == "edit":
            return await asyncio.to_thread(tool_edit, args, ctx)
        if name == "glob":
            return await asyncio.to_thread(tool_glob, args, ctx)
        if name == "grep":
            return await asyncio.to_thread(tool_grep, args, ctx)
        if name == "send_file":
            return await tool_send_file(args, ctx)
        if name == "load_action":
            return tool_load_action(args, ctx)
        if name == "task":
            return await tool_task(args, ctx)
        return f"Error: unknown tool {name}", []
    except Exception as e:
        import traceback
        return f"Error executing {name}: {e}\n{traceback.format_exc()[-1200:]}", []
