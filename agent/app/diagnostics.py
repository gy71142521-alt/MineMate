"""统一的安全诊断输出。

目标：服务端日志能看清“哪个阶段、什么异常、什么上下文”，
同时不把 API Key、请求头或模型原文写进日志，也不改变返回给调用方的错误码。
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import traceback
from collections.abc import Iterable
from typing import Any


_REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("minemate_request_id", default="-")
_SECRETS: tuple[str, ...] = ()
_TRACEBACK_MODE = "errors"  # none | errors | all

# 常见凭据形态；即使调用方忘记传入密钥，也能兜底脱敏。
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{4,}"),
    re.compile(r"(?i)\bBearer\s+[^\s,;\"']+"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password)\b\s*[:=]\s*[^\s,;\"']+"),
)


def set_secrets(secrets: Iterable[str]) -> None:
    global _SECRETS
    _SECRETS = tuple(sorted({value for value in secrets if isinstance(value, str) and len(value) >= 3}, key=len, reverse=True))


def set_traceback_mode(value: str | bool) -> None:
    """none 不打印堆栈；errors 只给未预期/基础设施异常；all 连预期校验也打印。"""
    global _TRACEBACK_MODE
    if isinstance(value, bool):
        _TRACEBACK_MODE = "errors" if value else "none"
        return
    text = str(value).strip().lower()
    if text in {"all", "full", "always"}:
        _TRACEBACK_MODE = "all"
    elif text in {"0", "false", "no", "off", "none"}:
        _TRACEBACK_MODE = "none"
    else:
        _TRACEBACK_MODE = "errors"


def set_traceback_enabled(enabled: bool) -> None:
    set_traceback_mode(enabled)


def traceback_mode() -> str:
    return _TRACEBACK_MODE


def traceback_enabled() -> bool:
    return _TRACEBACK_MODE != "none"


def model_preview_enabled() -> bool:
    # all 模式才输出模型原文预览；errors 模式保持只输出结构摘要。
    return _TRACEBACK_MODE == "all"


def model_preview(value: object, secrets: Iterable[str] = (), limit: int = 600) -> str | None:
    """仅在 LOG_TRACEBACK=all 时返回脱敏、单行、限长的模型输出预览。"""
    if not model_preview_enabled():
        return None
    if isinstance(value, (dict, list)):
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError, RecursionError):
            text = repr(value)
    else:
        text = str(value)
    return safe_text(text, secrets, limit)


def set_request_id(value: str) -> contextvars.Token[str]:
    return _REQUEST_ID.set((value or "-")[:32])


def reset_request_id(token: contextvars.Token[str]) -> None:
    try:
        _REQUEST_ID.reset(token)
    except ValueError:
        # 任务边界之外重置失败不应影响主流程。
        pass


def current_request_id() -> str:
    return _REQUEST_ID.get()


def redact(value: object, secrets: Iterable[str] = ()) -> str:
    """替换已知密钥与常见凭据形态；不改变其余文本。"""
    text = str(value)
    for secret in sorted({item for item in (*_SECRETS, *secrets) if isinstance(item, str) and len(item) >= 3}, key=len, reverse=True):
        text = text.replace(secret, "[已隐藏]")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[已隐藏]", text)
    return text


def safe_text(value: object, secrets: Iterable[str] = (), limit: int = 500) -> str:
    """单行、脱敏、限长；用于日志字段，避免换行伪造日志行。"""
    text = redact(value, secrets).replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
    if len(text) > limit:
        return text[:limit] + f"…(+{len(text) - limit})"
    return text


def format_exception(error: BaseException, secrets: Iterable[str] = (), max_chars: int = 8000) -> str:
    """输出完整异常链（含 cause/context），脱敏后限长。"""
    text = redact("".join(traceback.format_exception(type(error), error, error.__traceback__)), secrets)
    if len(text) > max_chars:
        return text[:max_chars] + f"\n...（堆栈已截断，原长 {len(text)} 字符）"
    return text


def safe_validation_error(error: Any, allowed_fields: Iterable[str] = (), limit: int = 8) -> str:
    """Pydantic ValidationError 的安全摘要。

    默认不输出 input/context/url：模型参数里可能夹带密钥。
    字段名只保留调用方允许的模型字段，其余标记为 OTHER。
    """
    allowed = {str(name) for name in allowed_fields}
    parts: list[str] = []
    try:
        errors = error.errors(include_input=False, include_context=False, include_url=False)
    except (AttributeError, TypeError):
        return type(error).__name__
    for item in errors[:limit]:
        loc = item.get("loc", ())
        labels = []
        for part in loc:
            if isinstance(part, str) and (not allowed or part in allowed):
                labels.append(part)
            elif isinstance(part, int):
                labels.append(str(part))
            else:
                labels.append("OTHER")
        kind = item.get("type", "invalid")
        parts.append(f"{'.'.join(labels) or 'root'}:{kind}")
    return ",".join(parts) if parts else type(error).__name__


def log_error(
    logger: logging.Logger,
    message: str,
    error: BaseException,
    *,
    secrets: Iterable[str] = (),
    level: int = logging.ERROR,
    include_traceback: bool | None = None,
    **fields: Any,
) -> None:
    """记录一条带上下文的异常日志。

    异常摘要始终记录；堆栈默认在配置开启时记录。
    取消任务属于正常控制流，不输出堆栈。
    """
    context = " ".join(f"{key}={safe_text(value, secrets, 700)}" for key, value in fields.items() if value is not None)
    summary = safe_text(f"{type(error).__name__}: {error}", secrets, 800)
    head = f"{message} | {context} | {summary}" if context else f"{message} | {summary}"
    if _TRACEBACK_MODE == "all":
        show = True
    elif _TRACEBACK_MODE == "none":
        show = False
    elif include_traceback is None:
        show = True
    else:
        show = include_traceback
    if show and not isinstance(error, asyncio.CancelledError):
        logger.log(level, "%s\n%s", head, format_exception(error, secrets=secrets))
    else:
        logger.log(level, "%s", head)
