import logging
import time
import uuid

from pydantic import ValidationError

from . import diagnostics
from .config import Settings
from .intent import MessageRequest, parse_intent, redact
from .llm_client import LLMClient, LLMError
from .persona import humanize_reply


logger = logging.getLogger("minemate.agent")
INTENT_FIELDS = {
    "intent", "arguments", "reply", "x", "y", "z",
    "conversation", "query_status", "follow", "goto", "stop", "unsupported", "clarification",
}
# 这些 502 是“模型输出不符合约束”，模块日志已有阶段和字段；无需重复打印整段堆栈。
VALIDATION_CODES = {"INVALID_TOOL_CALL", "LLM_INVALID_INTENT", "LLM_INVALID_RESPONSE", "LLM_OUTPUT_TRUNCATED"}


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
        self.active_players: set[str] = set()
        self.tool_loop = tool_loop

    async def handle(self, request: MessageRequest) -> dict:
        now = self.clock()
        config = self.settings
        started = time.monotonic()
        token = None
        if diagnostics.current_request_id() == "-":
            token = diagnostics.set_request_id(uuid.uuid4().hex[:8])
        try:
            if now - self.window_start >= 60:
                self.window_start, self.window_count = now, 0
            self.players = {name: expiry for name, expiry in self.players.items() if expiry > now}
            name = request.player.lower()
            if self.active >= config.llm_max_concurrent:
                raise LLMError(429, "AGENT_BUSY", "语言模型正在忙，请稍后再试。")
            # One turn per player: the memory log must not have two open turns.
            if name in self.active_players:
                raise LLMError(429, "PLAYER_BUSY", "上一条消息仍在处理中，请稍后再试。")
            if self.window_count >= config.llm_requests_per_minute or name in self.players or len(self.players) >= 512:
                raise LLMError(429, "AGENT_RATE_LIMITED", "消息过于频繁，请稍后再试。")
            self.active += 1
            self.active_players.add(name)
            self.window_count += 1
            self.players[name] = now + config.llm_player_cooldown
            logger.info("[Agent] 开始处理 player=%s message_len=%d tool_mode=%s",
                        request.player, len(request.message), config.tool_calling_enabled)
            try:
                if config.tool_calling_enabled:
                    if self.tool_loop is None:
                        raise LLMError(503, "TOOLS_NOT_CONFIGURED", "工具调用模块尚未就绪。")
                    return await self.tool_loop.run(request)
                text = await self.llm.complete(request.player, request.message)
                try:
                    result = parse_intent(text).model_dump()
                except (ValueError, TypeError, RecursionError) as error:
                    fields = (diagnostics.safe_validation_error(error, INTENT_FIELDS)
                              if isinstance(error, ValidationError) else "-")
                    logger.warning("[Agent] 模型意图无效 fields=%s error=%s preview=%s",
                                   fields, type(error).__name__,
                                   diagnostics.model_preview(text, (config.llm_api_key,), 700) or "-")
                    if isinstance(error, ValidationError):
                        raise LLMError(502, "LLM_INVALID_INTENT", "模型意图格式无效，请换种说法再试。") from None
                    raise LLMError(502, "LLM_INVALID_INTENT", "模型意图格式无效，请换种说法再试。") from error
                # 对动作意图使用确定性的说明，避免模型误称已经执行。
                replies = {
                    "follow": "好，我跟着你；你说“跟随我”我就动。",
                    "goto": "好，我记下方向了；你说“前往”加坐标我就过去。",
                    "stop": "好，我停；你说“停止”我就马上停。",
                    "query_status": "想看我状态就发“状态”，我告诉你。",
                    "unsupported": "这个我暂时还不会，换个玩法？",
                }
                result["reply"] = redact(
                    humanize_reply(replies.get(result["intent"], result["reply"])),
                    config.llm_api_key,
                )
                logger.info("[Agent] 处理完成 intent=%s elapsed_ms=%d",
                            result["intent"], int((time.monotonic() - started) * 1000))
                return result
            except LLMError as error:
                diagnostics.log_error(
                    logger, "[Agent] 消息处理失败", error,
                    secrets=(config.llm_api_key,),
                    level=logging.ERROR if error.status_code >= 500 else logging.WARNING,
                    include_traceback=error.status_code >= 500 and error.code not in VALIDATION_CODES,
                    player=request.player, code=error.code, status=error.status_code,
                    message_len=len(request.message), active=self.active,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )
                raise
            except Exception as error:
                diagnostics.log_error(
                    logger, "[Agent] 消息处理异常", error,
                    secrets=(config.llm_api_key,), level=logging.ERROR,
                    player=request.player, message_len=len(request.message),
                    active=self.active, elapsed_ms=int((time.monotonic() - started) * 1000),
                )
                raise LLMError(502, "AGENT_ERROR", "消息处理失败，请稍后再试。") from error
            finally:
                self.active -= 1
                self.active_players.discard(name)
        finally:
            if token is not None:
                diagnostics.reset_request_id(token)
