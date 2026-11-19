import logging
import time
from typing import Any

import httpx

from .config import Settings


logger = logging.getLogger("minemate.node")


class NodeApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class NodeClient:
    def __init__(self, settings: Settings, client_factory=httpx.AsyncClient):
        self.settings = settings
        self.client_factory = client_factory

    async def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        started = time.monotonic()
        try:
            async with self.client_factory(base_url=self.settings.node_api_url, timeout=self.settings.node_api_timeout_seconds) as client:
                response = await client.request(method, path, json=payload)
        except httpx.TimeoutException as error:
            logger.warning("[Node] 请求超时 method=%s path=%s timeout=%.1fs elapsed_ms=%d",
                           method, path, self.settings.node_api_timeout_seconds, int((time.monotonic() - started) * 1000))
            raise NodeApiError(504, "NODE_TIMEOUT", "Node.js 服务请求超时。") from error
        except httpx.RequestError as error:
            logger.warning("[Node] 连接失败 method=%s path=%s error=%s",
                           method, path, type(error).__name__)
            raise NodeApiError(503, "NODE_UNAVAILABLE", "Node.js 服务不可用。") from error

        try:
            body = response.json()
        except ValueError as error:
            logger.warning("[Node] 响应不是 JSON method=%s path=%s status=%d bytes=%d",
                           method, path, response.status_code, len(response.content))
            raise NodeApiError(502, "INVALID_NODE_RESPONSE", "Node.js 返回了无效响应。") from error
        if not isinstance(body, dict) or not {"success", "data", "error"}.issubset(body):
            logger.warning("[Node] 响应格式无效 method=%s path=%s status=%d body_type=%s keys=%s",
                           method, path, response.status_code, type(body).__name__,
                           sorted(body)[:8] if isinstance(body, dict) else "-")
            raise NodeApiError(502, "INVALID_NODE_RESPONSE", "Node.js 返回格式无效。")
        if not body["success"]:
            error = body.get("error") if isinstance(body.get("error"), dict) else {}
            code, message = str(error.get("code", "NODE_ERROR")), str(error.get("message", "Node.js 请求失败。"))
            status = response.status_code if response.status_code >= 400 else 502
            logger.warning("[Node] 请求失败 method=%s path=%s status=%d code=%s elapsed_ms=%d",
                           method, path, status, code, int((time.monotonic() - started) * 1000))
            raise NodeApiError(status, code, message)
        logger.debug("[Node] 请求完成 method=%s path=%s status=%d elapsed_ms=%d",
                     method, path, response.status_code, int((time.monotonic() - started) * 1000))
        return body
