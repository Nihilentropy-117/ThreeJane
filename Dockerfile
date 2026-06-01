FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    TIKTOKEN_CACHE_DIR=/app/.tiktoken

RUN apt-get update && apt-get install -y --no-install-recommends \
        bash git curl ca-certificates ripgrep procps less nano build-essential tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-cache the tiktoken encoding so the bot never fetches it at runtime.
RUN mkdir -p /app/.tiktoken && python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

COPY app ./app
RUN mkdir -p /workspace

CMD ["python", "-m", "app.main"]
