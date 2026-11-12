import asyncio
import json
import math
import re
from typing import Any

import httpx

from .config import Settings
from .llm_client import LLMError


def validate(schema: dict, value: Any) -> bool:
    """校验 Node 注册表使用的有限 Schema 子集，不独立维护游戏参数定义。"""
    if "enum" in schema and value not in schema["enum"]:
        return False
    kind = schema.get("type")
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
                            raise LLMError(502, "NODE_TOOL_ERROR", "Node 工具服务拒绝请求，可能已停止或状态已变化。")
                        return body["data"]
        except (TimeoutError, httpx.TimeoutException):
            raise LLMError(504, "TOOL_API_TIMEOUT", "游戏工具请求超时，结果尚未确认，请查询状态。") from None
        except httpx.RequestError:
            raise LLMError(503, "NODE_UNAVAILABLE", "无法连接 Node 游戏工具服务。") from None
        except (ValueError, TypeError, KeyError):
            raise LLMError(502, "INVALID_TOOL_RESPONSE", "游戏工具响应格式无效。") from None

    async def catalog(self) -> tuple[dict[str, dict], int]:
        body = await self.request("GET", "/api/tools")
        try:
            definitions, generation = body["tools"], body["generation"]
            if not isinstance(definitions, list) or not 1 <= len(definitions) <= 64 or type(generation) is not int:
                raise ValueError("catalog")
            result = {}
            for definition in definitions:
                name = definition["name"]
                if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) or name in result:
                    raise ValueError("name")
                if not isinstance(definition["description"], str) or not validate_schema(definition["parameters"]):
                    raise ValueError("definition")
                result[name] = definition
            return result, generation
        except (TypeError, KeyError, ValueError, RecursionError):
            raise LLMError(502, "INVALID_TOOL_CATALOG", "Node 工具目录格式无效。") from None

    async def execute(self, name: str, arguments: dict, player: str, request_id: str, generation: int) -> dict:
        result = await self.request("POST", "/api/tools/execute", {
            "tool": name, "arguments": arguments, "username": player, "requestId": request_id, "generation": generation
        })
        if not isinstance(result, dict) or set(result) != {"success", "tool", "data", "error"} or type(result["success"]) is not bool or result["tool"] != name:
            raise LLMError(502, "INVALID_TOOL_RESPONSE", "游戏工具执行结果无效。")
        if result["success"] and (result["error"] is not None or not isinstance(result["data"], dict)):
            raise LLMError(502, "INVALID_TOOL_RESPONSE", "游戏工具成功结果无效。")
        if not result["success"] and (result["data"] is not None or not isinstance(result["error"], dict)):
            raise LLMError(502, "INVALID_TOOL_RESPONSE", "游戏工具失败结果无效。")
        return result

    async def cancel(self, player: str, request_id: str | None = None) -> None:
        await self.request("POST", "/api/tools/cancel", {"username": player, **({"requestId": request_id} if request_id else {})})
