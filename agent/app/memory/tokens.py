"""Conservative token estimation without a tokenizer dependency.

The estimator intentionally over-estimates CJK text and ASCII prose so that
compaction starts before a provider rejects the request. It is not billing
grade; provider usage, when available, can be stored separately.
"""

from __future__ import annotations

import json
import math
from typing import Any


def estimate_text(text: str) -> int:
    if not text:
        return 0
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    non_ascii = len(text) - ascii_chars
    # ~4 ASCII chars/token; CJK is close to 1 token/char in common tokenizers.
    return max(1, math.ceil(ascii_chars / 4 + non_ascii))


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return str(content)


def estimate_message(message: dict[str, Any]) -> int:
    """Estimate one Chat Completions message including tool-call payloads."""
    total = 4  # role/framing overhead
    total += estimate_text(str(message.get("role", "")))
    total += estimate_text(_content_text(message.get("content")))
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            total += 4
            if isinstance(call, dict):
                total += estimate_text(str(call.get("id", "")))
                function = call.get("function")
                if isinstance(function, dict):
                    total += estimate_text(str(function.get("name", "")))
                    total += estimate_text(_content_text(function.get("arguments")))
                else:
                    total += estimate_text(_content_text(function))
    return total


def estimate_messages(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_message(message) for message in messages)


def estimate_tools(tools: Any) -> int:
    if not tools:
        return 0
    try:
        text = json.dumps(tools, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        text = str(tools)
    return estimate_text(text)


def truncate_text(text: str, limit: int, marker: str = "…（已截断）") -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + marker
