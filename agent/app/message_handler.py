import logging
import time

from .config import Settings
from .intent import MessageRequest, parse_intent, redact
from .llm_client import LLMClient, LLMError


logger = logging.getLogger("minemate.agent")


class MessageHandler:
    """单进程内有界准入，不排队，不持有游戏控制接口或对话记忆。"""

    def __init__(self, settings: Settings, llm: LLMClient, clock=time.monotonic, tool_loop=None):
        self.settings = settings
        self.llm = llm
        self.clock = clock
        self.active = 0
        self.window_start = clock()
        self.window_count = 0
        self.players: dict[str, float] = {}
        self.tool_loop = tool_loop

    async def handle(self, request: MessageRequest) -> dict:
        now = self.clock()
        config = self.settings
        if now - self.window_start >= 60:
            self.window_start, self.window_count = now, 0
        self.players = {name: expiry for name, expiry in self.players.items() if expiry > now}
        name = request.player.lower()
        if self.active >= config.llm_max_concurrent:
            raise LLMError(429, "AGENT_BUSY", "语言模型正在忙，请稍后再试。")
        if self.window_count >= config.llm_requests_per_minute or name in self.players or len(self.players) >= 512:
            raise LLMError(429, "AGENT_RATE_LIMITED", "消息过于频繁，请稍后再试。")
        self.active += 1
        self.window_count += 1
        self.players[name] = now + config.llm_player_cooldown
        try:
            if config.tool_calling_enabled:
                if self.tool_loop is None:
                    raise LLMError(503, "TOOLS_NOT_CONFIGURED", "工具调用模块尚未就绪。")
                return await self.tool_loop.run(request)
            text = await self.llm.complete(request.player, request.message)
            try:
                result = parse_intent(text).model_dump()
            except (ValueError, TypeError, RecursionError):
                raise LLMError(502, "LLM_INVALID_INTENT", "模型意图格式无效，请换种说法再试。") from None
            # 对动作意图使用确定性的说明，避免模型误称已经执行。
            replies = {
                "follow": "已识别跟随意图，尚未执行；如需现在跟随，请发送“跟随我”。",
                "goto": "已识别坐标导航意图，尚未执行；如需现在导航，请发送“前往 X Y Z”。",
                "stop": "已识别停止意图，尚未执行；如需立即停止，请发送“停止”。",
                "query_status": "已识别状态查询意图。请发送“状态”查询 Bot 自身状态；当前未读取玩家状态。",
                "unsupported": "这个操作目前尚不支持，请尝试聊天或已有指令。",
            }
            result["reply"] = redact(replies.get(result["intent"], result["reply"]), config.llm_api_key)
            return result
        except LLMError as error:
            logger.warning("[Agent] 消息处理失败：%s", error.code)
            raise
        except Exception:
            logger.error("[Agent] 消息处理异常。")
            raise LLMError(502, "AGENT_ERROR", "消息处理失败，请稍后再试。") from None
        finally:
            self.active -= 1
