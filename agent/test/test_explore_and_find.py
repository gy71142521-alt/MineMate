import asyncio
import copy
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from app import main
from app.config import Settings
from app.execution.manager import TaskManager
from app.llm_client import LLMError
from app.tool_client import ToolClient
from skills import SkillManager, SkillRequest
from skills.catalog import create_skill_registry
from skills.explore_and_find import ExploreAndFind, ExploreParameters
from test_execution import MockNode


class SearchNode(MockNode):
    def __init__(self, catalog):
        super().__init__(catalog)
        self.location = {"x": 0, "y": 64, "z": 0}
        self.blocks = [{"name": "oak_log", "position": {"x": 8, "y": 64, "z": 0}}]
        self.entities = [{"id": 42, "name": "cow", "type": "mob", "valid": True,
                          "position": {"x": 8, "y": 64, "z": 0}}]
        self.ambiguous = self.unknown = self.vanish = self.lie_arrival = False
        self.fail_navigation = self.fail_first = self.blocked = self.moving_entity = False
        self.find_count = 0
        self.moves = 0

    def world(self):
        world = super().world()
        world["player"].update(position=copy.deepcopy(self.location), alive=True)
        return world

    def handle(self, request):
        response = super().handle(request)
        if request.url.path != "/api/tools/execute":
            return response
        payload = json.loads(request.content)
        name, args = payload["tool"], payload["arguments"]
        result = response.json()["data"]
        data = result["data"]
        if name == "resolve_resource":
            data.update(ambiguous=self.ambiguous, candidates=[] if self.unknown else [
                {"kind": args["kind"], "name": "oak_log" if args["kind"] == "block" else "cow"}])
        elif name in ("find_block", "find_entity"):
            self.find_count += 1
            if self.moving_entity and name == "find_entity" and self.moves:
                self.entities[0]["position"]["x"] += 5
            entries = self.blocks if name == "find_block" else self.entities
            from skills.explore_and_find import distance
            data["blocks" if name == "find_block" else "entities"] = [
                {**copy.deepcopy(e), "distance": distance(self.location, e["position"])} for e in entries
                if distance(self.location, e["position"]) <= args["radius"]
                and not (self.vanish and self.find_count > 1)]
            data["truncated"] = False
        elif name == "find_interaction_positions":
            p = args["position"]
            data["candidates"] = [] if self.blocked else [
                {"position": {"x": p["x"] - 2, "y": p["y"], "z": p["z"]}},
                {"position": {"x": p["x"], "y": p["y"], "z": p["z"] - 2}}]
        elif name == "inspect_block":
            data.update(name="oak_log", position=args["position"], visible=not self.blocked)
        elif name == "goto_position":
            self.moves += 1
            if self.fail_navigation or (self.fail_first and self.moves == 1):
                result.update(success=False, data=None, error={"code": "NAVIGATION_FAILED", "message": "no path"})
            elif not self.lie_arrival and not self.pending_forever:
                self.location = {k: args[k] for k in ("x", "y", "z")}
        self.receipts[payload["requestId"]]["result"] = copy.deepcopy(result)
        return httpx.Response(200, json={"success": True, "data": result, "error": None})


class ExploreTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[2] / "minecraft-bot"
        command = "const {EventEmitter}=require('node:events');const {createToolSystem}=require('./src/tools');const r=createToolSystem(new EventEmitter());console.log(JSON.stringify(r.list()));r.dispose();"
        cls.catalog = json.loads(subprocess.check_output(["node", "-e", command], cwd=cls.root, text=True, encoding="utf-8"))

    async def asyncSetUp(self):
        self.node = SearchNode(self.catalog)
        client = ToolClient(Settings("http://node", 1, "127.0.0.1", 8000), httpx.MockTransport(self.node.handle))
        self.executor = TaskManager(client, poll_interval=0.001, heartbeat_interval=0.01)
        self.registry = create_skill_registry()
        self.manager = SkillManager(self.registry, self.executor)

    async def asyncTearDown(self):
        await self.executor.close()

    def request(self, arguments=None, **extra):
        return SkillRequest.model_validate({"player": "Steve", "request_id": "search1", "skill": "explore_and_find",
                                            "arguments": {"target_kind": "block", "target": "oak_log", **(arguments or {})}, **extra})

    async def finish(self, arguments=None, **extra):
        record = await self.manager.submit(self.request(arguments, **extra))
        await asyncio.wait_for(asyncio.shield(self.executor._work[record["execution_id"]]), 3)
        return self.manager.view(record["execution_id"], "Steve")

    async def test_production_catalog_and_strict_parameters(self):
        # 生产目录新增采集 Skill，原搜索 Skill 及其参数合同保持不变。
        self.assertEqual([s["name"] for s in self.registry.list()], ["explore_and_find", "gather_resource", "craft_item", "smelt_item", "mine_vein"])
        self.assertEqual(main.skill_registry.list(), self.registry.list())
        for args in ({"search_radius": 65}, {"search_radius": "16"}, {"max_waypoints": 9},
                     {"max_results": 9}, {"approach": 1}, {"target_kind": "item"}, {"target": "\n"},
                     {"shell": "run"}):
            with self.subTest(args=args), self.assertRaises(LLMError):
                await self.manager.submit(self.request(args))
        with self.assertRaises(ValidationError):
            self.request({"approach_distance": float("nan")})
        self.assertEqual(self.node.calls, [])

    async def test_search_only_fresh_confirmation_without_movement_or_inventory_changes(self):
        before = self.node.world()
        result = await self.finish()
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["selected"]["name"], "oak_log")
        self.assertEqual(result["data"]["selected"]["distance"], 8)
        self.assertEqual([n for n, _ in self.node.calls], ["resolve_resource", "find_block", "find_block"])
        self.assertEqual(self.node.world(), before)
        self.assertTrue(result["verification"]["verified"])

    async def test_block_approach_uses_standing_positions_and_waits_original_navigation(self):
        result = await self.finish({"approach": True})
        self.assertTrue(result["success"], result)
        names = [n for n, _ in self.node.calls]
        self.assertIn("find_interaction_positions", names)
        self.assertIn("inspect_block", names)
        self.assertEqual(names.count("goto_position"), 1)
        self.assertIn("get_movement_task", names)
        self.assertEqual(result["data"]["selected"]["distance"], 2)
        self.assertTrue(result["data"]["approached"])

    async def test_entity_search_and_approach_use_real_id(self):
        result = await self.finish({"target_kind": "entity", "target": "cow", "approach": True})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["selected"]["id"], 42)
        self.assertNotIn("attack_entity", [n for n, _ in self.node.calls])

    async def test_entity_distance_includes_vertical_height_and_excludes_invalid(self):
        self.node.entities = [{"id": 42, "name": "cow", "valid": True, "position": {"x": 0, "y": 74, "z": 0}},
                              {"id": 43, "name": "cow", "valid": False, "position": {"x": 0, "y": 64, "z": 0}}]
        result = await self.finish({"target_kind": "entity", "target": "cow", "search_radius": 5})
        self.assertEqual(result["error"]["code"], "TARGET_NOT_FOUND")
        result = await self.finish({"target_kind": "entity", "target": "cow"}, request_id="vertical")
        self.assertEqual(result["data"]["selected"]["distance"], 10)

    async def test_missing_and_ambiguous_names_request_clarification_without_moving(self):
        for i, flag in enumerate(("unknown", "ambiguous")):
            self.node.unknown = self.node.ambiguous = False
            setattr(self.node, flag, True)
            result = await self.finish(request_id=f"resolve{i}")
            self.assertEqual(result["error"]["code"], "TARGET_UNKNOWN" if flag == "unknown" else "TARGET_AMBIGUOUS")
            self.assertEqual(result["data"]["stage"], "clarification_required")
        self.assertEqual([n for n, _ in self.node.calls], ["resolve_resource", "resolve_resource"])

    async def test_not_found_preserves_search_evidence_and_never_claims_full_coverage(self):
        self.node.blocks = []
        result = await self.finish({"search_radius": 64})
        self.assertEqual(result["error"]["code"], "TARGET_NOT_FOUND")
        self.assertEqual(result["data"]["scan_radius"], 16)
        self.assertEqual(len(result["data"]["scans"]), 1)
        self.assertFalse(result["data"]["coverage_complete"])
        self.assertEqual(self.node.moves, 0)

    async def test_bounded_exploration_can_find_target_beyond_initial_scan(self):
        self.node.blocks[0]["position"]["x"] = 28
        result = await self.finish({"search_radius": 40, "explore": True, "max_waypoints": 2})
        self.assertTrue(result["success"], result)
        self.assertEqual(len(result["data"]["waypoints"]), 1)
        self.assertEqual(len(result["data"]["scans"]), 2)
        self.assertEqual(self.node.moves, 1)

    async def test_exploration_failure_is_bounded_and_does_not_retry_same_waypoint(self):
        self.node.blocks = []
        self.node.fail_navigation = True
        result = await self.finish({"search_radius": 40, "explore": True, "max_waypoints": 3})
        self.assertEqual(result["error"]["code"], "TARGET_NOT_FOUND")
        self.assertEqual(self.node.moves, 3)
        waypoints = result["data"]["waypoints"]
        self.assertEqual(len({tuple(p["position"].values()) for p in waypoints}), 3)
        self.assertTrue(all(p["status"] == "failed" for p in waypoints))

    async def test_failed_standing_candidate_can_be_replaced(self):
        self.node.fail_first = True
        result = await self.finish({"approach": True})
        self.assertTrue(result["success"], result)
        self.assertEqual(self.node.moves, 2)

    async def test_target_disappears_or_is_blocked_without_false_success(self):
        for i, flag in enumerate(("vanish", "blocked")):
            setattr(self.node, flag, True)
            result = await self.finish({"approach": flag == "blocked"}, request_id=f"missing{i}")
            self.assertFalse(result["success"])
            self.assertEqual(result["error"]["code"], "TARGET_UNREACHABLE_OR_LOST")
            setattr(self.node, flag, False)
            self.node.find_count = 0

    async def test_navigation_success_without_actual_distance_is_not_success(self):
        self.node.lie_arrival = True
        result = await self.finish({"approach": True})
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "TARGET_UNREACHABLE_OR_LOST")

    async def test_moving_entity_is_not_chased_forever(self):
        self.node.moving_entity = True
        result = await self.finish({"target_kind": "entity", "target": "cow", "approach": True})
        self.assertFalse(result["success"])
        self.assertLessEqual(self.node.moves, 2)

    async def test_timeout_cancel_and_call_limit_stop_future_operations(self):
        self.node.pending_forever = True
        result = await self.finish({"approach": True}, timeout_seconds=0.03)
        self.assertEqual(result["error"]["code"], "TASK_TIMEOUT")
        self.assertFalse(self.node.leased)
        self.node.pending_forever = False
        result = await self.finish(request_id="budget", max_tool_calls=1)
        self.assertEqual(result["error"]["code"], "CALL_LIMIT")
        self.node.pending_forever = True
        record = await self.manager.submit(self.request({"approach": True}, request_id="cancel"))
        async with asyncio.timeout(2):
            while not self.node.leased or self.node.calls[-1][0] != "get_movement_task":
                await asyncio.sleep(0.001)
        result = await self.manager.cancel(record["execution_id"], "Steve")
        count = len(self.node.calls)
        await asyncio.sleep(0.01)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(len(self.node.calls), count)
        self.assertFalse(self.node.leased)

    async def test_http_registered_skill_submit_poll_and_deduplicate(self):
        with patch.object(main, "skill_manager", self.manager):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://agent") as client:
                catalog = (await client.get("/api/agent/skills")).json()["data"]["skills"]
                self.assertEqual(catalog[0]["name"], "explore_and_find")
                response = await client.post("/api/agent/skills/execute", json=self.request().model_dump())
                self.assertEqual(response.status_code, 202)
                execution_id = response.json()["data"]["execution_id"]
                await self.executor._work[execution_id]
                result = (await client.get(f"/api/agent/skills/executions/{execution_id}", params={"player": "Steve"})).json()["data"]
                self.assertTrue(result["success"])
                again = await client.post("/api/agent/skills/execute", json=self.request().model_dump())
                self.assertEqual(again.json()["data"]["execution_id"], execution_id)

    async def test_independent_verifier_rejects_changed_dimension_or_distance(self):
        result = await self.finish({"approach": True})
        after = self.node.world()
        after["player"]["position"]["y"] += 10
        checks = ExploreAndFind().verify(ExploreParameters(target_kind="block", target="oak_log", approach=True),
                                        result["data"], self.node.world(), after).checks
        self.assertFalse(next(c.passed for c in checks if c.criterion == "approach_verified"))
        after["player"]["dimension"] = "the_nether"
        checks = ExploreAndFind().verify(ExploreParameters(target_kind="block", target="oak_log"),
                                        result["data"], self.node.world(), after).checks
        self.assertFalse(next(c.passed for c in checks if c.criterion == "same_dimension"))

    async def test_results_limit_and_origin_range_are_enforced(self):
        self.node.blocks = [{"name": "oak_log", "position": {"x": i, "y": 64, "z": 0}} for i in range(2, 8)]
        result = await self.finish({"max_results": 2, "search_radius": 4})
        self.assertTrue(result["success"], result)
        self.assertEqual(len(result["data"]["results"]), 2)
        self.assertTrue(result["data"]["results_truncated"])
        self.assertEqual(result["data"]["scans"][0]["matches"], 3)

    async def test_disconnect_and_node_restart_stop_search(self):
        for i, flag in enumerate(("offline", "restart")):
            setattr(self.node, flag, True)
            result = await self.finish(request_id=f"disconnect{i}")
            self.assertFalse(result["success"])
            self.assertNotIn("goto_position", [name for name, _ in self.node.calls])
            setattr(self.node, flag, False)

    async def test_real_node_http_search_and_approach_without_minecraft(self):
        # 真实注册表、HTTP、租约、站位检查和导航查询；游戏世界为受控测试场景。
        script = r"""
const {EventEmitter}=require('node:events');
const {Vec3}=require('vec3');
const {createToolSystem}=require('./src/tools');
const {createApiServer}=require('./src/httpServer');
const bot=new EventEmitter(), target=new Vec3(8,64,0);
bot.entity={id:1,position:new Vec3(0,64,0)};bot.entities={};bot.isAlive=true;
bot.game={dimension:'overworld',minY:-64,height:384};
bot.registry={blocksByName:{oak_log:{name:'oak_log',id:1,displayName:'Oak Log'}},entitiesByName:{}};
bot.inventory={items:()=>[]};bot.findBlocks=()=>[target];
bot.blockAt=(p)=>({name:p.equals(target)?'oak_log':p.y===63?'stone':'air',
 position:p.floored(),boundingBox:p.y===63?'block':'empty',shapes:p.y===63?[[0,0,0,1,1,1]]:[]});
bot.world={raycast:()=>({position:target})};bot.canDigBlock=()=>true;bot.canSeeBlock=()=>true;
let task;
const movement={getState:()=>({type:'idle'}),stop:()=>({ok:true}),
 goTo:(who,x,y,z)=>{bot.entity.position=new Vec3(x,y,z);task={taskId:7,status:'arrived'};return {ok:true,taskId:7};},
 getTask:()=>task};
const perception={getWorldSnapshot:()=>({availability:{player:'available',inventory:'available'},
 player:{position:{x:bot.entity.position.x,y:64,z:bot.entity.position.z},alive:true,dimension:'overworld'},
 inventory:[],nearbyBlocks:[],nearbyEntities:[],timestamp:Date.now()})};
bot.tools=createToolSystem(bot,{movement,perception,logger:{log(){},error(){}}});
bot.tools.guardMovement();bot.emit('spawn');bot.mineMateStatus=()=>({connected:true,spawned:true});
const server=createApiServer({bot,movement,perception,logger:{log(){},error(){}}});
server.listen(0,'127.0.0.1',()=>console.log(server.address().port));
"""
        process = await asyncio.create_subprocess_exec("node", "-e", script, cwd=self.root,
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        executor = None
        try:
            port = int(await asyncio.wait_for(process.stdout.readline(), 5))
            # 测试专用短轮询；实际超时/取消由专项测试验证，不依赖本机 HTTP 速度。
            executor = TaskManager(ToolClient(Settings(f"http://127.0.0.1:{port}", 1, "127.0.0.1", 8000)), poll_interval=0.001)
            manager = SkillManager(create_skill_registry(), executor)
            record = await manager.submit(self.request({"approach": True}))
            await asyncio.wait_for(executor._work[record["execution_id"]], 30)
            result = manager.view(record["execution_id"], "Steve")
            self.assertTrue(result["success"], result)
            self.assertTrue(result["verification"]["verified"])
            names = [entry["tool"] for entry in result["steps"]]
            self.assertIn("find_interaction_positions", names)
            self.assertEqual(names.count("goto_position"), 1)
            self.assertNotIn("mine_block", names)
        finally:
            if executor:
                await executor.close()
            process.terminate()
            await process.communicate()


if __name__ == "__main__":
    unittest.main()
