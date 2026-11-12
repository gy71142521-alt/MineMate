import os
from typing import Any, Awaitable, Callable

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import load_settings
from .node_client import NodeApiError, NodeClient
from .schemas import GotoRequest, MovementRequest


settings = load_settings()
app = FastAPI(title="MineMate Agent", version="0.1.0")
client = NodeClient(settings)


def success(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def failure(code: str, message: str) -> dict[str, Any]:
    return {"success": False, "data": None, "error": {"code": code, "message": message}}


@app.exception_handler(RequestValidationError)
async def validation_error(_request, _error) -> JSONResponse:
    # FastAPI 默认会返回另一种结构，这里统一为通信协议格式。
    return JSONResponse(status_code=422, content=failure("INVALID_ARGUMENT", "请求参数无效。"))


async def forward(call: Callable[[], Awaitable[dict[str, Any]]]) -> JSONResponse:
    try:
        body = await call()
        return JSONResponse(status_code=200, content=body)
    except NodeApiError as error:
        return JSONResponse(status_code=error.status_code, content=failure(error.code, error.message))
    except Exception:
        # 不把内部堆栈或环境信息暴露给调用方。
        return JSONResponse(status_code=502, content=failure("NODE_ERROR", "Node.js 请求处理失败。"))


@app.get("/health")
async def health() -> dict[str, Any]:
    return success({"service": "ok", "node_api": settings.node_api_url})


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

    uvicorn.run("app.main:app", host=settings.agent_host, port=settings.agent_port, reload=False)
