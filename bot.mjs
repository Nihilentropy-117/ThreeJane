import TelegramBot from "node-telegram-bot-api";
import { spawn } from "child_process";
import { randomUUID } from "crypto";

const TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const ALLOWED = process.env.ALLOWED_USER_IDS
  ? process.env.ALLOWED_USER_IDS.split(",").map((s) => s.trim()).filter(Boolean)
  : [];
const API_BASE =
  process.env.TELEGRAM_API_BASE || "http://telegram-bot-api:8081";
const FILES_DIR = "/app/telegram-files";
const WORKSPACE = "/app/workspace";

const bot = new TelegramBot(TOKEN, {
  polling: true,
  baseApiUrl: API_BASE,
});

const chatSessions = new Map();
const chatQueues = new Map();
const chatProcs = new Map();

const HELP_TEXT =
  "Send me a message and I'll pass it to Claude Code.\n\n" +
  "Commands:\n" +
  "/start - show this help\n" +
  "/help - show this help\n" +
  "/reset - reset the Claude session (alias: /new)\n" +
  "/new - start a new Claude session (alias of /reset)\n" +
  "/stop - hard stop any currently running Claude activity";

function getSession(chatId) {
  if (!chatSessions.has(chatId)) {
    chatSessions.set(chatId, { sessionId: randomUUID(), messageCount: 0 });
  }
  return chatSessions.get(chatId);
}

class LiveMessage {
  constructor(bot, chatId) {
    this.bot = bot;
    this.chatId = chatId;
    this.messageId = null;
    this.text = "";
    this.pendingEdit = null;
    this.editDelay = 600;
  }

  async append(line) {
    if (this.text.length + line.length + 1 > 4000 && this.messageId) {
      await this.flush();
      this.messageId = null;
      this.text = "";
    }
    this.text += (this.text ? "\n" : "") + line;
    this.scheduleEdit();
  }

  scheduleEdit() {
    if (this.pendingEdit) return;
    this.pendingEdit = setTimeout(() => this.flush(), this.editDelay);
  }

  async flush() {
    if (this.pendingEdit) {
      clearTimeout(this.pendingEdit);
      this.pendingEdit = null;
    }
    if (!this.text) return;
    try {
      if (!this.messageId) {
        const sent = await this.bot.sendMessage(this.chatId, this.text);
        this.messageId = sent.message_id;
      } else {
        await this.bot.editMessageText(this.text, {
          chat_id: this.chatId,
          message_id: this.messageId,
        });
      }
    } catch (err) {
      if (!err.message?.includes("message is not modified")) {
        console.error("Telegram edit error:", err.message);
      }
    }
  }
}

function runClaude(chatId, prompt, liveMsg) {
  const session = getSession(chatId);
  const isFirst = session.messageCount === 0;
  session.messageCount++;

  const args = [
    "-p",
    "--output-format",
    "stream-json",
    "--verbose",
    "--dangerously-skip-permissions",
  ];

  if (isFirst) {
    args.push("--session-id", session.sessionId);
  } else {
    args.push("--resume", session.sessionId);
  }

  args.push(prompt);

  return new Promise((resolve, reject) => {
    const proc = spawn("claude", args, {
      cwd: WORKSPACE,
      stdio: ["ignore", "pipe", "pipe"],
      env: {
        ...process.env,
        HOME: "/app",
        CLAUDE_CONFIG_DIR: "/app/.claude",
      },
      detached: true,
    });
    chatProcs.set(chatId, proc);

    let buffer = "";
    let resultText = "";
    // Track which content blocks we've already reported (by index within a message)
    const seenBlocks = new Set();

    proc.stdout.on("data", (chunk) => {
      buffer += chunk.toString();
      const lines = buffer.split("\n");
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const evt = JSON.parse(line);

          // assistant events have message.content[] with thinking, tool_use, text blocks
          if (evt.type === "assistant" && evt.message?.content) {
            for (const block of evt.message.content) {
              // Deduplicate — same message can appear multiple times as content grows
              const key = `${evt.message.id}:${block.type}:${block.id || block.thinking?.slice(0, 40) || ""}`;

              if (block.type === "thinking" && block.thinking && !seenBlocks.has(key)) {
                seenBlocks.add(key);
                const snippet = block.thinking.trim().slice(0, 300);
                if (snippet) liveMsg.append(`💭 ${snippet}`);
              }

              if (block.type === "tool_use" && !seenBlocks.has(key)) {
                seenBlocks.add(key);
                let summary = `🔧 ${block.name}`;
                const input = block.input || {};
                if (input.command) summary += `: ${input.command.slice(0, 150)}`;
                else if (input.file_path) summary += `: ${input.file_path}`;
                else if (input.pattern) summary += `: ${input.pattern}`;
                else if (input.query) summary += `: ${input.query.slice(0, 150)}`;
                liveMsg.append(summary);
              }
            }
          }

          // tool results come as type: "user" with tool_result content
          if (evt.type === "user" && evt.message?.content) {
            for (const block of evt.message.content) {
              if (block.type === "tool_result" && typeof block.content === "string") {
                const preview = block.content.trim().slice(0, 200);
                if (preview) liveMsg.append(`  ↳ ${preview}${block.content.length > 200 ? "…" : ""}`);
              }
            }
          }

          if (evt.type === "result") {
            resultText = evt.result || "";
          }
        } catch {}
      }
    });

    proc.stderr.on("data", (chunk) => {
      console.error("claude stderr:", chunk.toString());
    });

    proc.on("close", () => {
      if (chatProcs.get(chatId) === proc) chatProcs.delete(chatId);
      resolve(resultText);
    });
    proc.on("error", (err) => {
      if (chatProcs.get(chatId) === proc) chatProcs.delete(chatId);
      reject(err);
    });
  });
}

