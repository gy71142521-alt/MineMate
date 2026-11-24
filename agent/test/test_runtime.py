import json
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock

from app.config import load_settings
from app.intent import MessageRequest
from app.memory import DeterministicSummarizer, InMemoryEventStore, MemoryConfig, MemoryManager
from app.orchestration import AgentRuntime, PlanDecision, PlanStep
from app.orchestration.runtime import RuntimeState


def empty_schema():
    return {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


def demo_definition():
    return {"name": "demo", "description": "demo", "parameters": empty_schema()}


def tool_call(name, arguments=None, identity="call1"):
    return {"id": identity, "type": "function", "function": {
        "name": name,
        "arguments": json.dumps({} if arguments is None else arguments),
    }}


def finish_call(summary="搞定了。", identity="finish1", status="completed"):
    return tool_call("finish_task", {"status": status, "summary": summary}, identity)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self, planner_mode="direct", **overrides):
        task_config = {
            "task_timeout": 5, "task_max_steps": 6, "task_max_tool_calls": 8,
            "task_max_consecutive_errors": 3, "task_max_same_error": 2,
            "task_max_replans": 3, "task_max_text_only": 2,
        }
        task_config.update(overrides)
        settings = replace(
            load_settings({}), llm_api_key="unit-secret", llm_model="mock",
            tool_calling_enabled=True, **task_config,
        )
        llm = AsyncMock()
        client = AsyncMock()
        client.catalog.return_value = ({"demo": demo_definition()}, 1)
        client.request.return_value = {"player": {"health": 20}}
        client.execute.return_value = {"success": True, "tool": "demo",
                                       "data": {"status": "completed"}, "error": None}
        planner = AsyncMock()
        if planner_mode == "plan":
            planner.decide.return_value = PlanDecision(mode="plan", goal="完成演示", steps=[
                PlanStep(title="第一步", success_criteria="真实结果完成"),
                PlanStep(title="第二步", success_criteria="任务结束"),
            ])
        else:
            planner.decide.return_value = PlanDecision(mode="direct", reason="简单")
        return AgentRuntime(settings, llm, client, memory=None, planner=planner), llm, client

    async def test_direct_task_executes_tools_and_finishes_explicitly(self):
        runtime, llm, client = self.runtime()
        llm.tool_completion.side_effect = [
            {"tool_calls": [tool_call("demo")]},
            {"tool_calls": [finish_call("都弄好了。")]},
        ]
        result = await runtime.run(MessageRequest(player="Steve", message="执行"))
        self.assertEqual(result["reply"], "都弄好了。")
        self.assertEqual(client.execute.await_count, 1)
        self.assertEqual(client.cancel.await_count, 0)

    async def test_runtime_uses_planner_by_default(self):
        settings = replace(load_settings({}), llm_api_key="unit-secret", llm_model="mock",
                           tool_calling_enabled=True, task_timeout=5)
        llm = AsyncMock()
        client = AsyncMock()
        client.catalog.return_value = ({"demo": demo_definition()}, 1)
        client.request.return_value = {"player": {"health": 20}}
        llm.plan.return_value = {"mode": "plan", "goal": "完成演示", "steps": [
            {"title": "第一步", "success_criteria": "完成"},
            {"title": "第二步", "success_criteria": "结束"},
        ]}
        llm.tool_completion.return_value = {"tool_calls": [finish_call("完成。")]}
        runtime = AgentRuntime(settings, llm, client, memory=None)
        result = await runtime.run(MessageRequest(player="Steve", message="复杂任务"))
        self.assertEqual(result["reply"], "完成。")
        llm.plan.assert_awaited_once()
        messages = llm.tool_completion.call_args.args[0]
        self.assertTrue(any("[内部计划" in str(message.get("content", "")) for message in messages))

    async def test_plan_context_is_visible_before_acting(self):
        runtime, llm, client = self.runtime("plan")
        llm.tool_completion.return_value = {"tool_calls": [finish_call("完成。")]}
        await runtime.run(MessageRequest(player="Steve", message="复杂任务"))
        messages = llm.tool_completion.call_args.args[0]
        self.assertTrue(any("[内部计划" in str(message.get("content", "")) for message in messages))
        self.assertEqual(client.execute.await_count, 0)

    async def test_tool_error_is_fed_back_instead_of_ending(self):
        runtime, llm, client = self.runtime()
        client.execute.return_value = {"success": False, "tool": "demo", "data": None,
                                       "error": {"code": "NO_LINE_OF_SIGHT", "message": "看不见"}}
        llm.tool_completion.side_effect = [
            {"tool_calls": [tool_call("demo")]},
            {"tool_calls": [finish_call("被挡住了，先到这里。", status="partial")]},
        ]
        result = await runtime.run(MessageRequest(player="Steve", message="执行"))
        self.assertIn("挡住", result["reply"])
        self.assertEqual(client.execute.await_count, 1)
        second_messages = llm.tool_completion.call_args.args[0]
        self.assertTrue(any(message.get("role") == "tool" and "NO_LINE_OF_SIGHT" in message.get("content", "")
                            for message in second_messages))

    async def test_pending_is_observation_not_terminal(self):
        runtime, llm, client = self.runtime()
        client.execute.return_value = {"success": True, "tool": "demo",
                                       "data": {"status": "pending"}, "error": None}
        llm.tool_completion.side_effect = [
            {"tool_calls": [tool_call("demo")]},
            {"tool_calls": [finish_call("还在继续，先到这儿。", status="partial")]},
        ]
        result = await runtime.run(MessageRequest(player="Steve", message="执行"))
        self.assertIn("先到这儿", result["reply"])
        self.assertEqual(client.execute.await_count, 1)
        self.assertEqual(client.cancel.await_count, 1)

    async def test_timeout_returns_bounded_reply(self):
        runtime, llm, _ = self.runtime(task_timeout=0.01)

        async def slow(*_args, **_kwargs):
            await __import__("asyncio").sleep(0.1)
            return {"tool_calls": [finish_call("太晚了。")]}

        llm.tool_completion.side_effect = slow
        result = await runtime.run(MessageRequest(player="Steve", message="执行"))
        self.assertIn("先停", result["reply"])

    async def test_error_can_trigger_update_plan_then_finish(self):
        runtime, llm, client = self.runtime()
        client.execute.return_value = {"success": False, "tool": "demo", "data": None,
                                       "error": {"code": "OUT_OF_RANGE", "message": "太远"}}
        llm.tool_completion.side_effect = [
            {"tool_calls": [tool_call("demo")]},
            {"tool_calls": [tool_call("update_plan", {
                "reason": "需要先靠近",
                "steps": [{"title": "先靠近目标", "success_criteria": "进入交互距离"}],
            }, identity="plan2")]},
            {"tool_calls": [finish_call("先到这儿。", status="partial")]},
        ]
        result = await runtime.run(MessageRequest(player="Steve", message="执行"))
        self.assertIn("先到这儿", result["reply"])
        self.assertEqual(client.execute.await_count, 1)

    async def test_malformed_tool_call_is_fed_back_not_crashing(self):
        runtime, llm, client = self.runtime()
        llm.tool_completion.side_effect = [
            {"tool_calls": [{"id": "bad", "type": "function",
                             "function": {"name": "demo", "arguments": "{not json"}}]},
            {"tool_calls": [finish_call("修好了。")]},
        ]
        result = await runtime.run(MessageRequest(player="Steve", message="执行"))
        self.assertEqual(result["reply"], "修好了。")
        self.assertEqual(client.execute.await_count, 0)

    async def test_replan_allows_retry_of_previous_failed_action(self):
        runtime, llm, client = self.runtime()
        client.execute.side_effect = [
            {"success": False, "tool": "demo", "data": None,
             "error": {"code": "NO_LINE_OF_SIGHT", "message": "看不见"}},
            {"success": True, "tool": "demo", "data": {"status": "completed"}, "error": None},
        ]
        llm.tool_completion.side_effect = [
            {"tool_calls": [tool_call("demo")]},
            {"tool_calls": [tool_call("update_plan", {
                "reason": "先换站位",
                "steps": [{"title": "重新尝试", "success_criteria": "完成"}],
            }, identity="plan2")]},
            {"tool_calls": [tool_call("demo", identity="demo2")]},
            {"tool_calls": [finish_call("这次成功了。")]},
        ]
        result = await runtime.run(MessageRequest(player="Steve", message="执行"))
        self.assertEqual(result["reply"], "这次成功了。")
        self.assertEqual(client.execute.await_count, 2)

    async def test_finish_prefers_last_natural_reply_for_completed_tasks(self):
        runtime, llm, _ = self.runtime()
        llm.tool_completion.side_effect = [
            {"content": "喏，木斧。"},
            {"tool_calls": [finish_call("把木斧拿给玩家看，背包里还有 3 块木板。")]},
        ]
        result = await runtime.run(MessageRequest(player="Steve", message="我看看"))
        self.assertEqual(result["reply"], "喏，木斧。")

    async def test_intermediate_text_is_not_used_as_finish_reply(self):
        runtime, llm, _ = self.runtime()
        llm.tool_completion.side_effect = [
            {"content": "我先看看。"},
            {"tool_calls": [tool_call("demo")]},
            {"tool_calls": [finish_call("木头挖好了。")]},
        ]
        result = await runtime.run(MessageRequest(player="Steve", message="执行"))
        self.assertEqual(result["reply"], "木头挖好了。")

    async def test_invalid_finish_is_fed_back_and_can_be_corrected(self):
        runtime, llm, client = self.runtime()
        llm.tool_completion.side_effect = [
            {"tool_calls": [tool_call("finish_task", {"status": "completed"}, identity="bad-finish")]},
            {"tool_calls": [finish_call("修好后完成。")]},
        ]
        result = await runtime.run(MessageRequest(player="Steve", message="执行"))
        self.assertEqual(result["reply"], "修好后完成。")
        self.assertEqual(client.execute.await_count, 0)

    async def test_guardrail_finishes_without_infinite_loop(self):
        runtime, llm, client = self.runtime(task_max_steps=2, task_max_tool_calls=1,
                                            task_max_same_error=1, task_max_consecutive_errors=1)
        client.execute.return_value = {"success": False, "tool": "demo", "data": None,
                                       "error": {"code": "NO_LINE_OF_SIGHT", "message": "看不见"}}
        llm.tool_completion.return_value = {"tool_calls": [tool_call("demo")]}
        result = await runtime.run(MessageRequest(player="Steve", message="执行"))
        self.assertIn("先停", result["reply"])
        self.assertLessEqual(client.execute.await_count, 1)

    async def test_finish_closes_memory_turn(self):
        runtime, llm, _ = self.runtime()
        memory = MemoryManager(InMemoryEventStore(), MemoryConfig(), DeterministicSummarizer())
        await memory.startup()
        self.addAsyncCleanup(memory.close)
        runtime.memory = memory
        llm.tool_completion.return_value = {"tool_calls": [finish_call("记住这次。")]}
        await runtime.run(MessageRequest(player="Steve", message="执行"))
        stats = await memory.stats("Steve")
        self.assertEqual(stats["turns"], 1)
        self.assertFalse(stats["degraded"])

    async def test_steering_is_injected_at_the_next_step_boundary(self):
        runtime, _, _ = self.runtime()

        class SteerLLM:
            def __init__(self):
                self.calls = 0
                self.seen = []

            async def tool_completion(self, messages, tools, final=False):
                self.calls += 1
                self.seen.append(messages)
                if self.calls == 1:
                    self.runtime.steer("Steve", "换个目标，去做木镐")
                    return {"tool_calls": [tool_call("demo")]}
                return {"tool_calls": [finish_call("换成木镐了。")]}

        fake = SteerLLM()
        fake.runtime = runtime
        runtime.llm = fake
        result = await runtime.run(MessageRequest(player="Steve", message="去做石镐"))
        self.assertEqual(result["reply"], "换成木镐了。")
        self.assertTrue(any("[玩家插话]" in str(message.get("content", "")) for message in fake.seen[1]))

    async def test_steering_before_finish_defers_finish_until_seen(self):
        runtime, _, _ = self.runtime()

        class SteerLLM:
            def __init__(self):
                self.calls = 0
                self.seen = []

            async def tool_completion(self, messages, tools, final=False):
                self.calls += 1
                self.seen.append(messages)
                if self.calls == 1:
                    self.runtime.steer("Steve", "先停一下")
                    return {"tool_calls": [finish_call("旧任务完成。")]}
                return {"tool_calls": [finish_call("好，先停了。", status="partial")]}

        fake = SteerLLM()
        fake.runtime = runtime
        runtime.llm = fake
        result = await runtime.run(MessageRequest(player="Steve", message="执行"))
        self.assertEqual(result["reply"], "好，先停了。")
        self.assertTrue(any("[玩家插话]" in str(message.get("content", "")) for message in fake.seen[1]))

    async def test_steering_is_rejected_without_active_turn(self):
        runtime, _, _ = self.runtime()
        self.assertFalse(runtime.steer("Steve", "停下"))

    async def test_repeated_navigation_failures_stop_the_loop(self):
        settings = replace(load_settings({}), llm_api_key="unit-secret", llm_model="mock",
                           tool_calling_enabled=True, task_timeout=5)
        llm = AsyncMock()
        client = AsyncMock()
        client.catalog.return_value = ({"goto_position": {
            "name": "goto_position", "description": "goto",
            "parameters": {"type": "object", "properties": {"x": {"type": "integer", "minimum": 0, "maximum": 99}},
                           "required": ["x"], "additionalProperties": False},
        }}, 1)
        client.request.return_value = {"player": {"health": 20}}
        client.execute.return_value = {"success": False, "tool": "goto_position", "data": None,
                                       "error": {"code": "NAVIGATION_FAILED", "message": "no path"}}
        planner = AsyncMock()
        planner.decide.return_value = PlanDecision(mode="direct", reason="simple")
        llm.tool_completion.side_effect = [
            {"tool_calls": [tool_call("goto_position", {"x": i}, identity=f"nav{i}")]}
            for i in range(8)
        ]
        runtime = AgentRuntime(settings, llm, client, memory=None, planner=planner)
        result = await runtime.run(MessageRequest(player="Steve", message="执行"))
        self.assertIn("先停", result["reply"])
        self.assertEqual(client.execute.await_count, 5)

    async def test_text_only_falls_back_to_reply(self):
        runtime, llm, _ = self.runtime(task_max_text_only=1)
        llm.tool_completion.return_value = {"content": "我在呢。"}
        result = await runtime.run(MessageRequest(player="Steve", message="你好"))
        self.assertEqual(result["reply"], "我在呢。")
