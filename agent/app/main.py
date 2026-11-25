import asyncio
import logging
import re
import time
import uuid
from contextlib import suppress
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import diagnostics
from .config import load_settings
from .logging_config import configure_logging
from .node_client import NodeApiError, NodeClient
from .schemas import GotoRequest, MovementRequest
from .intent import MessageRequest
from .llm_client import LLMClient, LLMError
from .memory import (
    AdaptiveSummarizer,
    MemoryConfig,
    MemoryManager,
    ModelSummarizer,
    SQLiteEventStore,
)
from .message_handler import MessageHandler
from .orchestration import AgentRuntime
from .tool_client import ToolClient
from .tool_loop import ToolLoop
from .execution.manager import TaskManager
from .execution.models import TaskRequest
from skills import SkillManager, SkillRequest
from skills.catalog import create_skill_registry


settings = load_settings()
logger = logging.getLogger("minemate.http")
task_manager = TaskManager(ToolClient(settings))
skill_registry = create_skill_registry()
# 所有后台 Skill 与显式工具任务共享执行槽和 Node 租约。
skill_manager = SkillManager(skill_registry, task_manager)


@asynccontextmanager
async def lifespan(_app):
    configure_logging(secrets=(settings.llm_api_key,))
    logger.info("[Agent] 服务启动 host=%s port=%d node=%s tool_calling=%s memory=%s log_level=%s",
                settings.agent_host, settings.agent_port, settings.node_api_url,
                settings.tool_calling_enabled, bool(memory_manager),
                logging.getLevelName(logging.getLogger().level))
    if memory_manager is not None:
        try:
            await memory_manager.startup()
        except Exception as error:
            # Memory is auxiliary: a broken disk must not stop the game bot.
            logger.error("[Memory] 初始化失败，将在本轮禁用持久记忆：%s", type(error).__name__)
    yield
    await task_manager.close()
    if memory_manager is not None:
        await memory_manager.close()


app = FastAPI(title="MineMate Agent", version="0.1.0", lifespan=lifespan)
client = NodeClient(settings)
llm_client = LLMClient(settings)
memory_manager: MemoryManager | None = None
if settings.memory_enabled:
    memory_config = MemoryConfig(
        context_window=settings.memory_context_window,
        threshold_ratio=settings.memory_compaction_threshold,
        retain_turns=settings.memory_retain_turns,
        max_summary_chars=settings.memory_max_summary_chars,
        max_tool_result_chars=settings.memory_max_tool_result_chars,
        session_idle_days=settings.memory_session_idle_days,
        auto_compact=settings.memory_auto_compact,
    )
    memory_summarizer = (
        AdaptiveSummarizer(ModelSummarizer(llm_client))
        if settings.llm_api_key and settings.llm_model
        else AdaptiveSummarizer(None)
    )
    memory_manager = MemoryManager(
        SQLiteEventStore(settings.memory_db_path), memory_config, memory_summarizer,
    )
tool_client = ToolClient(settings)
legacy_tool_loop = ToolLoop(settings, llm_client, tool_client, memory=memory_manager)
runtime = (
    AgentRuntime(settings, llm_client, tool_client, memory=memory_manager,
                 skill_manager=skill_manager)
    if settings.execution_mode == "planned"
    else None
)
# `planned` uses the planner/runtime; `legacy` preserves the old single-loop behavior.
message_handler = MessageHandler(
    settings, llm_client, tool_loop=legacy_tool_loop, runtime=runtime)


def success(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def failure(code: str, message: str) -> dict[str, Any]:
    return {"success": False, "data": None, "error": {"code": code, "message": message}}


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    # FastAPI 默认会返回另一种结构，这里统一为通信协议格式。
    logger.warning("[HTTP] 请求参数无效 path=%s errors=%d fields=%s",
                   request.url.path, len(error.errors()),
                   diagnostics.safe_text(",".join(str(item.get("loc", "?")) for item in error.errors()[:6]), (), 240))
    return JSONResponse(status_code=422, content=failure("INVALID_ARGUMENT", "请求参数无效。"))


@app.exception_handler(LLMError)
async def service_error(_request, error: LLMError) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content=failure(error.code, error.message))


