from typing import Any

import httpx

from .config import Settings


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
        try:
            async with self.client_factory(base_url=self.settings.node_api_url, timeout=self.settings.node_api_timeout_seconds) as client:
                response = await client.request(method, path, json=payload)
        except httpx.TimeoutException as exc:
            raise NodeApiError(504, "NODE_TIMEOUT", "Node.js 服务请求超时。") from exc
        except httpx.RequestError as exc:
            raise NodeApiError(503, "NODE_UNAVAILABLE", "Node.js 服务不可用。") from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise NodeApiError(502, "INVALID_NODE_RESPONSE", "Node.js 返回了无效响应。") from exc
        if not isinstance(body, dict) or not {"success", "data", "error"}.issubset(body):
            raise NodeApiError(502, "INVALID_NODE_RESPONSE", "Node.js 返回格式无效。")
        if not body["success"]:
            error = body.get("error") if isinstance(body.get("error"), dict) else {}
            raise NodeApiError(response.status_code if response.status_code >= 400 else 502,
                               str(error.get("code", "NODE_ERROR")),
                               str(error.get("message", "Node.js 请求失败。")))
        return body
