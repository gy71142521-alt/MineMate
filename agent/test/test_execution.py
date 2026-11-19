import asyncio
import copy
import json
from pathlib import Path
import subprocess
import unittest

import httpx
from pydantic import ValidationError

from app import main
from app.config import Settings
from app.execution.manager import TaskManager
from app.execution.models import TaskRequest
from app.execution.references import ReferenceError, resolve
from app.llm_client import LLMError
from app.tool_client import ToolClient


INSTANCE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
LEASE = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class MockNode:
    def __init__(self, catalog):
        self.catalog = catalog
        self.generation = 0
        self.instance = INSTANCE
        self.leased = False
        self.calls = []
        self.receipts = {}
        self.inventory = {}
        self.nav_polls = self.smelt_polls = 0
        self.pending_forever = False
        self.uncertain = self.lying = self.offline = self.restart = False
        self.transport_timeout = False
        self.receipt_running = False
        self.fail_tool = None

    def world(self):
        return {"availability": {"player": "available", "inventory": "available"},
                "player": {"position": {"x": 1, "y": 64, "z": 2}, "dimension": "overworld"},
                "inventory": [{"name": name, "count": amount, "slot": i + 9} for i, (name, amount) in enumerate(self.inventory.items())],
                "nearbyEntities": [], "nearbyBlocks": [], "timestamp": 1}

    def handle(self, request):
        payload = json.loads(request.content) if request.content else {}

        def ok(data):
            return httpx.Response(200, json={"success": True, "data": data, "error": None})

        def error(code="STALE_TASK"):
            return httpx.Response(409, json={"success": False, "data": None, "error": {"code": code, "message": "fixed"}})

        if request.url.path == "/api/tools":
            return ok({"tools": self.catalog, "instanceId": self.instance, "generation": self.generation})
        if request.url.path == "/api/world":
            return error("BOT_NOT_READY") if self.offline else ok(self.world())
        if request.url.path == "/api/tools/lease":
            if payload["generation"] != self.generation or payload["instanceId"] != self.instance:
                return error()
            if payload["action"] == "acquire":
                if self.leased:
                    return error("BUSY")
                self.leased = True
            elif not self.leased or payload["leaseId"] != LEASE:
                return error()
            elif payload["action"] == "release":
                self.leased = False
            return ok({"instanceId": self.instance, "lease": {"leaseId": LEASE} if self.leased else None})
        if request.url.path.startswith("/api/tools/requests/"):
            receipt = self.receipts.get(request.url.path.rsplit("/", 1)[1])
            return ok(receipt) if receipt else error("REQUEST_NOT_FOUND")
        if request.url.path == "/api/tools/execute":
            if not self.leased or payload.get("leaseId") != LEASE or payload.get("instanceId") != self.instance or payload["generation"] != self.generation:
                return error()
            name, args = payload["tool"], payload["arguments"]
            self.calls.append((name, copy.deepcopy(args)))
            if payload["requestId"] in self.receipts:
                raise AssertionError("executor must not resubmit")
            data = {"status": "completed"}
            if name == "query_status":
                data["position"] = {"x": 1, "y": 64, "z": 2}
            elif name == "goto_position":
                data = {"status": "pending", "movement": {"taskId": 7}}
            elif name == "get_movement_task":
                self.nav_polls += 1
                data = {"status": "pending" if self.pending_forever or self.nav_polls < 2 else "completed", "movement": {"taskId": 7, "status": "running" if self.pending_forever else "arrived"}}
            elif name == "craft_item":
                if not self.lying:
                    self.inventory[args["item_name"]] = self.inventory.get(args["item_name"], 0) + args["count"]
                if self.uncertain:
                    data["status"] = "uncertain"
            elif name == "smelt_item":
                if args["action"] == "start":
                    data = {"status": "pending", "expectedOutput": "iron_ingot", "expectedCount": args.get("count", 1)}
                elif args["action"] == "status":
                    self.smelt_polls += 1
                    ready = self.smelt_polls >= 2
                    data = {"status": "completed" if ready else "pending", "outputReady": ready}
                else:
                    self.inventory[args["item_name"]] = self.inventory.get(args["item_name"], 0) + args["count"]
            result = {"success": name != self.fail_tool, "tool": name, "data": data, "error": None}
            if name == self.fail_tool:
                result.update(data=None, error={"code": "BLOCK_CHANGED", "message": "fixed"})
            self.receipts[payload["requestId"]] = {"requestId": payload["requestId"], "result": result, "running": self.receipt_running, "instanceId": self.instance}
            if self.restart:
                self.instance = "cccccccc-cccc-cccc-cccc-cccccccccccc"
                self.leased = False
            if self.transport_timeout and name == "craft_item":
                raise httpx.ReadTimeout("simulated lost response")
            return ok(result)
        raise AssertionError(request.url.path)


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # 使用真实 Node 注册目录，不在 Python 测试中复制游戏工具 Schema。
        root = Path(__file__).resolve().parents[2] / "minecraft-bot"
        command = "const {EventEmitter}=require('node:events');const {createToolSystem}=require('./src/tools');const r=createToolSystem(new EventEmitter());console.log(JSON.stringify(r.list()));r.dispose();"
        cls.catalog = json.loads(subprocess.check_output(["node", "-e", command], cwd=root, text=True, encoding="utf-8"))

    async def asyncSetUp(self):
        self.node = MockNode(self.catalog)
        self.client = ToolClient(Settings("http://node", 1, "127.0.0.1", 8000), httpx.MockTransport(self.node.handle))
        self.manager = TaskManager(self.client, poll_interval=0.001, heartbeat_interval=0.01)

    async def asyncTearDown(self):
        await self.manager.close()

    def request(self, steps=None, **kwargs):
        return TaskRequest.model_validate({"player": "Steve", "request_id": "test1", "steps": steps or [
            {"step_id": "craft", "tool": "craft_item", "arguments": {"item_name": "crafting_table", "count": 1}}],
            "verify_inventory": [{"item": "crafting_table", "count": 1}], **kwargs})

    async def finish(self, request):
        submitted = await self.manager.submit(request)
        await asyncio.wait_for(asyncio.shield(self.manager._work[submitted["task_id"]]), 2)
        return self.manager.view(submitted["task_id"])

    async def test_serial_navigation_and_recipe_result_reference(self):
        steps = [{"step_id": "where", "tool": "query_status"},
                 {"step_id": "walk", "tool": "goto_position", "arguments": {
                     axis: {"$ref": {"step": "where", "path": ["data", "position", axis]}} for axis in ("x", "y", "z")}},
                 {"step_id": "make", "tool": "craft_item", "arguments": {"item_name": "crafting_table", "count": 1}}]
        result = await self.finish(self.request(steps))
        self.assertEqual(result["status"], "completed")
        names = [name for name, _ in self.node.calls]
        self.assertEqual(names, ["query_status", "goto_position", "get_movement_task", "get_movement_task", "craft_item"])
        self.assertTrue(result["verification"]["verified"])
        self.assertFalse(self.node.leased)

    async def test_smelt_start_status_take_once_and_verify_inventory(self):
        result = await self.finish(self.request([{"step_id": "smelt", "tool": "smelt_item", "arguments": {
            "position": {"x": 1, "y": 64, "z": 2}, "action": "start", "item_name": "raw_iron", "fuel_name": "coal", "count": 2}}],
            verify_inventory=[{"item": "iron_ingot", "count": 2}]))
        self.assertEqual(result["status"], "completed")
        self.assertEqual([args["action"] for _, args in self.node.calls], ["start", "status", "status", "take"])
        self.assertEqual(self.node.inventory["iron_ingot"], 2)

    async def test_same_submission_deduplicated_and_conflict_rejected(self):
        request = self.request()
        result = await self.finish(request)
        again = await self.manager.submit(request)
        self.assertEqual(again["task_id"], result["task_id"])
        self.assertEqual(len(self.node.calls), 1)
        with self.assertRaises(LLMError) as error:
            await self.manager.submit(self.request(verify_inventory=[]))
        self.assertEqual(error.exception.code, "REQUEST_CONFLICT")

    async def test_validation_rejects_unknown_tools_bad_args_future_refs_before_lease(self):
        cases = [("shell", {}), ("goto_position", {"x": True, "y": 1, "z": 2}),
                 ("goto_position", {"x": {"$ref": {"step": "later", "path": ["x"]}}, "y": 1, "z": 2}),
                 ("follow_player", {}), ("stop_movement", {})]
        for tool, args in cases:
            with self.subTest(tool=tool), self.assertRaises(LLMError):
                await self.manager.submit(self.request([{"step_id": "one", "tool": tool, "arguments": args}]))
            self.assertFalse(self.node.leased)
        self.assertEqual(self.node.calls, [])

    async def test_runtime_reference_validated_against_actual_tool_schema(self):
        steps = [{"step_id": "where", "tool": "query_status"},
                 {"step_id": "walk", "tool": "goto_position", "arguments": {
                     "x": {"$ref": {"step": "where", "path": ["data", "position"]}}, "y": 64, "z": 2}}]
        result = await self.finish(self.request(steps))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(self.node.calls), 1)

    async def test_tool_error_does_not_execute_later_steps(self):
        self.node.fail_tool = "craft_item"
        steps = self.request().steps[0].model_dump()
        result = await self.finish(self.request([steps, {"step_id": "later", "tool": "query_status"}]))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(self.node.calls), 1)
        self.assertEqual(result["steps"][0]["status"], "failed")

    async def test_uncertain_observed_not_repeated_or_marked_completed(self):
        self.node.uncertain = True
        result = await self.finish(self.request())
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(len(self.node.calls), 1)
        self.assertIsNotNone(result["ledger"][0]["after"])
        self.assertIsNone(result["verification"])

    async def test_lost_response_recovered_only_from_original_receipt(self):
        self.node.transport_timeout = True
        result = await self.finish(self.request())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.node.calls), 1)

    async def test_inflight_receipt_stays_uncertain_and_is_observed(self):
        self.node.transport_timeout = self.node.receipt_running = True
        result = await self.finish(self.request())
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["ledger"][0]["status"], "uncertain")
        self.assertIsNotNone(result["ledger"][0]["after"])
        self.assertEqual(len(self.node.calls), 1)

    async def test_forged_success_does_not_verify_goal(self):
        self.node.lying = True
        result = await self.finish(self.request())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "GOAL_NOT_VERIFIED")

    async def test_no_final_condition_finishes_unverified(self):
        result = await self.finish(self.request(verify_inventory=[]))
        self.assertEqual(result["status"], "finished_unverified")
        self.assertFalse(result["verification"]["verified"])

    async def test_wait_timeout_and_call_budget_never_restart_navigation(self):
        self.node.pending_forever = True
        step = {"step_id": "walk", "tool": "goto_position", "arguments": {"x": 1, "y": 64, "z": 2}, "wait_seconds": 0.005}
        result = await self.finish(self.request([step]))
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(sum(name == "goto_position" for name, _ in self.node.calls), 1)
        self.node.calls.clear()
        result = await self.finish(self.request([step], request_id="budget", max_tool_calls=1))
        self.assertEqual(result["error"]["code"], "CALL_LIMIT")
        self.assertEqual(len(self.node.calls), 1)

    async def test_task_timeout_and_cancellation_release_control(self):
        self.node.pending_forever = True
        steps = [{"step_id": "walk", "tool": "goto_position", "arguments": {"x": 1, "y": 64, "z": 2}}]
        result = await self.finish(self.request(steps, timeout_seconds=0.005))
        self.assertEqual(result["error"]["code"], "TASK_TIMEOUT")
        self.assertFalse(self.node.leased)
        submitted = await self.manager.submit(self.request(steps, request_id="cancel"))
        await asyncio.sleep(0.005)
        cancelled = await self.manager.cancel(submitted["task_id"], "Steve")
        calls = len(self.node.calls)
        await asyncio.sleep(0.01)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(len(self.node.calls), calls)
        self.assertFalse(self.node.leased)

    async def test_immediate_cancel_and_shutdown_before_coroutine_starts(self):
        submitted = await self.manager.submit(self.request())
        self.assertEqual((await self.manager.cancel(submitted["task_id"], "Steve"))["status"], "cancelled")
        self.assertFalse(self.node.leased)
        await self.manager.submit(self.request(request_id="shutdown"))
        await self.manager.close()
        self.assertFalse(self.node.leased)
        self.assertEqual(self.node.calls, [])

    async def test_bot_offline_or_node_restarted_stops_sequence(self):
        self.node.offline = True
        result = await self.finish(self.request())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.node.calls, [])
        self.node.offline = False
        self.node.restart = True
        result = await self.finish(self.request(request_id="restart"))
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["verification"])

    async def test_single_task_slot_lease_loss_and_player_isolation(self):
        self.node.pending_forever = True
        steps = [{"step_id": "walk", "tool": "goto_position", "arguments": {"x": 1, "y": 64, "z": 2}}]
        submitted = await self.manager.submit(self.request(steps))
        with self.assertRaises(LLMError):
            await self.manager.submit(self.request(request_id="new"))
        with self.assertRaises(LLMError):
            await self.manager.cancel(submitted["task_id"], "Alex")
        await asyncio.sleep(0.005)
        self.node.generation += 1
        self.node.leased = False
        await asyncio.wait_for(self.manager._work[submitted["task_id"]], 1)
        self.assertEqual(self.manager.view(submitted["task_id"])["status"], "failed")

    async def test_background_http_returns_202_progress_cancel_and_body_limit(self):
        original = main.task_manager
        main.task_manager = self.manager
        self.node.pending_forever = True
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://agent") as client:
                steps = [{"step_id": "walk", "tool": "goto_position", "arguments": {"x": 1, "y": 64, "z": 2}}]
                response = await client.post("/api/agent/tasks", json=self.request(steps).model_dump())
                self.assertEqual(response.status_code, 202)
                task_id = response.json()["data"]["task_id"]
                self.assertEqual((await client.get("/health")).status_code, 200)
                self.assertEqual((await client.get(f"/api/agent/tasks/{task_id}?player=Steve")).status_code, 200)
                self.assertEqual((await client.get(f"/api/agent/tasks/{task_id}?player=Alex")).status_code, 404)
                stopped = await client.post(f"/api/agent/tasks/{task_id}/cancel", json={"username": "Steve"})
                self.assertEqual(stopped.json()["data"]["status"], "cancelled")
                self.assertEqual((await client.post("/api/agent/tasks", content=" " * 16385)).status_code, 413)
                self.assertEqual((await client.post("/api/agent/tasks", json={"player": "bad"})).status_code, 422)
        finally:
            main.task_manager = original

    async def test_real_node_http_registry_navigation_and_crafting_chain(self):
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
let task=null, last=null, id=0;
const movement={getState:()=>({type:task?'goto':'idle',taskId:task?.taskId}),
  getTask:()=>last,stop:()=>{task=null;return {ok:true}},
  goTo:()=>{task=last={taskId:++id,status:'running'};setTimeout(()=>{last={taskId:id,status:'arrived'};task=null},30);return {ok:true,taskId:id}}};
