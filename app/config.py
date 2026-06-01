import os
from dataclasses import dataclass

import yaml


def _expand(v):
    """Expand a ${ENV_VAR} reference against the environment."""
    if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
        return os.environ.get(v[2:-1], "")
    return v


@dataclass
class Config:
    bot_token: str
    api_url: str
    allowed_user_id: int

    openrouter_api_key: str
    referer: str
    title: str

    model_main: str
    model_explore: str
    model_general: str
    model_web: str

    token_limit: int
    inactivity_timeout: int
    thinking_update_interval: int

    actions_dir: str
    workspace_dir: str


def load_config(path: str = "/app/settings/config.yaml") -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    tg = raw.get("telegram", {})
    orr = raw.get("openrouter", {})
    models = raw.get("models", {})
    agent = raw.get("agent", {})
    paths = raw.get("paths", {})

    return Config(
        bot_token=_expand(tg.get("bot_token")),
        api_url=tg.get("api_url", "http://telegram-bot-api:8081"),
        allowed_user_id=int(_expand(tg.get("allowed_user_id"))),
        openrouter_api_key=_expand(orr.get("api_key")),
        referer=orr.get("referer", "https://localhost/3jane"),
        title=orr.get("title", "3Jane"),
        model_main=models.get("main"),
        model_explore=models.get("explore"),
        model_general=models.get("general"),
        model_web=models.get("web"),
        token_limit=int(agent.get("token_limit", 25000)),
        inactivity_timeout=int(agent.get("inactivity_timeout", 3600)),
        thinking_update_interval=int(agent.get("thinking_update_interval", 10)),
        actions_dir=paths.get("actions_dir", "/app/settings/Actions"),
        workspace_dir=paths.get("workspace_dir", "/workspace"),
    )
