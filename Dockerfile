FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/venv/bin:$PATH"

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

WORKDIR /app

COPY package.json .
RUN npm install

COPY bot.mjs entrypoint.sh ./

RUN mkdir -p /app/workspace /app/.claude /app/telegram-files

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["node", "bot.mjs"]
