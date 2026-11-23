import os
from dataclasses import dataclass, field
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
    llm_api_key: str = field(default="", repr=False)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = ""
    llm_timeout: float = 30
    llm_max_concurrent: int = 2
    llm_requests_per_minute: int = 20
    llm_player_cooldown: float = 3
    tool_calling_enabled: bool = False
    tool_max_calls: int = 4
    tool_calling_timeout: float = 90
    tool_api_timeout: float = 25
    tool_llm_max_tokens: int = 2048
    memory_enabled: bool = True
    memory_db_path: str = str(Path(__file__).resolve().parents[2] / "data" / "minemate-memory.sqlite3")
    memory_context_window: int = 32768
    memory_compaction_threshold: float = 0.75
    memory_retain_turns: int = 6
    memory_max_summary_chars: int = 4000
    memory_max_tool_result_chars: int = 4000
    memory_session_idle_days: float = 30
    memory_auto_compact: bool = True


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
    def bounded_number(name: str, default: float, maximum: float) -> float:
        try:
            number = float(values.get(name, str(default)))
        except ValueError:
            raise ValueError(f"{name} 配置无效。") from None
        if not 0 < number <= maximum:
            raise ValueError(f"{name} 配置超出范围。")
        return number

    def bounded_int(name: str, default: int, maximum: int) -> int:
        number = bounded_number(name, default, maximum)
        if not number.is_integer():
            raise ValueError(f"{name} 必须为整数。")
        return int(number)

    llm_url = values.get("LLM_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    from urllib.parse import urlsplit
    try:
        parsed = urlsplit(llm_url)
        valid_url = parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
    except ValueError:
        valid_url = False
    if not valid_url:
        raise ValueError("LLM_BASE_URL 必须为不含凭据或查询参数的 HTTP 地址。")
    tool_enabled = values.get("TOOL_CALLING_ENABLED", "false").strip().lower()
    if tool_enabled not in ("true", "false"):
        raise ValueError("TOOL_CALLING_ENABLED 必须为 true 或 false。")
    memory_enabled = values.get("MEMORY_ENABLED", "true").strip().lower()
    if memory_enabled not in ("true", "false"):
        raise ValueError("MEMORY_ENABLED 必须为 true 或 false。")
    auto_compact = values.get("MEMORY_AUTO_COMPACT", "true").strip().lower()
    if auto_compact not in ("true", "false"):
        raise ValueError("MEMORY_AUTO_COMPACT 必须为 true 或 false。")
    default_memory_db = str(Path(__file__).resolve().parents[2] / "data" / "minemate-memory.sqlite3")
    memory_db_path = values.get("MEMORY_DB_PATH", "").strip() or default_memory_db
    if not memory_db_path or "\x00" in memory_db_path:
        raise ValueError("MEMORY_DB_PATH 不能为空且不能包含空字符。")

    def bounded_ratio(name: str, default: float) -> float:
        try:
            number = float(values.get(name, str(default)))
        except ValueError:
            raise ValueError(f"{name} 配置无效。") from None
        if not 0.1 <= number <= 0.95:
            raise ValueError(f"{name} 必须在 0.1–0.95 之间。")
        return number

    def bounded_int_range(name: str, default: int, minimum: int, maximum: int) -> int:
        number = bounded_int(name, default, maximum)
        if number < minimum:
            raise ValueError(f"{name} 必须不小于 {minimum}。")
        return number

    def bounded_non_negative(name: str, default: float, maximum: float) -> float:
        try:
            number = float(values.get(name, str(default)))
        except ValueError:
            raise ValueError(f"{name} 配置无效。") from None
        if not 0 <= number <= maximum:
            raise ValueError(f"{name} 配置超出范围。")
        return number

    return Settings(
        url, timeout, host, int(port_text),
        llm_api_key=values.get("LLM_API_KEY", "").strip(),
        llm_base_url=llm_url, llm_model=values.get("LLM_MODEL", "").strip(),
        llm_timeout=bounded_number("LLM_TIMEOUT", 30, 120),
        llm_max_concurrent=bounded_int("LLM_MAX_CONCURRENT", 2, 8),
        llm_requests_per_minute=bounded_int("LLM_REQUESTS_PER_MINUTE", 20, 120),
        llm_player_cooldown=bounded_number("LLM_PLAYER_COOLDOWN", 3, 60),
        tool_calling_enabled=tool_enabled == "true",
        tool_max_calls=bounded_int("TOOL_MAX_CALLS", 4, 50),
        tool_calling_timeout=bounded_number("TOOL_CALLING_TIMEOUT", 90, 120),
        tool_api_timeout=bounded_number("TOOL_API_TIMEOUT", 25, 60),
        tool_llm_max_tokens=bounded_int("TOOL_LLM_MAX_TOKENS", 2048, 8192),
        memory_enabled=memory_enabled == "true",
        memory_db_path=memory_db_path,
        memory_context_window=bounded_int_range("MEMORY_CONTEXT_WINDOW", 32768, 1024, 1_000_000),
        memory_compaction_threshold=bounded_ratio("MEMORY_COMPACTION_THRESHOLD", 0.75),
        memory_retain_turns=bounded_int_range("MEMORY_RETAIN_TURNS", 6, 1, 64),
        memory_max_summary_chars=bounded_int_range("MEMORY_MAX_SUMMARY_CHARS", 4000, 256, 16000),
        memory_max_tool_result_chars=bounded_int_range("MEMORY_MAX_TOOL_RESULT_CHARS", 4000, 256, 32000),
        memory_session_idle_days=bounded_non_negative("MEMORY_SESSION_IDLE_DAYS", 30, 3650),
        memory_auto_compact=auto_compact == "true",
    )
