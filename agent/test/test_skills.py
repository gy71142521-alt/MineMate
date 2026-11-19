import asyncio
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import httpx
from pydantic import Field, ValidationError

from app import main
from app.config import Settings
from app.execution.manager import ExecutionError, TaskManager, inventory_counts
from app.execution.models import TaskRequest
from app.llm_client import LLMError
from app.tool_client import ToolClient
from skills import Skill, SkillCheck, SkillManager, SkillParameters, SkillRegistry, SkillRequest, SkillVerification
from test_execution import MockNode


class TestParameters(SkillParameters):
    item: str = Field(default="crafting_table", min_length=1, max_length=64)
    count: int = Field(default=1, ge=1, le=8)


class TestSkill(Skill):
    """仅用于测试基础设施，不在生产目录注册业务 Skill。"""

    name = "test_skill"
    description = "测试确定性串行调用和独立验收"
    parameters = TestParameters
    preconditions = ("bot_ready",)
    success_criteria = ("inventory_increase",)
    required_tools = ("craft_item", "goto_position", "get_movement_task")

    def __init__(self, mode="normal"):
        self.mode = mode
        self.entered = asyncio.Event()

    async def check_preconditions(self, runtime, parameters, world):
        if self.mode == "materials":
            raise ExecutionError("MATERIALS_MISSING")

    async def execute(self, runtime, parameters):
        self.entered.set()
        if self.mode == "sleep":
            await asyncio.Event().wait()
        if self.mode == "unexpected":
            raise RuntimeError("unit-secret")
        if self.mode in ("walk", "parallel"):
            args = {"x": 1, "y": 64, "z": 2, "wait_seconds": 0}
            if self.mode == "parallel":
                # 第一个请求正在等待导航时，第二个修改请求必须被拒绝。
                moving = asyncio.create_task(runtime.call("goto_position", args, operation_id="walk"))
                try:
                    await asyncio.sleep(0.01)
                    await runtime.call("craft_item", {"item_name": parameters.item, "count": parameters.count}, operation_id="craft")
                finally:
                    moving.cancel()
                    await asyncio.gather(moving, return_exceptions=True)
            else:
                await runtime.call("goto_position", args, operation_id="walk", wait_seconds=0.1)
        if self.mode == "bad_tool":
            await runtime.call("shell", {}, operation_id="invalid")
        if self.mode == "bad_arguments":
            await runtime.call("craft_item", {"item_name": parameters.item, "count": "3"}, operation_id="invalid")
        args = {"item_name": parameters.item, "count": parameters.count}
        if self.mode == "catch_uncertain":
            try:
                await runtime.call("craft_item", args, operation_id="first")
            except ExecutionError:
                await runtime.call("craft_item", args, operation_id="new_id")
        await runtime.call("craft_item", args, operation_id="craft")
        if self.mode == "repeat":
            await runtime.call("craft_item", args, operation_id="craft")
        if self.mode == "conflict":
            await runtime.call("craft_item", {**args, "count": parameters.count + 1}, operation_id="craft")
        return {"item": parameters.item, "count": parameters.count}

    def verify(self, parameters, data, before, after):
        delta = inventory_counts(after).get(parameters.item, 0) - inventory_counts(before).get(parameters.item, 0)
        criterion = "wrong_criterion" if self.mode == "wrong_criterion" else "inventory_increase"
        return SkillVerification(checks=[SkillCheck(criterion=criterion, passed=delta >= parameters.count, observed=delta)])


