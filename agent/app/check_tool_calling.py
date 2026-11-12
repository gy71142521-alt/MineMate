"""手动执行一次模型协议探测；不连接游戏、不执行工具，可能产生 API 费用。"""
import asyncio

from .config import load_settings
from .llm_client import LLMClient, LLMError
from .tool_loop import ToolCall, parse_arguments


async def check() -> bool:
    settings = load_settings()
    if not settings.llm_api_key or not settings.llm_model:
        print("未配置模型 Key 或 Model，未发出请求。")
        return False
    client = LLMClient(settings)
    tool = {"type": "function", "function": {"name": "protocol_probe", "description": "仅用于协议探测，不执行游戏操作。",
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}}
    try:
        # 强制调用一个无副作用的探测定义；不创建游戏工具客户端。
        body = await client._request({"model": settings.llm_model, "messages": [{"role": "user", "content": "Call protocol_probe with empty arguments."}],
            "tools": [tool], "tool_choice": {"type": "function", "function": {"name": "protocol_probe"}}, "parallel_tool_calls": False, "max_tokens": settings.tool_llm_max_tokens})
        choice = body["choices"][0]
        calls = choice["message"]["tool_calls"]
        if choice.get("finish_reason") != "tool_calls" or len(calls) != 1:
            raise ValueError("invalid calls")
        call = ToolCall.model_validate(calls[0])
        if call.function.name != "protocol_probe" or parse_arguments(call.function.arguments) != {}:
            raise ValueError("invalid arguments")
        print("Tool Calling 协议探测通过：收到合法的 function tool_calls，未执行游戏操作。")
        return True
    except LLMError as error:
        print(f"协议探测失败：{error.code}。请核对供应商的 function tools 支持情况。")
    except (ValueError, KeyError, IndexError, TypeError):
        print("协议探测失败：模型未按要求返回 function tool_calls。")
    return False


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(check()) else 1)
