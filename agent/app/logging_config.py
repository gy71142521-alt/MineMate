"""Agent 日志初始化。

默认输出到 stderr，格式包含时间、级别、模块、请求 ID 和消息。
LOG_LEVEL 控制级别；LOG_TRACEBACK=none|errors|all 控制堆栈与模型输出预览。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable

from . import diagnostics


_CONFIGURED = False
_HANDLER_TAG = "_minemate_handler"


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = diagnostics.current_request_id()
        return True


class RedactingFormatter(logging.Formatter):
    """最后一道防线：即使某处直接记录了异常，也替换已知密钥。"""

    def format(self, record: logging.LogRecord) -> str:
        return diagnostics.redact(super().format(record))


def _resolve_level(value: str | None) -> int:
    name = (value or os.environ.get("LOG_LEVEL", "INFO")).strip().upper()
    level = getattr(logging, name, None)
    return level if isinstance(level, int) else logging.INFO


def _traceback_setting() -> str:
    return os.environ.get("LOG_TRACEBACK", "true").strip().lower()


def configure_logging(*, secrets: Iterable[str] = (), level: str | None = None) -> None:
    """幂等初始化；重复调用只更新密钥和级别，不重复添加 handler。"""
    global _CONFIGURED
    diagnostics.set_secrets(secrets)
    diagnostics.set_traceback_mode(_traceback_setting())
    resolved = _resolve_level(level)

    root = logging.getLogger()
    if not _CONFIGURED:
        handler = logging.StreamHandler()
        handler.setFormatter(RedactingFormatter(
            "%(asctime)s %(levelname)-7s %(name)s [rid=%(request_id)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        handler.addFilter(RequestIdFilter())
        setattr(handler, _HANDLER_TAG, True)
        root.addHandler(handler)
        _CONFIGURED = True
    else:
        for handler in root.handlers:
            if getattr(handler, _HANDLER_TAG, False):
                handler.addFilter(RequestIdFilter())

    root.setLevel(resolved)
    # 只约束本项目命名空间，避免把第三方库全部调到 DEBUG。
    for name in ("minemate",):
        logging.getLogger(name).setLevel(resolved)
    # httpx 的每条请求 INFO 会淹没业务日志；连接问题仍由 Agent 自己记录。
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(max(resolved, logging.WARNING))
