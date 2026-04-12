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

// Track session IDs and message counts per chat
const chatSessions = new Map(); // chatId -> { sessionId, messageCount }
// Serialize requests per chat
const chatQueues = new Map(); // chatId -> Promise chain

function getSession(chatId) {
  if (!chatSessions.has(chatId)) {
    chatSessions.set(chatId, { sessionId: randomUUID(), messageCount: 0 });
  }
  return chatSessions.get(chatId);
}

function runClaude(chatId, prompt) {
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
    });

    let buffer = "";
    let thinkingParts = [];
    let toolParts = [];
    let resultText = "";

    proc.stdout.on("data", (chunk) => {
      buffer += chunk.toString();
      const lines = buffer.split("\n");
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const evt = JSON.parse(line);

          if (evt.type === "assistant" && evt.subtype === "thinking") {
            thinkingParts.push(evt.content || "");
          } else if (evt.type === "assistant" && evt.subtype === "tool_use") {
            const name = evt.tool_name || evt.name || "tool";
            const input = evt.input
              ? JSON.stringify(evt.input).slice(0, 200)
              : "";
            toolParts.push(`${name}: ${input}`);
          } else if (evt.type === "result") {
            resultText = evt.result || "";
          }
        } catch {}
      }
    });

    proc.stderr.on("data", (chunk) => {
      console.error("claude stderr:", chunk.toString());
    });

    proc.on("close", (code) => {
      resolve({ thinkingParts, toolParts, resultText });
    });

    proc.on("error", reject);
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

  if (ALLOWED?.length && !ALLOWED.includes(userId)) {
    return bot.sendMessage(chatId, "Unauthorized.");
  }

  let prompt = "";

  // Check for files
  const file =
    msg.document ||
    (msg.photo && msg.photo[msg.photo.length - 1]) ||
    msg.video ||
    msg.audio ||
    msg.voice;

  if (file) {
    const fileId = file.file_id;
    try {
      const localPath = await bot.downloadFile(fileId, FILES_DIR);
      const relPath = localPath.startsWith("/app/")
        ? "." + localPath.slice(4)
        : localPath;
      const caption = msg.caption || "";
      prompt = caption
        ? `${caption}\n\n--- User Sent File: ${relPath}`
        : `--- User Sent File: ${relPath}`;
    } catch (err) {
      console.error("Download error:", err);
      return bot.sendMessage(chatId, "Failed to download file.");
    }
  } else if (msg.text) {
    if (msg.text === "/start") {
      return bot.sendMessage(
        chatId,
        "Send me a message and I'll pass it to Claude Code."
      );
    }
    if (msg.text === "/reset") {
      chatSessions.delete(chatId);
      return bot.sendMessage(chatId, "Session reset.");
    }
    prompt = msg.text;
  } else {
    return;
  }

  // Queue per chat so messages don't overlap
  enqueue(chatId, async () => {
    const typingInterval = setInterval(
      () => bot.sendChatAction(chatId, "typing"),
      4000
    );
    bot.sendChatAction(chatId, "typing");

    try {
      const { thinkingParts, toolParts, resultText } = await runClaude(
        chatId,
        prompt
      );

      // Message 1: thinking + tool calls
      let meta = "";
      if (thinkingParts.length > 0) {
        const thinking = thinkingParts.join("\n").slice(0, 3500);
        meta += `💭 Thinking:\n\n${thinking}\n`;
      }
      if (toolParts.length > 0) {
        meta += `\n🔧 Tools:\n${toolParts.map((t) => `• ${t}`).join("\n")}`;
      }

      if (meta) {
        for (const chunk of splitMsg(meta)) {
          await bot.sendMessage(chatId, chunk).catch(console.error);
        }
      }

      // Message 2: final response
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
