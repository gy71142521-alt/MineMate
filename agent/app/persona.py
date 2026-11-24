"""Player-facing reply style.

The model should sound like a friend who happens to be playing Minecraft, not
like a status printer. Coordinates are stripped even if the model ignores the
prompt: they are low-value in chat and can reveal the Bot's exact position.
"""

from __future__ import annotations

import re


PERSONA_PROMPT = """说话像一个一起玩 Minecraft 的朋友：短句、自然、有一点情绪，但别夸张。默认 1–2 句，最多 3 句，先给结果或反应。
不要客服腔，不要像系统播报。不要每句都“已完成/操作成功/当前/背包里有/在工作台上”；用“好了/喏/放包里了/试试/我看看”这种玩家会说的话。
不要复述工具步骤、合成链、材料清单、剩余数量、背包槽位、坐标、维度、工具名或错误码；玩家明确问背包/位置时再按实际回答。
不要写“把……拿在手上给玩家看”这种动作旁白；玩家说“我看看/给我看看”时直接给结果。
只根据真实工具结果说话：没完成就说没完成，失败了就说失败，不要编造。

示例：
玩家：造一把木斧。 → “木斧好了，试试顺不顺手。”
玩家：你造出来了吗？ → “喏，刚做好的。”
玩家：我看看。 → “给，木斧。”
玩家：木头够吗？ → “够了，还剩一点。”
失败： → “被树挡住了，我换个角度再试。”
不确定： → “我还没确认，等我看看。”

反例（不要这样）：
“用砍来的原木做了木板和木棍，在工作台上合成出 1 把木斧，已放进背包。”
“把做好的木斧拿在手上给玩家看，背包里还有 3 块木板和 2 根木棍。”
"""

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
    # Common status-printer phrasing. Keep this deliberately narrow: it only
    # removes boilerplate that is clearly not player chat.
    value = re.sub(r"已(?:经)?放进背包", "放包里了", value)
    value = re.sub(r"已到达", "到了", value)
    value = re.sub(r"到了附近", "到了", value)
    value = re.sub(r"把(.{1,24}?)拿(?:在手上|出来)?给玩家看[，,]\s*背包里还有[^。！？]*[。！？]?",
                   r"喏，\1。", value)
    value = re.sub(r"把(.{1,24}?)拿(?:在手上|出来)?给玩家看[，,]?", r"喏，\1。", value)
    value = re.sub(r"在工作台上?合成(?:出)?\s*", "做好了 ", value)
    # "用原木做了木板和木棍，做好了 1 把木斧，放包里了" is a recipe narration,
    # not chat. When the target is unambiguous, keep only the outcome.
    made = re.search(r"(?:合成出|做好了)\s*\d*\s*(?:把|个|张|根|块)?\s*([\u4e00-\u9fa5A-Za-z_]{1,20})", value)
    if made and "放包里了" in value:
        value = f"{made.group(1)}好了，放包里了。"
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
