#!/bin/bash
set -e

# Create venv if bind mount is empty
if [ ! -f /venv/bin/python3 ]; then
  python3 -m venv /venv
fi

# Start Claude Code Router in the background so the `claude` CLI can proxy
# through it. Config lives at $HOME/.claude-code-router/config.json
# (bind-mounted to ./claude-code-router).
mkdir -p "$HOME/.claude-code-router/logs"
if ! ccr status >/dev/null 2>&1; then
  ccr start >/tmp/ccr.log 2>&1 &
fi

# Wait briefly for the router to accept connections.
CCR_PORT="${CCR_PORT:-3456}"
for i in $(seq 1 50); do
  if (echo > "/dev/tcp/127.0.0.1/$CCR_PORT") 2>/dev/null; then
    break
  fi
  sleep 0.2
done

# Export the env vars that tell `claude` to route through ccr
# (ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN, etc.). These inherit into the
# bot process and through to every `claude` subprocess it spawns.
eval "$(ccr activate)"

exec "$@"