@app.post("/api/agent/tasks", status_code=202)
async def submit_task(request: Request) -> JSONResponse:
    # 后台请求也限制流式请求体，不能只相信 Content-Length。
    from pydantic import ValidationError
    import json
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 16384:
            return JSONResponse(status_code=413, content=failure("BODY_TOO_LARGE", "任务请求不能超过 16 KiB。"))
    try:
        task = TaskRequest.model_validate(json.loads(raw))
    except (ValidationError, ValueError, TypeError, RecursionError) as error:
        detail = diagnostics.safe_validation_error(error, {"player", "request_id", "steps", "verify_inventory", "timeout_seconds", "max_tool_calls"}) if isinstance(error, ValidationError) else type(error).__name__
        logger.warning("[HTTP] 后台任务参数无效 error=%s fields=%s", type(error).__name__, detail)
        return JSONResponse(status_code=422, content=failure("INVALID_ARGUMENT", "任务参数无效。"))
    return JSONResponse(status_code=202, content=success(await task_manager.submit(task)))


@app.get("/api/agent/skills")
async def list_skills() -> dict:
    return success({"skills": skill_registry.list()})


@app.post("/api/agent/skills/execute", status_code=202)
async def execute_skill(request: Request) -> JSONResponse:
    from pydantic import ValidationError
    import json
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 16384:
            return JSONResponse(status_code=413, content=failure("BODY_TOO_LARGE", "Skill 请求不能超过 16 KiB。"))
    try:
        skill = SkillRequest.model_validate(json.loads(raw))
    except (ValidationError, ValueError, TypeError, RecursionError) as error:
        detail = diagnostics.safe_validation_error(error, {"player", "request_id", "skill", "arguments", "timeout_seconds", "max_tool_calls"}) if isinstance(error, ValidationError) else type(error).__name__
        logger.warning("[HTTP] Skill 请求参数无效 error=%s fields=%s", type(error).__name__, detail)
        return JSONResponse(status_code=422, content=failure("INVALID_ARGUMENT", "Skill 请求参数无效。"))
    return JSONResponse(status_code=202, content=success(await skill_manager.submit(skill)))


@app.get("/api/agent/skills/executions/{execution_id}")
async def skill_status(execution_id: str, player: str) -> dict:
    return success(skill_manager.view(execution_id, player))


@app.post("/api/agent/skills/executions/{execution_id}/cancel")
async def cancel_skill(execution_id: str, request: MovementRequest) -> dict:
    return success(await skill_manager.cancel(execution_id, request.username))


@app.get("/api/agent/tasks/{task_id}")
async def task_status(task_id: str, player: str) -> dict:
    return success(task_manager.view(task_id, player))


