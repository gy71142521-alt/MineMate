import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


# 从 agent/.env 读取本地配置；已有系统环境变量优先，不覆盖它们。
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    node_api_url: str
    node_api_timeout_seconds: float
    agent_host: str
    agent_port: int


def load_settings(env: dict[str, str] | None = None) -> Settings:
    values = os.environ if env is None else env
    url = values.get("NODE_API_URL", "http://127.0.0.1:3000").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError("NODE_API_URL 必须是 http:// 或 https:// 地址。")
    timeout_text = values.get("NODE_API_TIMEOUT_SECONDS", "5").strip()
    try:
        timeout = float(timeout_text)
    except ValueError as exc:
        raise ValueError("NODE_API_TIMEOUT_SECONDS 必须是正数。") from exc
    if not 0 < timeout <= 120:
        raise ValueError("NODE_API_TIMEOUT_SECONDS 必须在 0 和 120 秒之间。")
    host = values.get("AGENT_HOST", "127.0.0.1").strip()
    port_text = values.get("AGENT_PORT", "8000").strip()
    if not host:
        raise ValueError("AGENT_HOST 不能为空。")
    if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        raise ValueError("AGENT_PORT 必须是 1–65535 的整数。")
    return Settings(url, timeout, host, int(port_text))
