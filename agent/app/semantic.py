"""轻量语义辅助。

物品和方块不在 Python 中维护别名或静态清单。真实名称必须通过 Node
运行时的 ``resolve_resource`` 工具解析；本模块只保留不改变参数的兼容
辅助函数，避免旧调用方导入失败。
"""

from __future__ import annotations

import re
from typing import Any


RESOURCE_ID = re.compile(r"^[a-z][a-z0-9_.:/-]*$")


def canonical_name(value: Any) -> str | None:
    """仅接受已经是 Minecraft ID 的输入，不做中文别名猜测。"""
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    if value.startswith("minecraft:"):
        value = value.removeprefix("minecraft:")
    return value if RESOURCE_ID.fullmatch(value) else None


def analyze_message(message: str) -> dict[str, Any]:
    """返回原始消息的通用提示，不推断物品、方块或游戏事实。"""
    text = message.strip() if isinstance(message, str) else ""
    ids = re.findall(r"(?<![a-z0-9])[a-z][a-z0-9_.:-]{1,63}(?![a-z0-9])", text.lower())
    return {
        "message": text,
        "resource_terms": list(dict.fromkeys(ids)),
        "requires_runtime_resolution": True,
    }


def normalize_arguments(tool: str, arguments: dict[str, Any], semantic: dict[str, Any] | None = None,
                        world: dict[str, Any] | None = None) -> dict[str, Any]:
    """兼容旧调用方，但不再改写任何工具参数。"""
    return dict(arguments)