const snapshot=()=>({availability:{inventory:'available',player:'available'},inventory:inventory.map((x,i)=>({...x,slot:i+9})),player:{position:{x:0,y:64,z:0}},nearbyEntities:[],nearbyBlocks:[],timestamp:Date.now()});
const perception={getWorldSnapshot:snapshot};
bot.tools=createToolSystem(bot,{movement,perception,logger:{log(){},error(){}}});
bot.tools.guardMovement();bot.emit('spawn');
bot.mineMateStatus=()=>({connected:true,spawned:true});
const server=createApiServer({bot,movement,perception,logger:{log(){},error(){}}});
server.listen(0,'127.0.0.1',()=>console.log(server.address().port));
"""
        process = await asyncio.create_subprocess_exec("node", "-e", script, cwd=Path(__file__).resolve().parents[2] / "minecraft-bot",
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        manager = None
        try:
            port = int(await asyncio.wait_for(process.stdout.readline(), 3))
            manager = TaskManager(ToolClient(Settings(f"http://127.0.0.1:{port}", 1, "127.0.0.1", 8000)), poll_interval=0.01)
            steps = [{"step_id": "walk", "tool": "goto_position", "arguments": {"x": 1, "y": 64, "z": 2, "wait_seconds": 0}},
                     {"step_id": "make", "tool": "craft_item", "arguments": {"item_name": "crafting_table", "count": 1}}]
            task = await manager.submit(self.request(steps))
            await asyncio.wait_for(manager._work[task["task_id"]], 5)
            result = manager.view(task["task_id"])
            self.assertEqual(result["status"], "completed", result.get("error"))
            self.assertTrue(result["verification"]["verified"])
            self.assertEqual(sum(item["tool"] == "goto_position" for item in result["ledger"]), 1)
        finally:
            if manager:
                await manager.close()
            process.terminate()
            await process.communicate()


class ReferenceTests(unittest.TestCase):
    def test_references_reject_code_future_keys_negative_index_and_missing_path(self):
        for ref in [{"step": "future", "path": ["data"]}, {"step": "one", "path": ["__class__"]},
                    {"step": "one", "path": ["data", -1]}, {"step": "one", "path": ["no_such_field"]}]:
            with self.subTest(ref=ref), self.assertRaises(ReferenceError):
                resolve({"$ref": ref}, {"one": {"data": []}}, {"one"})
        source = {"one": {"data": [{"x": 1}]}}
        result = resolve({"$ref": {"step": "one", "path": ["data", 0]}}, source, {"one"})
        result["x"] = 99
        self.assertEqual(source["one"]["data"][0]["x"], 1)

    def test_request_schema_step_limits_duplicate_ids_extra_fields(self):
        base = {"player": "Steve", "request_id": "one", "steps": [{"step_id": "a", "tool": "query_status"}]}
        for bad in [{**base, "steps": base["steps"] * 2}, {**base, "steps": base["steps"] * 25},
                    {**base, "timeout_seconds": 601}, {**base, "code": "eval"}]:
            with self.assertRaises(ValidationError):
                TaskRequest.model_validate(bad)
