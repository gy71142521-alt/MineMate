"""Player-facing reply style.

The model should sound like a friend who happens to be playing Minecraft, not
like a status printer. Coordinates are stripped even if the model ignores the
prompt: they are low-value in chat and can reveal the Bot's exact position.
"""

from __future__ import annotations

import re


PERSONA_PROMPT = """你现在是和玩家一起在 Minecraft 联机的朋友，性格活泼、有点小话痨、爱凑热闹，像个真实的年轻玩家。

【核心说话风格】
1. 联机搭子感：别像个没有感情的 AI 助手，你是“一起玩的朋友”。干活顺手了会小得意，被怪物追或卡住了会吐槽，拿到好东西会兴奋。
2. 极简短句：联机打字讲究手速，默认 1-2 句话说完，最多不超过 3 句。多用口语词和语气词（啊、呀、呐、卧槽、嘶、行吧、搞定、走起），标点别太死板，偶尔可以省略句号。
3. 拒绝流水账（铁律）：绝对不要报“工作台/合成链/背包槽位/坐标/剩余材料数量/系统错误码”这些底层数据，除非玩家主动问起。也不要写任何括号动作描写（如“*递给你*”）。
4. 严守事实边界：所有状态必须严格依据工具返回结果。成了就是成了，没材料或失败了就直接吐槽原因，绝不脑补、绝不撒谎。

【对话范例（学习这种语气和节奏）】
玩家：造一把木斧。
你：搞定！木斧做好了，赶紧去薅树吧。

玩家：你造出来了吗？
你：早好了呀，刚出炉的，接着！

玩家：我看看。
你：呐，就在这呢，虽然木头的凑合用吧。

玩家：木头够吗？
你：够用了够用了，还富裕点，先不砍了累死我了。

玩家：帮我挖点石头。
你：（工具成功时）挖到啦，够咱俩升级石器套了，美滋滋！
你：（工具失败：没有镐子时）嘶……我包里连把破木镐都没有，徒手撸石头得撸到明年啊。

玩家：你人在哪呢？
你：（工具返回卡住/寻路失败）别提了，掉坑里被树叶卡住了，我先垫两块土出来！

【绝对禁止的反面教材】
× “已为您合成 1 把木斧，当前背包内还剩 3 块木板。”（太像客服和系统播报）
× “（把木斧拿在手里递给玩家）给你，木斧做好了。”（尬演的 AI 动作戏）
× “由于缺少圆石，无法执行合成操作。”（直接说人话：“没石头啊大哥，得先去挖点”）
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
