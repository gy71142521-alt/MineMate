import asyncio
import json
import logging
import math
import re
import time
from typing import Any

import httpx

from . import diagnostics
from .config import Settings
from .llm_client import LLMError


logger = logging.getLogger("minemate.tool_client")


def validate(schema: dict, value: Any) -> bool:
    """校验 Node 注册表使用的有限 Schema 子集，不独立维护游戏参数定义。"""
    if "enum" in schema and value not in schema["enum"]:
        return False
    kind = schema.get("type")
    if kind == "boolean":
        return type(value) is bool
    if kind == "object":
        return isinstance(value, dict) and all(key in value for key in schema["required"]) and all(
            key in schema["properties"] and validate(schema["properties"][key], item) for key, item in value.items())
    if kind in ("number", "integer"):
        return type(value) in (int, float) and math.isfinite(value) and (kind != "integer" or value == int(value)) and schema["minimum"] <= value <= schema["maximum"]
    if kind == "string":
        return isinstance(value, str) and schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 128) and (not schema.get("pattern") or re.search(schema["pattern"], value) is not None)
    return False


def validate_schema(schema: Any, depth: int = 0) -> bool:
    if depth > 6 or not isinstance(schema, dict):
        return False
    kind = schema.get("type")
    if kind == "boolean":
        return True
    if kind == "object":
        properties, required = schema.get("properties"), schema.get("required")
        return isinstance(properties, dict) and len(properties) <= 20 and isinstance(required, list) and schema.get("additionalProperties") is False and all(
            isinstance(name, str) and name in properties for name in required) and all(validate_schema(value, depth + 1) for value in properties.values())
    if kind in ("number", "integer"):
        return all(type(schema.get(key)) in (int, float) and math.isfinite(schema[key]) for key in ("minimum", "maximum")) and schema["minimum"] <= schema["maximum"]
    if kind == "string":
        return ("pattern" not in schema or isinstance(schema["pattern"], str)) and ("enum" not in schema or isinstance(schema["enum"], list))
    return False


class ToolClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    async def request(self, method: str, path: str, payload: dict | None = None) -> Any:
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.settings.tool_api_timeout):
                async with httpx.AsyncClient(base_url=self.settings.node_api_url, timeout=self.settings.tool_api_timeout,
                                             transport=self.transport, follow_redirects=False) as client:
                    async with client.stream(method, path, json=payload) as response:
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw) > 131072:
                                raise LLMError(502, "INVALID_TOOL_RESPONSE", "游戏工具响应过大。")
                        body = json.loads(raw)
                        if response.status_code != 200 or not isinstance(body, dict) or body.get("success") is not True or body.get("error") is not None:
                            code = body.get("error", {}).get("code") if isinstance(body, dict) and isinstance(body.get("error"), dict) else None
                            logger.warning("[Tools] Node 拒绝请求 method=%s path=%s status=%d code=%s bytes=%d elapsed_ms=%d",
                                           method, path, response.status_code, code or "-", len(raw),
                                           int((time.monotonic() - started) * 1000))
                            if code in {"STALE_TASK", "STALE_INSTANCE", "STALE_LEASE", "FORBIDDEN", "BUSY", "BOT_NOT_READY", "REQUEST_NOT_FOUND"}:
                                raise LLMError(response.status_code, code, "Node 游戏控制状态已变化或请求被拒绝。")
                            raise LLMError(502, "NODE_TOOL_ERROR", "Node 工具服务拒绝请求，可能已停止或状态已变化。")
                        logger.debug("[Tools] Node 请求完成 method=%s path=%s status=%d bytes=%d elapsed_ms=%d",
                                     method, path, response.status_code, len(raw),
                                     int((time.monotonic() - started) * 1000))
                        return body["data"]
        except LLMError:
            raise
        except (TimeoutError, httpx.TimeoutException) as error:
            logger.warning("[Tools] Node 请求超时 method=%s path=%s timeout=%.1fs elapsed_ms=%d",
                           method, path, self.settings.tool_api_timeout, int((time.monotonic() - started) * 1000))
            raise LLMError(504, "TOOL_API_TIMEOUT", "游戏工具请求超时，结果尚未确认，请查询状态。") from error
        except httpx.RequestError as error:
            # 不记录底层异常的 message/URL：其中可能夹带凭据或主机细节；错误类型足以定位连接问题。
            logger.warning("[Tools] Node 连接失败 method=%s path=%s error=%s",
                           method, path, type(error).__name__)
            raise LLMError(503, "NODE_UNAVAILABLE", "无法连接 Node 游戏工具服务。") from error
        except (ValueError, TypeError, KeyError) as error:
            logger.warning("[Tools] Node 响应无效 method=%s path=%s error=%s detail=%s",
                           method, path, type(error).__name__,
                           diagnostics.safe_text(error, (self.settings.llm_api_key,), 240))
            raise LLMError(502, "INVALID_TOOL_RESPONSE", "游戏工具响应格式无效。") from error

    async def catalog(self) -> tuple[dict[str, dict], int]:
        body = await self.request("GET", "/api/tools")
        return self.parse_catalog(body)

    @staticmethod
    def parse_catalog(body: Any) -> tuple[dict[str, dict], int]:
        reason = "CATALOG_SHAPE"
        try:
            if not isinstance(body, dict):
                raise ValueError("catalog must be an object")
            definitions, generation = body["tools"], body["generation"]
            if not isinstance(definitions, list) or not 1 <= len(definitions) <= 64 or type(generation) is not int:
                raise ValueError("catalog header")
            result = {}
            for index, definition in enumerate(definitions):
                if not isinstance(definition, dict):
                    reason = f"DEFINITION_{index}_TYPE"
                    raise ValueError("definition must be an object")
                name = definition.get("name")
                if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) or name in result:
                    reason = f"DEFINITION_{index}_NAME"
                    raise ValueError("invalid tool name")
                if not isinstance(definition.get("description"), str):
                    reason = f"DEFINITION_{name}_DESCRIPTION"
                    raise ValueError("invalid description")
                if not validate_schema(definition.get("parameters")):
                    reason = f"DEFINITION_{name}_PARAMETERS"
                    raise ValueError("invalid parameters")
                result[name] = definition
            logger.debug("[Tools] 工具目录读取完成 tools=%d generation=%d", len(result), generation)
            return result, generation
        except (TypeError, KeyError, ValueError, RecursionError) as error:
            logger.warning("[Tools] 工具目录无效 reason=%s error=%s detail=%s",
                           reason, type(error).__name__,
                           diagnostics.safe_text(error, (), 240))
            raise LLMError(502, "INVALID_TOOL_CATALOG", "Node 工具目录格式无效。") from error

    async def session(self) -> tuple[dict[str, dict], dict]:
        body = await self.request("GET", "/api/tools")
        catalog, generation = self.parse_catalog(body)
        instance = body.get("instanceId")
        if not isinstance(instance, str) or not re.fullmatch(r"[a-f0-9-]{36}", instance):
            raise LLMError(502, "INVALID_TOOL_CATALOG", "Node 未提供有效实例标识，请更新 Node。")
        return catalog, {"instanceId": instance, "generation": generation}

    async def lease(self, action: str, player: str, context: dict) -> dict:
        return await self.request("POST", "/api/tools/lease", {"action": action, "username": player, **context})

    async def receipt(self, player: str, request_id: str, instance_id: str) -> dict:
        from urllib.parse import urlencode
        return await self.request("GET", f"/api/tools/requests/{request_id}?" + urlencode({"username": player, "instanceId": instance_id}))

    async def execute(self, name: str, arguments: dict, player: str, request_id: str, generation: int, *, context: dict | None = None) -> dict:
        result = await self.request("POST", "/api/tools/execute", {
            "tool": name, "arguments": arguments, "username": player, "requestId": request_id, "generation": generation,
            **({key: context[key] for key in ("instanceId", "leaseId") if key in context} if context else {})
        })
        return self.validate_result(name, result)

    @staticmethod
    def validate_result(name: str, result: Any) -> dict:
        if not isinstance(result, dict) or set(result) != {"success", "tool", "data", "error"} or type(result["success"]) is not bool or result["tool"] != name:
            raise LLMError(502, "INVALID_TOOL_RESPONSE", "游戏工具执行结果无效。")
        if result["success"] and (result["error"] is not None or not isinstance(result["data"], dict)):
            raise LLMError(502, "INVALID_TOOL_RESPONSE", "游戏工具成功结果无效。")
        if not result["success"] and (result["data"] is not None or not isinstance(result["error"], dict)):
            raise LLMError(502, "INVALID_TOOL_RESPONSE", "游戏工具失败结果无效。")
        return result

    async def cancel(self, player: str, request_id: str | None = None, *, context: dict | None = None) -> None:
        await self.request("POST", "/api/tools/cancel", {"username": player, **({"requestId": request_id} if request_id else {}), **(context or {})})
