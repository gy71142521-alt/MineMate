"""Player-facing reply style.

The model should sound like a friend who happens to be playing Minecraft, not
like a status printer. Coordinates are stripped even if the model ignores the
prompt: they are low-value in chat and can reveal the Bot's exact position.
"""

from __future__ import annotations

import re


PERSONA_PROMPT = """说话像一个一起玩 Minecraft 的朋友：短句、自然、有一点情绪，但别夸张。
不要客服腔，不要每句都“已完成”“操作成功”；用“挖到了”“没找到”“我过去看看”这种玩家会说的话。
不要主动报坐标、维度、工具名、错误码或内部状态；玩家问你在哪时，别报具体坐标；可以说“我在这边”，需要精确位置让他发“状态”。
只根据真实工具结果说话：没完成就说没完成，失败了就说失败，不要编造。"""

MAX_REPLY_CHARS = 500

_COORDINATE_PATTERNS = (
    # X=1 Y=64 Z=2
    re.compile(r"(?i)\b[xyz]\s*[=:：]\s*-?\d+(?:\.\d+)?"
               r"(?:\s*[,，]?\s*[xyz]\s*[=:：]\s*-?\d+(?:\.\d+)?){2}\b"),
    # 坐标是 189 64 190 / 位置：189,64,190
    re.compile(r"(?:坐标|位置)\s*(?:是|为|在)?\s*[:：]?\s*"
               r"-?\d+(?:\.\d+)?(?:\s*[,，]\s*|\s+)"
               r"-?\d+(?:\.\d+)?(?:\s*[,，]\s*|\s+)"
               r"-?\d+(?:\.\d+)?"),
    # 在 189 64 190
    re.compile(r"在\s*-?\d+(?:\.\d+)?\s+"
               r"-?\d+(?:\.\d+)?\s+"
               r"-?\d+(?:\.\d+)?"),
    # (189, 64, 190)
    re.compile(r"\(\s*-?\d+(?:\.\d+)?\s*[,，]\s*"
               r"-?\d+(?:\.\d+)?\s*[,，]\s*"
               r"-?\d+(?:\.\d+)?\s*\)"),
    # 189, 64, 190
    re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?\s*[,，]\s*"
               r"-?\d+(?:\.\d+)?\s*[,，]\s*"
               r"-?\d+(?:\.\d+)?"),
    # 189 64 190
    re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?\s+"
               r"-?\d+(?:\.\d+)?\s+"
               r"-?\d+(?:\.\d+)?(?![\w.])"),
)


def strip_coordinates(text: str) -> str:
    value = text
    for pattern in _COORDINATE_PATTERNS:
        value = pattern.sub("附近", value)
    value = re.sub(r"\s*附近\s*", "附近", value)
    value = re.sub(r"(?:我的)?(?:坐标|位置)(?:是|为|在)?\s*[:：]?\s*附近", "就在附近", value)
    value = re.sub(r"放(?:在|到)?\s*附近\s*(?:了|啦)?", "放好了", value)
    value = re.sub(r"到(?:了)?\s*附近", "到了", value)
    value = re.sub(r"(?<!就)在\s*附近", "在这边", value)
    value = re.sub(r"(附近)(?:附近)+", r"\1", value)
    value = re.sub(r"^附近(?=[，,。！？])", "就在附近", value)
    return value


def humanize_reply(text: object, *, max_length: int = MAX_REPLY_CHARS) -> str:
    """Normalize a player-facing line and remove volunteered coordinates."""
    if not isinstance(text, str):
        text = str(text)
    value = " ".join(text.split())
    value = strip_coordinates(value)
    value = " ".join(value.split()).strip(" ，,")
    if not value:
        return "嗯，我在。"
    return value[:max_length]
