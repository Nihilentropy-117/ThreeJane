# Claude Telegram Agent

Dockerized Telegram bot + Claude Agent SDK bridge with streamed tool activity and persistent conversation history.

## Services

- `telegram-bot-api`: Local Telegram Bot API server (`--local`) with shared file storage.
- `bot`: aiogram bot that handles auth, SQLite history, SSE streaming UI updates, `/new`, `/history`, `/cancel`.
- `agent`: HTTP SSE server that runs Claude Agent SDK queries in `/workspace` with access to `/shared-files`.

## Quick Start

1. Copy env template:

```bash
cp .env.example .env
```

2. Fill required values in `.env`:

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `TELEGRAM_BOT_TOKEN`
- `AUTHORIZED_USER_ID`
- `ANTHROPIC_API_KEY`

3. Build and run:

```bash
docker compose up --build
```

## Notes

- Uploaded Telegram files are resolved to `/shared-files/...` and prepended into the prompt.
- Agent output files created under `/shared-files/outgoing/` are sent back as Telegram documents.
- Conversation history is persisted in `/data/conversations/conversations.db` inside the bot container volume.
