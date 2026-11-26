import json
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock

from app.config import load_settings
from app.intent import MessageRequest
from app.llm_client import LLMError
from app.memory import DeterministicSummarizer, InMemoryEventStore, MemoryConfig, MemoryManager
from app.orchestration import AgentRuntime, PlanDecision, PlanStep
from app.orchestration.runtime import RuntimeState
from app.tool_loop import ToolCall
from skills.catalog import create_skill_registry


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

    async def test_runtime_routes_registered_skill_and_returns_skill_evidence(self):
        class Registry:
            def list(self):
                return [{
                    "name": "craft_item", "description": "按真实配方合成物品。",
                    "parameters": {"type": "object", "properties": {"item_name": {"type": "string"},
                                                                            "count": {"type": "integer"}},
                                   "required": ["item_name", "count"]},
                }]

        class Skills:
            def __init__(self):
                self.registry = Registry()
                self.request = None

            async def submit(self, request):
                self.request = request
                return {"execution_id": "skill-1", "skill": request.skill, "status": "completed",
                        "success": True, "data": {"actualIncrease": 1}, "verification": {"verified": True},
                        "error": None, "steps": [{"tool": "craft_item", "status": "completed"}], "calls": 1}

            def view(self, execution_id, player):
                return {"execution_id": execution_id, "skill": "craft_item", "status": "completed",
                        "success": True, "data": {"actualIncrease": 1}, "verification": {"verified": True},
                        "error": None, "steps": [{"tool": "craft_item", "status": "completed"}], "calls": 1}

            async def cancel(self, execution_id, player):
                return self.view(execution_id, player)

            async def wait_view(self, execution_id, player):
                return self.view(execution_id, player)

        runtime, llm, client = self.runtime()
        skills = Skills()
        runtime.skill_manager = skills
        llm.tool_completion.side_effect = [
            {"tool_calls": [tool_call("execute_skill", {"skill": "craft_item",
                                                           "arguments": {"item_name": "crafting_table", "count": 1}})]},
            {"tool_calls": [finish_call("工作台做好了。")]},
        ]
        with self.assertLogs("minemate.orchestration", level="INFO") as logs:
            result = await runtime.run(MessageRequest(player="Steve", message="做一个工作台"))
        selected = [line for line in logs.output if "[Skills] 模型选择" in line]
        self.assertEqual(len(selected), 1)
        self.assertIn("skill=craft_item", selected[0])
        self.assertIn('arguments={"count": 1, "item_name": "crafting_table"}', selected[0])
        self.assertIn("player=Steve call_id=call1", selected[0])
        self.assertEqual(result["reply"], "工作台做好了。")
        self.assertEqual(skills.request.skill, "craft_item")
        self.assertEqual(skills.request.arguments, {"item_name": "crafting_table", "count": 1})
        client.execute.assert_not_awaited()
        tool_messages = [m for m in llm.tool_completion.call_args_list[1].args[0] if m.get("role") == "tool"]
        self.assertTrue(tool_messages)
        self.assertIn("verified", tool_messages[-1]["content"])

    async def test_skill_selection_log_redacts_bounds_and_preserves_rejected_arguments(self):
        runtime, _, _ = self.runtime()
        runtime.skill_manager = AsyncMock()
        runtime.skill_manager.submit.side_effect = LLMError(422, "INVALID_SKILL_ARGUMENT", "参数无效。")
        arguments = {"skill": "gather_resource", "arguments": {
            "resource": "木头\nunit-secret", "z_extra": "x" * 2000}}
        call = ToolCall.model_validate(tool_call("execute_skill", arguments))
        with self.assertLogs("minemate.orchestration", level="INFO") as logs:
            await runtime._execute_skill(MessageRequest(player="Steve", message="采集木头"),
                                         RuntimeState(), [], None, call, arguments)
        selected = [line for line in logs.output if "[Skills] 模型选择" in line]
        self.assertEqual(len(selected), 1)
        self.assertNotIn("unit-secret", selected[0])
        self.assertIn("[已隐藏]", selected[0])
        self.assertIn("木头", selected[0])
        self.assertNotIn("\n", selected[0])
        self.assertIn("…(+", selected[0])
        self.assertLess(len(selected[0]), 1500)
        self.assertEqual(arguments["arguments"]["resource"], "木头\nunit-secret")

    def skill_runtime(self, planner_mode="direct"):
        runtime, llm, client = self.runtime(planner_mode)
        skills = AsyncMock()
        skills.registry = create_skill_registry()

        async def submit(request):
            skills.registry.validate(request.skill, request.arguments)
            return {"execution_id": "route-skill-1", "skill": request.skill, "status": "completed",
                    "success": True, "data": {}, "verification": {"verified": True},
                    "error": None, "steps": [], "calls": 1}

        skills.submit.side_effect = submit
        runtime.skill_manager = skills
        definitions = {name: {**demo_definition(), "name": name}
                       for name in ("craft_item", "find_block", "mine_block", "place_block", "follow_player",
                                    "inspect_mining_route", "mine_route_block", "mine_vein_block")}
        client.catalog.return_value = (definitions, 1)
        return runtime, llm, client, skills

    async def test_skill_first_catalog_and_dispatch_in_direct_and_plan_modes(self):
        examples = [
            ("craft_item", {"item_name": "iron_chestplate", "count": 1}),
            ("gather_resource", {"resource": "oak_log", "count": 4}),
            ("explore_and_find", {"target_kind": "block", "target": "crafting_table"}),
            ("smelt_item", {"item_name": "raw_iron", "count": 3}),
            ("mine_vein", {"resource": "raw_iron", "count": 3}),
        ]
        for mode in ("direct", "plan"):
            for name, arguments in examples:
                with self.subTest(mode=mode, skill=name):
                    runtime, llm, client, skills = self.skill_runtime(mode)
                    llm.tool_completion.side_effect = [
                        {"tool_calls": [tool_call("execute_skill", {"skill": name, "arguments": arguments})]},
                        {"tool_calls": [finish_call()]},
                    ]
                    await runtime.run(MessageRequest(player="Steve", message="执行目标"))
                    selected = skills.submit.call_args.args[0]
                    self.assertEqual((selected.skill, selected.arguments), (name, arguments))
                    client.execute.assert_not_awaited()
                    visible = llm.tool_completion.call_args_list[0].args[1]
                    names = [tool["function"]["name"] for tool in visible]
                    self.assertEqual(names[0], "execute_skill")
                    self.assertNotIn("craft_item", names)
                    self.assertNotIn("find_block", names)
                    self.assertIn("mine_block", names)  # 精确坐标挖掘仍可调用。
                    for helper in ("inspect_mining_route", "mine_route_block", "mine_vein_block"):
                        self.assertNotIn(helper, names)
                    description = visible[0]["function"]["description"]
                    for field in ('"seed_position"', '"vein_radius"', '"max_route_steps"'):
                        self.assertIn(field, description)  # 第五个 Skill 的 Schema 不能被截断。
                    self.assertEqual(runtime.planner.decide.call_args.args[1], visible[:-2])

    async def test_old_memory_raw_craft_is_rejected_then_model_can_select_skill(self):
        runtime, llm, client, skills = self.skill_runtime()
        llm.tool_completion.side_effect = [
            {"tool_calls": [tool_call("craft_item")]},
            {"tool_calls": [tool_call("execute_skill", {"skill": "craft_item",
                                                        "arguments": {"item_name": "iron_chestplate"}})]},
            {"tool_calls": [finish_call()]},
        ]
        await runtime.run(MessageRequest(player="Steve", message="制作一个铁胸甲"))
        client.execute.assert_not_awaited()
        skills.submit.assert_awaited_once()
        history = llm.tool_completion.call_args_list[1].args[0]
        self.assertTrue(any("SKILL_REQUIRED" in m.get("content", "")
                            for m in history if m.get("role") == "tool"))

    async def test_workbench_placement_can_precede_crafting_skill(self):
        runtime, llm, client, skills = self.skill_runtime()
        llm.tool_completion.side_effect = [
            {"tool_calls": [tool_call("place_block")]},
            {"tool_calls": [tool_call("execute_skill", {"skill": "craft_item", "arguments": {
                "item_name": "iron_chestplate", "table_position": {"x": 1, "y": 64, "z": 0}}})]},
            {"tool_calls": [finish_call()]},
        ]
        await runtime.run(MessageRequest(player="Steve", message="制作一个铁胸甲"))
        self.assertEqual(client.execute.call_args.args[0], "place_block")
        self.assertEqual(client.execute.await_count, 1)
        skills.submit.assert_awaited_once()

    async def test_skill_failure_does_not_unlock_raw_craft(self):
        runtime, llm, client, skills = self.skill_runtime()
        skills.submit.side_effect = LLMError(409, "MATERIALS_MISSING", "材料不足。")
        llm.tool_completion.side_effect = [
            {"tool_calls": [tool_call("execute_skill", {"skill": "craft_item", "arguments": {
                "item_name": "iron_chestplate"}})]},
            {"tool_calls": [tool_call("craft_item")]},
            {"tool_calls": [finish_call(status="blocked")]},
        ]
        await runtime.run(MessageRequest(player="Steve", message="制作铁胸甲"))
        client.execute.assert_not_awaited()
        history = llm.tool_completion.call_args.args[0]
        for code in ("MATERIALS_MISSING", "SKILL_REQUIRED"):
            self.assertTrue(any(code in m.get("content", "") for m in history if m.get("role") == "tool"))

    async def test_smelt_start_requires_skill_but_status_take_still_execute(self):
        runtime, _, client = self.runtime()
        for action in ("start", "status", "take"):
            with self.subTest(action=action):
                client.execute.reset_mock()
                state = RuntimeState(preferred_skills={"smelt_item": "smelt_item"})
                result = await runtime._execute_one(MessageRequest(player="Steve", message="熔炼"),
                                                    state, "smelt_item", {"action": action}, action, 1)
                if action == "start":
                    self.assertEqual(result["error"]["code"], "SKILL_REQUIRED")
                    client.execute.assert_not_awaited()
                else:
                    client.execute.assert_awaited_once()

    async def test_no_skill_manager_preserves_raw_crafting(self):
        runtime, llm, client, _ = self.skill_runtime()
        runtime.skill_manager = None
        llm.tool_completion.side_effect = [
            {"tool_calls": [tool_call("craft_item")]},
            {"tool_calls": [finish_call()]},
        ]
        await runtime.run(MessageRequest(player="Steve", message="制作铁胸甲"))
        client.execute.assert_awaited_once()

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

    async def test_same_tool_failing_across_batches_is_bounded(self):
        runtime, llm, client = self.runtime(task_max_same_error=5, task_max_steps=10)
        client.execute.return_value = {"success": False, "tool": "demo", "data": None,
                                       "error": {"code": "ITEM_ENTITY_MISSING", "message": "missing"}}
        llm.tool_completion.return_value = {"tool_calls": [tool_call("demo")]}
        result = await runtime.run(MessageRequest(player="Steve", message="执行"))
        self.assertIn("先停", result["reply"])
        self.assertLessEqual(client.execute.await_count, 5)

    async def test_error_nudge_does_not_split_a_multi_tool_batch(self):
        runtime, llm, client = self.runtime()
        client.execute.return_value = {"success": False, "tool": "demo", "data": None,
                                       "error": {"code": "OUT_OF_RANGE", "message": "太远"}}
        llm.tool_completion.side_effect = [
            {"tool_calls": [tool_call("demo", identity=f"e{i}") for i in range(4)]},
            {"tool_calls": [finish_call("先停一下。", status="partial")]},
        ]
        await runtime.run(MessageRequest(player="Steve", message="执行"))
        seen = llm.tool_completion.call_args_list[-1].args[0]
        start = next(i for i, message in enumerate(seen)
                     if message.get("role") == "assistant" and len(message.get("tool_calls", [])) == 4)
        self.assertEqual([message.get("role") for message in seen[start + 1:start + 5]], ["tool"] * 4)
        self.assertEqual(seen[start + 5].get("role"), "user")

    async def test_missing_tool_result_is_repaired_before_provider_call(self):
        messages = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "demo", "arguments": "{}"}},
                {"id": "b", "type": "function", "function": {"name": "demo", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "a", "content": "{}"},
        ]
        RuntimeState  # keep import used by test module
        AgentRuntime._ensure_tool_protocol(messages)
        self.assertEqual([m.get("tool_call_id") for m in messages if m.get("role") == "tool"], ["a", "b"])

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