@app.post("/api/agent/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: MovementRequest) -> dict:
    return success(await task_manager.cancel(task_id, request.username))


async def forward(call: Callable[[], Awaitable[dict[str, Any]]]) -> JSONResponse:
    try:
        body = await call()
        return JSONResponse(status_code=200, content=body)
    except NodeApiError as error:
        logger.warning("[HTTP] 转发 Node 请求失败 code=%s status=%d", error.code, error.status_code)
        return JSONResponse(status_code=error.status_code, content=failure(error.code, error.message))
    except Exception as error:
        # 不把内部堆栈或环境信息暴露给调用方，但服务端日志必须保留因果链。
        diagnostics.log_error(logger, "[HTTP] 转发 Node 请求异常", error,
                              secrets=(settings.llm_api_key,), level=logging.ERROR)
        return JSONResponse(status_code=502, content=failure("NODE_ERROR", "Node.js 请求处理失败。"))


@app.get("/health")
async def health() -> dict[str, Any]:
    return success({"service": "ok", "node_api": settings.node_api_url})


@app.get("/api/agent/memory/{player}")
async def memory_status(player: str) -> dict:
    if memory_manager is None:
        raise LLMError(503, "MEMORY_DISABLED", "记忆模块未启用。")
    if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", player):
        raise LLMError(400, "INVALID_ARGUMENT", "玩家名无效。")
    return success(await memory_manager.stats(player))


@app.post("/api/agent/memory/{player}/reset")
async def memory_reset(player: str) -> dict:
    if memory_manager is None:
        raise LLMError(503, "MEMORY_DISABLED", "记忆模块未启用。")
    if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", player):
        raise LLMError(400, "INVALID_ARGUMENT", "玩家名无效。")
    await memory_manager.reset(player)
    return success({"player": player, "reset": True})


@app.post("/api/agent/message")
async def agent_message(request: MessageRequest, http_request: Request) -> JSONResponse:
    token = diagnostics.set_request_id(uuid.uuid4().hex[:8])
    started = time.monotonic()
    logger.info("[HTTP] /api/agent/message 开始 player=%s message_len=%d generation=%s",
                request.player, len(request.message), request.generation)
    work = asyncio.create_task(message_handler.handle(request))
    try:
        while not work.done():
            await asyncio.wait({work}, timeout=0.2)
            if not work.done() and await http_request.is_disconnected():
                work.cancel()
                with suppress(asyncio.CancelledError):
                    await work
                logger.warning("[HTTP] 调用方断开，已取消 player=%s elapsed_ms=%d",
                               request.player, int((time.monotonic() - started) * 1000))
                return JSONResponse(status_code=499, content=failure("CLIENT_DISCONNECTED", "调用方已断开，已请求取消未完成操作。"))
        data = await work
        logger.info("[HTTP] /api/agent/message 完成 player=%s status=200 elapsed_ms=%d",
                    request.player, int((time.monotonic() - started) * 1000))
        return JSONResponse(status_code=200, content=success(data))
    except LLMError as error:
        logger.log(
            logging.ERROR if error.status_code >= 500 else logging.WARNING,
            "[HTTP] /api/agent/message 失败 player=%s status=%d code=%s elapsed_ms=%d",
            request.player, error.status_code, error.code, int((time.monotonic() - started) * 1000),
        )
        return JSONResponse(status_code=error.status_code, content=failure(error.code, error.message))
    finally:
        if not work.done():
            work.cancel()
            with suppress(asyncio.CancelledError):
                await work
        diagnostics.reset_request_id(token)


@app.post("/api/agent/steer")
async def agent_steer(request: MessageRequest) -> JSONResponse:
    """Insert a player instruction into the next step of a running turn."""
    token = diagnostics.set_request_id(uuid.uuid4().hex[:8])
    try:
        accepted = await message_handler.steer(request)
        logger.info("[HTTP] /api/agent/steer player=%s accepted=%s", request.player, accepted)
        return JSONResponse(status_code=200, content=success({"accepted": accepted}))
    finally:
        diagnostics.reset_request_id(token)


@app.get("/api/game/world")
async def world() -> JSONResponse:
    return await forward(lambda: client.request("GET", "/api/world"))


@app.post("/api/game/follow")
async def follow(request: MovementRequest) -> JSONResponse:
    return await forward(lambda: client.request("POST", "/api/movement/follow", request.model_dump()))


@app.post("/api/game/goto")
async def goto(request: GotoRequest) -> JSONResponse:
    return await forward(lambda: client.request("POST", "/api/movement/goto", request.model_dump()))


@app.post("/api/game/stop")
async def stop(request: MovementRequest) -> JSONResponse:
    return await forward(lambda: client.request("POST", "/api/movement/stop", request.model_dump()))


if __name__ == "__main__":
    import uvicorn

    configure_logging(secrets=(settings.llm_api_key,))
    uvicorn.run("app.main:app", host=settings.agent_host, port=settings.agent_port, reload=False)
