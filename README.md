# 3Jane

A single-user Telegram bot that runs as an autonomous, Claude-Code-style agent inside a Docker
container, backed by OpenRouter. It plans, runs tools (bash + file ops), spawns cheaper subagents,
loads pre-written "actions", and streams its work into a live "thinking" message — then sends a
clean final result as a separate message.

## How it works

- **The agent is the LLM.** A fixed main model drives an agentic loop: it calls tools, reads the
  results, and continues until it answers with no tool call. That final turn is the result.
- **Tools:** `bash`, `read`, `write`, `edit`, `glob`, `grep`, `task`, `load_action`. The bash shell
  is persistent (cwd/env/venv carry across calls). `read` before `edit` is enforced.
- **Subagents (`task`):** `explore` (read-only), `general` (full file + bash), `web` (live web
  search via the OpenRouter web plugin). Each uses its own, cheaper model and its own 25k-token
  window, runs one at a time, starts fresh, returns one message, and cannot spawn subagents.
- **Memory:** held in RAM only. Cleared on `/new` or after 1 hour of inactivity. Not persisted.
  Sliding window is 25k tokens for the main agent and for each subagent.
- **Files:** files you send arrive on a volume shared with the local Bot API server (2 GB limit).
  The agent is given the path and reads it with `read`. Images are only seen when the agent reads
  the image file; PDFs are text-extracted.
- **Full root.** The agent may run anything and install anything in its container.

## Commands

`/start` · `/help` · `/new` (clear memory + reset shell) · `/cancel` (stop current task) · `/status`

## Setup

1. **Telegram credentials.** Get a bot token from @BotFather and your API ID/hash from
   <https://my.telegram.org>. Find your numeric user id (e.g. via @userinfobot).
2. `cp .env.example .env` and fill in `BOT_TOKEN`, `ALLOWED_USER_ID`, `OPENROUTER_API_KEY`,
   `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`.
3. Edit `settings/config.yaml` and set the four model slugs to current OpenRouter models.
4. `docker compose up --build`.

## Operational gotchas

- **Switch the bot off Telegram's cloud first.** A bot token can only talk to one Bot API server.
  If the token was ever used against Telegram's cloud, call `logOut` on the cloud once before
  pointing it at the local server:
  `curl https://api.telegram.org/bot<TOKEN>/logOut`
  Then start the stack. (A brand-new token can skip this.)
- **Shared volume path must match.** Both containers mount `telegram-files` at
  `/var/lib/telegram-bot-api` so the absolute path from `get_file` resolves inside the bot. Don't
  change one without the other.
- **Models drift.** The slugs in `config.yaml` are examples — verify them against
  <https://openrouter.ai/models>.

## Writing actions

An action is a folder under `settings/Actions/<key>/` containing `<key>.md` with YAML frontmatter:

```markdown
---
Name: Deploy Site
Description: Build and deploy the static site to the server.
AlsoLoad: [ssh-helpers]
---
Step-by-step instructions go here...
```

`Name`/`Description` of every action are listed in the system prompt so the agent knows what
exists. It loads one with `load_action`; `AlsoLoad` dependencies are pulled in recursively. Extra
files in the folder are surfaced to the agent by path. Actions are re-scanned on each message, so
edits take effect without a restart.

## Possible next steps

- File send-back (let the agent return files to Telegram).
- Startup validation of action files (circular `AlsoLoad`, missing keys).
