FROM node:22-slim

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

WORKDIR /app

COPY package.json .
RUN npm install

COPY bot.mjs .

# Workspace for Claude to operate in
RUN mkdir -p /app/workspace /app/.claude /app/telegram-files

CMD ["node", "bot.mjs"]
