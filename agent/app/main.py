import asyncio
import logging
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
from .message_handler import MessageHandler
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
    logger.info("[Agent] 服务启动 host=%s port=%d node=%s tool_calling=%s log_level=%s",
                settings.agent_host, settings.agent_port, settings.node_api_url,
                settings.tool_calling_enabled, logging.getLevelName(logging.getLogger().level))
    yield
    await task_manager.close()


app = FastAPI(title="MineMate Agent", version="0.1.0", lifespan=lifespan)
client = NodeClient(settings)
llm_client = LLMClient(settings)
message_handler = MessageHandler(settings, llm_client, tool_loop=ToolLoop(settings, llm_client, ToolClient(settings)))


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
