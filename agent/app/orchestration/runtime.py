"""Planned task runtime: plan -> act -> observe -> replan -> explicit finish.

The runtime owns only orchestration. Minecraft operations still go through
Node's tool registry; skills and the background TaskManager are untouched.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from .. import diagnostics
from ..config import Settings
from ..intent import MessageRequest, redact
from ..llm_client import LLMClient, LLMError
from ..memory.manager import MemoryManager, MemoryTurn
from ..persona import humanize_reply
from ..tool_client import ToolClient, validate
from ..tool_loop import (
    MAX_CALLS_PER_RESPONSE,
    ToolCall,
    ToolOutputError,
    display_reply,
    parse_arguments,
)
from .models import FinishArgs, PlanDecision, UpdatePlanArgs
from .planner import Planner
from .prompts import orchestrator_system, render_plan
from .tools import CONTROL_TOOL_NAMES, FINISH_TOOL, UPDATE_PLAN_TOOL

logger = logging.getLogger("minemate.orchestration")


@dataclass
class RuntimeState:
    plan: PlanDecision | None = None
    tool_calls: int = 0
    llm_steps: int = 0
    text_only: int = 0
    replans: int = 0
    consecutive_errors: int = 0
    invalid_outputs: int = 0
    error_counts: dict[tuple[str, str, str], int] = field(default_factory=dict)
    error_nudged: bool = False
    last_text: str | None = None
    executing: bool = False
    pending: bool = False
    request_id: str | None = None


class AgentRuntime:
    def __init__(self, settings: Settings, llm: LLMClient, client: ToolClient,
                 memory: MemoryManager | None = None, planner: Planner | None = None):
        self.settings, self.llm, self.client, self.memory = settings, llm, client, memory
        self.planner = planner or Planner(llm, enabled=settings.task_planner_enabled)

    async def run(self, request: MessageRequest) -> dict:
        if not self.settings.llm_api_key or not self.settings.llm_model:
            raise LLMError(503, "LLM_NOT_CONFIGURED", "语言模型尚未配置。")
        secrets = (self.settings.llm_api_key,)
        state = RuntimeState()
        memory_turn: MemoryTurn | None = None
        memory_reason = "error"
        try:
            async with asyncio.timeout(self.settings.task_timeout):
                catalog, generation = await self.client.catalog()
                if request.generation is not None and request.generation != generation:
                    raise LLMError(409, "STALE_TASK", "这条消息已被停止或新的移动任务替换。")
                snapshot = await self.client.request("GET", "/api/world")
                node_tools = [
                    {"type": "function", "function": {key: definition[key] for key in ("name", "description", "parameters")}}
                    for definition in catalog.values()
                ]
                memory_turn, messages = await self._initial_messages(
                    request, node_tools, snapshot, memory_turn)
                decision = await self.planner.decide(messages, node_tools)
                if decision.mode == "plan":
                    state.plan = decision
                    messages.append({"role": "user", "content": render_plan(decision)})
                tools = node_tools + [FINISH_TOOL, UPDATE_PLAN_TOOL]
                result = await self._loop(
                    request, state, messages, tools, catalog, generation, memory_turn)
                memory_reason = "completed" if result["reply"] else memory_reason
                return result
        except asyncio.CancelledError:
            memory_reason = "cancelled"
            await self._cancel(state, request.player)
            raise
        except TimeoutError:
            memory_reason = "timeout"
            await self._cancel(state, request.player)
            logger.warning("[Runtime] 任务超时 steps=%d tools=%d", state.llm_steps, state.tool_calls)
            return await self._fallback(request, state, memory_turn, "timeout")
        except LLMError:
            memory_reason = "error"
            await self._cancel(state, request.player)
            raise
        except Exception as error:
            memory_reason = "error"
            await self._cancel(state, request.player)
            diagnostics.log_error(
                logger, "[Runtime] 任务执行异常", error,
                secrets=secrets, level=logging.ERROR,
                player=request.player, steps=state.llm_steps, tools=state.tool_calls,
            )
            raise LLMError(502, "AGENT_ERROR", "任务执行失败，请稍后再试。") from error
        finally:
            if memory_turn is not None and not memory_turn.closed:
                await self._end_memory(memory_turn, memory_reason)

    async def _initial_messages(self, request: MessageRequest, node_tools: list[dict],
                                snapshot: Any, memory_turn: MemoryTurn | None
                                ) -> tuple[MemoryTurn | None, list[dict]]:
        system = orchestrator_system()
        fallback_messages = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps({
            "player": request.player, "message": request.message, "world": snapshot,
        }, ensure_ascii=False)}]
        if self.memory is None:
            return None, fallback_messages
        try:
            memory_turn = await self.memory.begin_turn(
                request.player, request.message, system_prompt=system)
            messages = await self.memory.build_context(memory_turn, system, snapshot, node_tools)
            return memory_turn, messages
        except Exception as error:
            logger.warning("[Runtime] 记忆上下文不可用，使用无记忆上下文：%s", type(error).__name__)
            if memory_turn is not None:
                await self._end_memory(memory_turn, "error")
            return None, fallback_messages

    async def _loop(self, request: MessageRequest, state: RuntimeState, messages: list[dict],
                    tools: list[dict], catalog: dict, generation: int,
                    memory_turn: MemoryTurn | None) -> dict:
        max_steps = self.settings.task_max_steps
        for _ in range(max_steps):
            state.llm_steps += 1
            if state.invalid_outputs >= 4:
                return await self._fallback(request, state, memory_turn, "invalid_limit")
            if state.tool_calls >= self.settings.task_max_tool_calls:
                return await self._fallback(request, state, memory_turn, "tool_limit")
            response = await self.llm.tool_completion(messages, tools, final=False)
            if not isinstance(response, dict):
                await self._invalid(state, messages, "模型响应不是对象")
                continue
            calls = response.get("tool_calls")
            if calls is None:
                calls = []
            if not isinstance(calls, list):
                await self._invalid(state, messages, "tool_calls 不是列表")
                continue

            if not calls:
                content = response.get("content")
                try:
                    text = display_reply(content)
                except ValueError:
                    await self._invalid(state, messages, "最终文字格式无效")
                    continue
                state.last_text = text
                state.text_only += 1
                messages.append({"role": "assistant", "content": text})
                await self._record_assistant(memory_turn, text, [])
                if state.text_only >= self.settings.task_max_text_only:
                    summary = text if not state.plan and state.tool_calls == 0 and state.consecutive_errors == 0 else None
                    return await self._fallback(
                        request, state, memory_turn, "text_only", summary=summary)
                messages.append({
                    "role": "user",
                    "content": "[编排器提示] 如果任务已经结束，请调用 finish_task；否则继续调用工具。",
                })
                continue

            state.text_only = 0
            try:
                parsed = self._validate_calls(calls, catalog)
            except ToolOutputError as error:
                tool = str(error.context.get("tool", ""))[:64]
                detail = f"工具调用无效（{error.code}）" + (f"：{tool}" if tool else "")
                await self._invalid(state, messages, detail)
                continue

            control = [item for item in parsed if item[1] in CONTROL_TOOL_NAMES]
            if control:
                if len(parsed) != 1:
                    await self._invalid(state, messages, "finish_task/update_plan 必须单独调用")
                    continue
                call, name, arguments, _ = parsed[0]
                if name == "finish_task":
                    result = await self._finish(request, state, messages, memory_turn, call, arguments)
                    if result is not None:
                        return result
                    continue
                updated = await self._update_plan(request, state, messages, memory_turn, call, arguments)
                if not updated:
                    return await self._fallback(request, state, memory_turn, "replan_limit")
                continue

            payload = [item[0].model_dump() for item in parsed]
            await self._record_assistant(memory_turn, None, payload)
            messages.append({"role": "assistant", "content": None, "tool_calls": payload})
            for call, name, arguments, fingerprint in parsed:
                result = await self._execute_one(
                    request, state, name, arguments, fingerprint, generation)
                await self._record_tool_result(memory_turn, call.id, name, result)
                messages.append({
                    "role": "tool", "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })
                await self._observe(state, messages, result, name, fingerprint)
            if state.error_nudged and state.consecutive_errors >= self.settings.task_max_consecutive_errors * 2:
                return await self._fallback(request, state, memory_turn, "error_limit")
            if state.invalid_outputs >= 4:
                return await self._fallback(request, state, memory_turn, "invalid_limit")
        return await self._fallback(request, state, memory_turn, "step_limit")

    def _validate_calls(self, calls: list, catalog: dict) -> list[tuple[ToolCall, str, dict, str]]:
        if len(calls) > MAX_CALLS_PER_RESPONSE:
            raise ToolOutputError("TOO_MANY_CALLS", "too many calls", count=len(calls))
        parsed: list[tuple[ToolCall, str, dict, str]] = []
        ids: set[str] = set()
        for raw in calls:
            try:
                call = ToolCall.model_validate(raw)
                name = call.function.name
                arguments = parse_arguments(call.function.arguments)
                if call.id in ids:
                    raise ToolOutputError("DUPLICATE_CALL_ID", "duplicate id", tool=name)
                ids.add(call.id)
                if name in CONTROL_TOOL_NAMES:
                    pass
                elif name not in catalog:
                    raise ToolOutputError("UNKNOWN_TOOL", "unregistered tool", tool=name)
                elif not validate(catalog[name]["parameters"], arguments):
                    raise ToolOutputError("INVALID_ARGUMENT", "invalid arguments", tool=name)
                fingerprint = json.dumps([name, arguments], sort_keys=True)
            except ToolOutputError:
                raise
            except Exception as error:
                raise ToolOutputError("INVALID_TOOL_CALL", "malformed tool call",
                                      tool=str(raw.get("function", {}).get("name", "unknown"))
                                      if isinstance(raw, dict) and isinstance(raw.get("function"), dict) else "unknown") from error
            parsed.append((call, name, arguments, fingerprint))
        return parsed

    async def _execute_one(self, request: MessageRequest, state: RuntimeState,
                           name: str, arguments: dict, fingerprint: str, generation: int) -> dict:
        if state.tool_calls >= self.settings.task_max_tool_calls:
            return self._failure(name, "CALL_LIMIT", "本次任务的工具调用次数已达到上限。")
        # Do not repeat an identical failed call indefinitely. The model sees the
        # error and must change the plan/arguments or finish.
        if any(existing[0] == name and existing[1] == fingerprint and count >= self.settings.task_max_same_error
               for existing, count in state.error_counts.items()):
            return self._failure(name, "REPEATED_FAILURE", "同一操作已经失败多次，请换方案或结束。")
        request_id = uuid.uuid4().hex
        state.executing = True
        state.request_id = request_id
        try:
            result = await self.client.execute(name, arguments, request.player, request_id, generation)
        except LLMError as error:
            result = self._failure(name, error.code, error.message)
        except Exception as error:
            logger.warning("[Runtime] 工具执行异常 tool=%s error=%s", name, type(error).__name__)
            result = self._failure(name, "EXECUTION_FAILED", "工具执行异常。")
        finally:
            state.executing = False
        state.tool_calls += 1
        return result

    async def _observe(self, state: RuntimeState, messages: list[dict], result: dict,
                       name: str, fingerprint: str) -> None:
        success = result.get("success") is True
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        status = data.get("status")
        failed = not success or status in ("uncertain", "failed", "cancelled")
        if status == "pending":
            state.pending = True
        else:
            state.pending = False
        if failed:
            state.consecutive_errors += 1
            error = result.get("error") if isinstance(result.get("error"), dict) else {}
            code = str(error.get("code", "TOOL_FAILED"))
            key = (name, fingerprint, code)
            state.error_counts[key] = state.error_counts.get(key, 0) + 1
            if state.consecutive_errors >= self.settings.task_max_consecutive_errors and not state.error_nudged:
                state.error_nudged = True
                messages.append({
                    "role": "user",
                    "content": "[编排器提示] 连续失败次数较多。请根据错误改变参数/工具或调用 update_plan，仍无法继续则 finish_task。",
                })
        else:
            state.consecutive_errors = 0
            state.error_nudged = False
            # A successful action means the world/plan changed; allow a
            # previously failed action to be retried without waiting for a
            # separate update_plan.
            state.error_counts.clear()

    async def _finish(self, request: MessageRequest, state: RuntimeState, messages: list[dict],
                      memory_turn: MemoryTurn | None, call: ToolCall, arguments: dict) -> dict | None:
        try:
            finish = FinishArgs.model_validate(arguments)
        except Exception:
            result = self._failure("finish_task", "INVALID_ARGUMENT", "finish_task 参数无效。")
            await self._record_assistant(memory_turn, None, [call.model_dump()])
            await self._record_tool_result(memory_turn, call.id, "finish_task", result)
            messages.append({"role": "assistant", "content": None, "tool_calls": [call.model_dump()]})
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": json.dumps(result, ensure_ascii=False)})
            return None
        if state.pending:
            await self._cancel(state, request.player)
        reply = redact(humanize_reply(finish.summary), self.settings.llm_api_key) or "好了。"
        await self._record_assistant(memory_turn, None, [call.model_dump()])
        result = {"success": True, "tool": "finish_task",
                  "data": {"status": finish.status, "summary": finish.summary}, "error": None}
        await self._record_tool_result(memory_turn, call.id, "finish_task", result)
        await self._record_assistant(memory_turn, reply, [])
        if memory_turn is not None:
            await self._end_memory(memory_turn, finish.status)
        logger.info("[Runtime] 完成任务 status=%s steps=%d tools=%d", finish.status,
                    state.llm_steps, state.tool_calls)
        return {"intent": "conversation", "arguments": {}, "reply": reply}

    async def _update_plan(self, request: MessageRequest, state: RuntimeState, messages: list[dict],
                           memory_turn: MemoryTurn | None, call: ToolCall, arguments: dict) -> bool:
        try:
            update = UpdatePlanArgs.model_validate(arguments)
            goal = update.goal or (state.plan.goal if state.plan else request.message)
            plan = PlanDecision(mode="plan", goal=goal, steps=update.steps)
        except Exception:
            result = self._failure("update_plan", "INVALID_ARGUMENT", "update_plan 参数无效。")
            await self._record_assistant(memory_turn, None, [call.model_dump()])
            await self._record_tool_result(memory_turn, call.id, "update_plan", result)
            messages.append({"role": "assistant", "content": None, "tool_calls": [call.model_dump()]})
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": json.dumps(result, ensure_ascii=False)})
            return True
        state.replans += 1
        if state.replans > self.settings.task_max_replans:
            return False
        state.plan = plan
        state.error_counts.clear()
        state.consecutive_errors = 0
        state.error_nudged = False
        result = {"success": True, "tool": "update_plan",
                  "data": {"status": "completed", "steps": [s.model_dump() for s in plan.steps]},
                  "error": None}
        await self._record_assistant(memory_turn, None, [call.model_dump()])
        await self._record_tool_result(memory_turn, call.id, "update_plan", result)
        messages.append({"role": "assistant", "content": None, "tool_calls": [call.model_dump()]})
        messages.append({"role": "tool", "tool_call_id": call.id,
                         "content": json.dumps(result, ensure_ascii=False)})
        messages.append({"role": "user", "content": render_plan(plan)})
        logger.info("[Runtime] 计划已更新 replans=%d steps=%d", state.replans, len(plan.steps))
        return True

    async def _invalid(self, state: RuntimeState, messages: list[dict], reason: str) -> None:
        state.invalid_outputs += 1
        state.consecutive_errors += 1
        logger.warning("[Runtime] 模型输出无效 reason=%s count=%d", reason, state.invalid_outputs)
        messages.append({
            "role": "user",
            "content": f"[编排器提示] {reason}。请修正工具调用或结束；不要重复同一无效输出。",
        })

    async def _fallback(self, request: MessageRequest, state: RuntimeState,
                        memory_turn: MemoryTurn | None, reason: str,
                        summary: str | None = None) -> dict:
        await self._cancel(state, request.player)
        if summary:
            reply = summary
        elif state.last_text and not state.plan and state.tool_calls == 0 and state.consecutive_errors == 0:
            reply = state.last_text
        elif state.consecutive_errors > 0:
            reply = "这次先到这儿，遇到点问题，任务还没完全做完。"
        elif state.plan:
            reply = "这次先到这儿，任务还没完全结束，剩下的步骤还得继续。"
        else:
            reply = "这次先到这儿，任务还没完全结束。"
        reply = redact(humanize_reply(reply), self.settings.llm_api_key) or "这次先到这儿。"
        await self._record_assistant(memory_turn, reply, [])
        await self._end_memory(memory_turn, "limit")
        logger.warning("[Runtime] 兜底结束 reason=%s steps=%d tools=%d errors=%d",
                       reason, state.llm_steps, state.tool_calls, state.consecutive_errors)
        return {"intent": "conversation", "arguments": {}, "reply": reply}

    @staticmethod
    def _failure(tool: str, code: str, message: str) -> dict:
        return {"success": False, "tool": tool, "data": None,
                "error": {"code": code, "message": message}}

    async def _record_assistant(self, turn: MemoryTurn | None, content: str | None,
                                tool_calls: list[dict] | None) -> None:
        if self.memory is None or turn is None:
            return
        try:
            await self.memory.record_assistant(turn, content, tool_calls or [])
        except Exception as error:
            logger.warning("[Runtime] 记忆写入失败 label=assistant error=%s", type(error).__name__)

    async def _record_tool_result(self, turn: MemoryTurn | None, call_id: str, tool: str,
                                  result: dict) -> None:
        if self.memory is None or turn is None:
            return
        try:
            await self.memory.record_tool_result(turn, call_id, tool, result)
        except Exception as error:
            logger.warning("[Runtime] 记忆写入失败 label=tool_result error=%s", type(error).__name__)

    async def _end_memory(self, turn: MemoryTurn | None, reason: str) -> None:
        if self.memory is None or turn is None or turn.closed:
            return
        try:
            await asyncio.shield(self.memory.end_turn(turn, reason))
        except BaseException as error:
            logger.warning("[Runtime] 关闭记忆轮次失败 reason=%s error=%s", reason, type(error).__name__)

    async def _cancel(self, state: RuntimeState, player: str) -> None:
        if not (state.executing or state.pending):
            return
        try:
            async with asyncio.timeout(3):
                await self.client.cancel(player, state.request_id)
        except Exception as error:
            logger.warning("[Runtime] 取消请求失败 error=%s", type(error).__name__)