class RegistryTests(unittest.TestCase):
    def test_register_catalog_isolated_from_node_and_cannot_be_mutated(self):
        registry = SkillRegistry()
        self.assertEqual(registry.list(), [])
        skill = TestSkill()
        registry.register(skill)
        self.assertIs(registry.get("test_skill"), skill)
        entry = registry.list()[0]
        self.assertEqual(set(entry), {"name", "description", "parameters", "preconditions", "success_criteria", "required_tools"})
        entry["parameters"]["properties"]["count"]["maximum"] = 999
        self.assertEqual(registry.list()[0]["parameters"]["properties"]["count"]["maximum"], 8)
        with self.assertRaises(ValueError):
            registry.register(TestSkill())
        with self.assertRaises(LLMError) as error:
            registry.get("shell")
        self.assertEqual(error.exception.code, "UNKNOWN_SKILL")

    def test_strict_skill_and_request_parameter_validation(self):
        registry = SkillRegistry()
        registry.register(TestSkill())
        for value in ({"count": "3"}, {"count": True}, {"count": 0}, {"count": 9}, {"extra": "code"}):
            with self.subTest(value=value), self.assertRaises(LLMError):
                registry.validate("test_skill", value)
        for value in ({"timeout_seconds": 601}, {"max_tool_calls": 193}, {"code": "eval"},
                      {"arguments": {"bad": float("nan")}}, {"arguments": {"big": "a" * 17000}}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                SkillRequest.model_validate({"player": "Steve", "request_id": "one", "skill": "test_skill", **value})


class SkillExecutionTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[2] / "minecraft-bot"
        command = "const {EventEmitter}=require('node:events');const {createToolSystem}=require('./src/tools');const r=createToolSystem(new EventEmitter());console.log(JSON.stringify(r.list()));r.dispose();"
        cls.catalog = json.loads(subprocess.check_output(["node", "-e", command], cwd=root, text=True, encoding="utf-8"))

    async def asyncSetUp(self):
        self.node = MockNode(self.catalog)
        self.client = ToolClient(Settings("http://node", 1, "127.0.0.1", 8000), httpx.MockTransport(self.node.handle))
        self.executor = TaskManager(self.client, poll_interval=0.001, heartbeat_interval=0.01)
        self.registry = SkillRegistry()
        self.skill = TestSkill()
        self.registry.register(self.skill)
        self.manager = SkillManager(self.registry, self.executor)

    async def asyncTearDown(self):
        await self.executor.close()

    def request(self, **kwargs):
        return SkillRequest.model_validate({"player": "Steve", "request_id": "skill1", "skill": "test_skill", **kwargs})

    async def finish(self, request=None):
        record = await self.manager.submit(request or self.request())
        await asyncio.wait_for(asyncio.shield(self.executor._work[record["execution_id"]]), 2)
        return self.manager.view(record["execution_id"], "Steve")

    async def test_success_is_based_on_real_snapshot_delta_and_has_stable_result(self):
        result = await self.finish(self.request(arguments={"count": 3}))
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["success"])
        self.assertEqual(result["verification"]["checks"][0]["observed"], 3)
        self.assertEqual(len(result["steps"]), 1)
        self.assertFalse(self.node.leased)
        result["data"]["item"] = "fake"
        self.assertEqual(self.manager.view(result["execution_id"], "Steve")["data"]["item"], "crafting_table")

    async def test_tool_success_without_goal_evidence_is_not_skill_success(self):
        self.node.lying = True
        result = await self.finish()
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "GOAL_NOT_VERIFIED")
        self.assertFalse(result["verification"]["verified"])

    async def test_failed_tool_keeps_original_code_and_never_continues(self):
        self.node.fail_tool = "craft_item"
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "BLOCK_CHANGED")
        self.assertEqual(len(self.node.calls), 1)
        self.assertFalse(result["success"])

    async def test_preconditions_missing_materials_stop_before_game_modification(self):
        self.skill.mode = "materials"
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "MATERIALS_MISSING")
        self.assertEqual(self.node.calls, [])

    async def test_unknown_skill_bad_parameters_missing_tool_are_rejected_before_lease(self):
        for request, expected in [(self.request(skill="shell"), "UNKNOWN_SKILL"),
                                  (self.request(arguments={"count": "3"}), "INVALID_SKILL_ARGUMENT")]:
            with self.assertRaises(LLMError) as error:
                await self.manager.submit(request)
            self.assertEqual(error.exception.code, expected)
        self.node.catalog = [definition for definition in self.catalog if definition["name"] != "craft_item"]
        with self.assertRaises(LLMError) as error:
            await self.manager.submit(self.request())
        self.assertEqual(error.exception.code, "SKILL_TOOL_UNAVAILABLE")
        self.assertFalse(self.node.leased)

    async def test_runtime_revalidates_tool_name_and_arguments(self):
        for index, mode in enumerate(("bad_tool", "bad_arguments")):
            self.skill.mode = mode
            result = await self.finish(self.request(request_id=f"invalid{index}"))
            self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.node.calls, [])

    async def test_uncertain_and_lost_response_do_not_repeat_side_effect(self):
        self.node.transport_timeout = True
        result = await self.finish()
        self.assertTrue(result["success"])
        self.assertEqual(len(self.node.calls), 1)
        self.node.receipt_running = True
        result = await self.finish(self.request(request_id="uncertain"))
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["error"]["code"], "UNCONFIRMED_REQUEST")
        self.assertEqual(len(self.node.calls), 2)

    async def test_same_submission_and_operation_are_deduplicated(self):
        self.skill.mode = "repeat"
        result = await self.finish()
        again = await self.manager.submit(self.request())
        self.assertEqual(again["execution_id"], result["execution_id"])
        self.assertEqual(len(self.node.calls), 1)
        with self.assertRaises(LLMError) as error:
            await self.manager.submit(self.request(arguments={"count": 2}))
        self.assertEqual(error.exception.code, "REQUEST_CONFLICT")

    async def test_operation_id_with_changed_arguments_is_rejected(self):
        self.skill.mode = "conflict"
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "OPERATION_CONFLICT")
        self.assertEqual(len(self.node.calls), 1)

    async def test_uncertain_result_cannot_be_bypassed_with_new_operation_id(self):
        self.skill.mode = "catch_uncertain"
        self.node.uncertain = True
        result = await self.finish()
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["error"]["code"], "UNCONFIRMED_RESULT")
        self.assertEqual(len(self.node.calls), 1)

    async def test_pending_navigation_waits_original_task_and_obeys_call_limit(self):
        self.skill.mode = "walk"
        result = await self.finish()
        self.assertTrue(result["success"])
        self.assertEqual([name for name, _ in self.node.calls], ["goto_position", "get_movement_task", "get_movement_task", "craft_item"])
        result = await self.finish(self.request(request_id="limit", max_tool_calls=1))
        self.assertEqual(result["error"]["code"], "CALL_LIMIT")
        self.assertEqual([name for name, _ in self.node.calls].count("goto_position"), 2)

    async def test_total_timeout_and_cancel_release_lease(self):
        self.skill.mode = "sleep"
        result = await self.finish(self.request(timeout_seconds=0.03))
        self.assertEqual(result["error"]["code"], "TASK_TIMEOUT")
        self.assertFalse(self.node.leased)
        record = await self.manager.submit(self.request(request_id="cancel"))
        result = await self.manager.cancel(record["execution_id"], "Steve")
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(self.node.leased)
        self.assertEqual(self.node.calls, [])

    async def test_cancellation_during_navigation_prevents_later_crafting(self):
        self.skill.mode = "walk"
        self.node.pending_forever = True
        record = await self.manager.submit(self.request())
        await asyncio.wait_for(self.skill.entered.wait(), 1)
        result = await self.manager.cancel(record["execution_id"], "Steve")
        self.assertEqual(result["status"], "cancelled")
        self.assertNotIn("craft_item", [name for name, _ in self.node.calls])
        self.assertFalse(self.node.leased)

    async def test_skills_and_legacy_tasks_share_execution_slot_and_player_isolation(self):
        self.skill.mode = "sleep"
        record = await self.manager.submit(self.request())
        task = TaskRequest(player="Steve", request_id="legacy", steps=[{"step_id": "read", "tool": "query_status"}])
        with self.assertRaises(LLMError) as error:
            await self.executor.submit(task)
        self.assertEqual(error.exception.code, "TASK_BUSY")
        with self.assertRaises(LLMError):
            await self.manager.cancel(record["execution_id"], "Alex")
        await self.manager.cancel(record["execution_id"], "Steve")
        self.node.pending_forever = True
        task = TaskRequest(player="Steve", request_id="legacy", steps=[{"step_id": "walk", "tool": "goto_position", "arguments": {"x": 1, "y": 64, "z": 2}}])
        await self.executor.submit(task)
        with self.assertRaises(LLMError) as error:
            await self.manager.submit(self.request(request_id="second"))
        self.assertEqual(error.exception.code, "TASK_BUSY")

    async def test_disconnect_or_restart_prevents_goal_success(self):
        for index, flag in enumerate(("offline", "restart")):
            setattr(self.node, flag, True)
            result = await self.finish(self.request(request_id=f"lifecycle{index}"))
            self.assertFalse(result["success"])
            self.assertEqual(result["status"], "failed")
            setattr(self.node, flag, False)

    async def test_missing_criterion_and_unexpected_exceptions_are_safe(self):
        for index, mode in enumerate(("wrong_criterion", "unexpected")):
            self.skill.mode = mode
            result = await self.finish(self.request(request_id=f"exception{index}"))
            self.assertFalse(result["success"])
            self.assertEqual(result["status"], "uncertain")
            self.assertNotIn("unit-secret", json.dumps(result))

    async def test_parallel_tool_calls_are_rejected(self):
        self.skill.mode = "parallel"
        self.node.pending_forever = True
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "CONCURRENT_TOOL_CALL")
        self.assertNotIn("craft_item", [name for name, _ in self.node.calls])

    async def test_http_catalog_execute_progress_cancel_and_input_limits(self):
        self.skill.mode = "sleep"
        with patch.object(main, "skill_registry", self.registry), patch.object(main, "skill_manager", self.manager):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://agent") as client:
                self.assertEqual((await client.get("/api/agent/skills")).json()["data"]["skills"], self.registry.list())
                response = await client.post("/api/agent/skills/execute", json=self.request().model_dump())
                self.assertEqual(response.status_code, 202)
                execution_id = response.json()["data"]["execution_id"]
                self.assertFalse(response.json()["data"]["success"])
                path = f"/api/agent/skills/executions/{execution_id}"
                self.assertEqual((await client.get(path, params={"player": "Steve"})).status_code, 200)
                self.assertEqual((await client.get(path, params={"player": "Alex"})).status_code, 404)
                self.assertEqual((await client.post(path + "/cancel", json={"username": "Steve"})).json()["data"]["status"], "cancelled")
                for body, status in [(" " * 16385, 413), ("not json", 422)]:
                    self.assertEqual((await client.post("/api/agent/skills/execute", content=body)).status_code, status)
                self.assertEqual((await client.post("/api/agent/skills/execute", json=self.request(skill="not_registered").model_dump())).status_code, 404)

    async def test_skill_uses_real_node_http_registry_lease_and_execution(self):
        # 真实 HTTP 和真实 Node Registry，游戏数据来自测试 Bot，不连接 Minecraft。
        script = r"""
const {EventEmitter}=require('node:events');
const {Vec3}=require('vec3');
const {createToolSystem}=require('./src/tools');
const {createApiServer}=require('./src/httpServer');
const bot=new EventEmitter(), inventory=[];
bot.entity={position:new Vec3(0,64,0)}; bot.isAlive=true;
bot.inventory={items:()=>inventory};
bot.registry={itemsByName:{crafting_table:{id:1}}};
bot.recipesAll=()=>[{result:{count:1}}]; bot.recipesFor=bot.recipesAll;
bot.craft=async()=>inventory.push({name:'crafting_table',count:1,type:1});
const movement={getState:()=>({type:'idle'}),getTask:()=>null,stop:()=>({ok:true})};
const snapshot=()=>({availability:{inventory:'available',player:'available'},
  inventory:inventory.map((x,i)=>({...x,slot:i+9})),player:{position:{x:0,y:64,z:0},alive:true},timestamp:Date.now()});
const perception={getWorldSnapshot:snapshot};
bot.tools=createToolSystem(bot,{movement,perception,logger:{log(){},error(){}}});
bot.tools.guardMovement();bot.emit('spawn');
bot.mineMateStatus=()=>({connected:true,spawned:true});
const server=createApiServer({bot,movement,perception,logger:{log(){},error(){}}});
server.listen(0,'127.0.0.1',()=>console.log(server.address().port));
"""
        process = await asyncio.create_subprocess_exec(
            "node", "-e", script, cwd=Path(__file__).resolve().parents[2] / "minecraft-bot",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        executor = None
        try:
            port = int(await asyncio.wait_for(process.stdout.readline(), 5))
            executor = TaskManager(ToolClient(Settings(f"http://127.0.0.1:{port}", 1, "127.0.0.1", 8000)))
            manager = SkillManager(self.registry, executor)
            record = await manager.submit(self.request(arguments={"count": 2}))
            await asyncio.wait_for(executor._work[record["execution_id"]], 5)
            result = manager.view(record["execution_id"], "Steve")
            self.assertTrue(result["success"], result["error"])
            self.assertEqual(result["verification"]["checks"][0]["observed"], 2)
            self.assertEqual(len(result["steps"]), 1)
        finally:
            if executor:
                await executor.close()
            process.terminate()
            await process.communicate()


if __name__ == "__main__":
    unittest.main()