function enqueue(chatId, fn) {
  const prev = chatQueues.get(chatId) || Promise.resolve();
  const next = prev.then(fn, fn);
  chatQueues.set(chatId, next);
  return next;
}

bot.on("message", async (msg) => {
  const chatId = msg.chat.id;
  const userId = String(msg.from.id);

  if (ALLOWED.length && !ALLOWED.includes(userId)) {
    return bot.sendMessage(chatId, "Unauthorized.");
  }

  let prompt = "";

  const file =
    msg.document ||
    (msg.photo && msg.photo[msg.photo.length - 1]) ||
    msg.video ||
    msg.audio ||
    msg.voice;

  if (file) {
    const fileId = file.file_id;
    try {
      // With a local Bot API server, files are stored on disk under
      // <data_dir>/<token>/<type>/. Use getFile to get the relative path,
      // then resolve it against the local files directory + token.
      const fileInfo = await bot.getFile(fileId);
      const localPath = `${FILES_DIR}/${TOKEN}/${fileInfo.file_path}`;
      const caption = msg.caption || "";
      if (msg.photo) {
        // Claude Code reads images via its Read tool when given a file path
        prompt = caption
          ? `${caption}\n\n--- User Sent Photo: ${localPath}`
          : `What's in this image?\n\n--- User Sent Photo: ${localPath}`;
      } else {
        prompt = caption
          ? `${caption}\n\n--- User Sent File: ${localPath}`
          : `--- User Sent File: ${localPath}`;
      }
    } catch (err) {
      console.error("Download error:", err);
      return bot.sendMessage(chatId, "Failed to download file.");
    }
  } else if (msg.text) {
    if (msg.text === "/start" || msg.text === "/help") {
      return bot.sendMessage(chatId, HELP_TEXT);
    }
    if (msg.text === "/reset" || msg.text === "/new") {
      chatSessions.delete(chatId);
      return bot.sendMessage(chatId, "Session reset.");
    }
    if (msg.text === "/stop") {
      const proc = chatProcs.get(chatId);
      if (!proc) {
        return bot.sendMessage(chatId, "Nothing to stop.");
      }
      try {
        process.kill(-proc.pid, "SIGKILL");
      } catch {
        try { proc.kill("SIGKILL"); } catch {}
      }
      chatProcs.delete(chatId);
      return bot.sendMessage(chatId, "Stopped.");
    }
    prompt = msg.text;
  } else {
    return;
  }

  enqueue(chatId, async () => {
    const typingInterval = setInterval(
      () => bot.sendChatAction(chatId, "typing"),
      4000
    );
    bot.sendChatAction(chatId, "typing");

    try {
      const liveMsg = new LiveMessage(bot, chatId);
      const resultText = await runClaude(chatId, prompt, liveMsg);
      await liveMsg.flush();

      if (resultText) {
        for (const chunk of splitMsg(resultText)) {
          await bot
            .sendMessage(chatId, chunk, { parse_mode: "Markdown" })
            .catch(() => bot.sendMessage(chatId, chunk));
        }
      } else {
        await bot.sendMessage(chatId, "(No response from Claude)");
      }
    } catch (err) {
      console.error("Error:", err);
      await bot.sendMessage(chatId, `Error: ${err.message}`);
    } finally {
      clearInterval(typingInterval);
    }
  });
});

function splitMsg(text, max = 4000) {
  const chunks = [];
  while (text.length > max) {
    let i = text.lastIndexOf("\n", max);
    if (i < max / 2) i = max;
    chunks.push(text.slice(0, i));
    text = text.slice(i);
  }
  if (text) chunks.push(text);
  return chunks;
}

process.on("SIGINT", () => {
  console.log("Shutting down...");
  bot.stopPolling();
  process.exit(0);
});
process.on("SIGTERM", () => {
  bot.stopPolling();
  process.exit(0);
});

console.log("ThreeJane bot started.");
