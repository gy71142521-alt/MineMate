import asyncio
import copy
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import httpx

from app import main
from app.config import Settings
from app.execution.manager import TaskManager
from app.llm_client import LLMError
from app.tool_client import ToolClient
from skills import SkillManager, SkillRequest
from skills.catalog import create_skill_registry
from skills.explore_and_find import distance
from skills.gather_resource import GatherParameters, GatherResource
from test_execution import MockNode


class GatheringNode(MockNode):
    def __init__(self, catalog):
        super().__init__(catalog)
        self.location = {"x": 0, "y": 64, "z": 0}
        self.blocks = [{"name": "oak_log", "position": {"x": i, "y": 64, "z": 0}} for i in range(2, 6)]
        self.item_name = "oak_log"
        self.block_name = "oak_log"
        self.source_available = True
        self.source_mismatch = self.ambiguous = False
        self.auto_pickup = self.no_pickup = self.fake_collect = self.full = False
        self.uncertain_mining = self.fake_removal = self.transport_lost = False
        self.mine_error = None
        self.blocked = self.no_stands = self.lie_arrival = False
        self.pickup_blocked = self.item_entity_visible = False
        self.fail_navigation = False
        self.changed = False
        self.pending_drop = 0
        self.missing_count = 0

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
        if not result["success"]:
            return response
        data = result["data"]

        def fail(code):
            result.update(success=False, data=None, error={"code": code, "message": "controlled failure"})

        if name == "resolve_resource":
            resolved = self.block_name if args["kind"] == "block" else args["query"]
            data.update(ambiguous=self.ambiguous, candidates=[{"kind": args["kind"], "name": resolved}])
        elif name == "inspect_mining_sources":
            data.update(available=self.source_available, sources=[] if self.source_mismatch or not self.source_available else [{"name": self.block_name}], truncated=False)
        elif name == "nearby_entities":
            data.update(available=True, truncated=False, items=([{"id": 900, "itemName": self.item_name,
                "itemCount": 1, "position": {"x": 2, "y": 64, "z": 0}}] if self.item_entity_visible else []))
        elif name == "find_block":
            data.update(blocks=[{**copy.deepcopy(b), "distance": distance(self.location, b["position"])} for b in self.blocks
                                if b["name"] == args["block_name"] and distance(self.location, b["position"]) <= args["radius"]], truncated=False)
        elif name == "inspect_block":
            block = next((b for b in self.blocks if b["position"] == args["position"]), None)
            if distance(self.location, args["position"]) > 4.5:
                fail("OUT_OF_RANGE")
            else:
                data.update(name=block["name"] if block and not self.changed else "air", visible=not self.blocked,
                            diggable=True, position=args["position"])
        elif name == "find_interaction_positions":
            p = args["position"]
            data["candidates"] = [] if self.no_stands else [{"position": {"x": p["x"] - 2, "y": p["y"], "z": p["z"]}},
                                                           {"position": {"x": p["x"], "y": p["y"], "z": p["z"] - 2}}]
        elif name == "clear_path":
            if self.blocked:
                self.blocked = False
                data.update(cleared=1, clearedBlocks=[{"name": "stone", "position": args["position"]}], remainingBlocked=False)
            elif self.pickup_blocked:
                self.pickup_blocked = False
                data.update(cleared=1, clearedBlocks=[{"name": "stone", "position": args["position"]}], remainingBlocked=False)
            else:
                data.update(cleared=0, clearedBlocks=[], remainingBlocked=False)
        elif name == "goto_position":
            if self.fail_navigation:
                fail("NAVIGATION_FAILED")
            elif not self.lie_arrival and not self.pending_forever:
                self.location = {k: args[k] for k in ("x", "y", "z")}
        elif name == "mine_block":
            if self.mine_error:
                fail(self.mine_error)
            else:
                if not self.fake_removal:
                    self.blocks = [b for b in self.blocks if b["position"] != args["position"]]
                    if self.auto_pickup:
                        self.inventory[self.item_name] = self.inventory.get(self.item_name, 0) + 1
                    else:
                        self.pending_drop += 1
                data.update(blockRemoved=not self.fake_removal, pickupVerified=False,
                            status="uncertain" if self.uncertain_mining else "completed")
        elif name == "collect_item":
            if self.full:
                fail("INVENTORY_FULL")
            elif self.no_pickup or self.missing_count:
                self.missing_count = max(0, self.missing_count - 1)
                fail("ITEM_ENTITY_MISSING")
            else:
                if not self.fake_collect:
                    self.inventory[self.item_name] = self.inventory.get(self.item_name, 0) + self.pending_drop
                    self.pending_drop = 0
                data.update(collected=1)
        self.receipts[payload["requestId"]]["result"] = copy.deepcopy(result)
        if name == "mine_block" and self.transport_lost:
            raise httpx.ReadTimeout("controlled lost response")
        return httpx.Response(200, json={"success": True, "data": result, "error": None})


class GatherTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[2] / "minecraft-bot"
        command = "const {EventEmitter}=require('node:events');const {createToolSystem}=require('./src/tools');const r=createToolSystem(new EventEmitter());console.log(JSON.stringify(r.list()));r.dispose();"
        cls.catalog = json.loads(subprocess.check_output(["node", "-e", command], cwd=cls.root, text=True, encoding="utf-8"))

    async def asyncSetUp(self):
        self.node = GatheringNode(self.catalog)
        client = ToolClient(Settings("http://node", 1, "127.0.0.1", 8000), httpx.MockTransport(self.node.handle))
        self.executor = TaskManager(client, poll_interval=0.001, heartbeat_interval=0.01)
        self.registry = create_skill_registry()
        self.manager = SkillManager(self.registry, self.executor)

    async def asyncTearDown(self):
        await self.executor.close()

    def request(self, arguments=None, **extra):
        return SkillRequest.model_validate({"player": "Steve", "request_id": "gather1", "skill": "gather_resource",
                                            "arguments": {"resource": self.node.item_name, "count": 2, **(arguments or {})}, **extra})

    async def finish(self, arguments=None, **extra):
        record = await self.manager.submit(self.request(arguments, **extra))
        await asyncio.wait_for(asyncio.shield(self.executor._work[record["execution_id"]]), 4)
        return self.manager.view(record["execution_id"], "Steve")

    def names(self):
        return [name for name, _ in self.node.calls]

    async def test_registry_and_strict_parameters(self):
        self.assertEqual([s["name"] for s in self.registry.list()], ["explore_and_find", "gather_resource", "craft_item", "smelt_item", "mine_vein"])
        for args in ({"count": 0}, {"count": 257}, {"count": True}, {"count": "2"}, {"search_radius": 17},
                     {"max_blocks": 65}, {"resource": "\n"}, {"block_name": ""}, {"shell": "run"}):
            with self.subTest(args=args), self.assertRaises(LLMError):
                await self.manager.submit(self.request(args))
        self.assertEqual(self.node.calls, [])
        self.assertEqual(GatherParameters(resource="oak_log", count=256).count, 256)

    async def test_gather_mines_picks_up_and_uses_total_inventory_count(self):
        self.node.inventory["oak_log"] = 2
        result = await self.finish({"count": 4})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["collected"], 2)
        self.assertEqual(result["data"]["current_count"], 4)
        self.assertEqual(self.names().count("mine_block"), 2)
        self.assertEqual(self.names().count("collect_item"), 2)
        self.assertTrue(result["verification"]["verified"])

    async def test_already_enough_never_moves_or_mines(self):
        self.node.inventory["oak_log"] = 2
        result = await self.finish()
        self.assertTrue(result["success"], result)
        self.assertEqual(self.names(), ["resolve_resource"])
        self.assertEqual(result["data"]["blocks_mined"], 0)

    async def test_automatic_pickup_avoids_redundant_collect(self):
        self.node.auto_pickup = True
        result = await self.finish()
        self.assertTrue(result["success"], result)
        self.assertNotIn("collect_item", self.names())

    async def test_different_block_drop_names_use_node_sources(self):
        self.node.item_name = "raw_iron"
        self.node.block_name = "iron_ore"
        for block in self.node.blocks:
            block["name"] = "iron_ore"
        result = await self.finish({"block_name": "iron_ore", "tool_name": "iron_pickaxe"})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["current_count"], 2)
        self.assertEqual(self.node.inventory.get("iron_ore", 0), 0)
        self.assertEqual(next(a["tool_name"] for n, a in self.node.calls if n == "mine_block"), "iron_pickaxe")

    async def test_unsafe_support_skipped_and_other_resource_selected(self):
        self.node.blocks.insert(0, {"name": "oak_log", "position": {"x": 0, "y": 63, "z": 0}})
        result = await self.finish()
        self.assertTrue(result["success"], result)
        mined = [a["position"] for n, a in self.node.calls if n == "mine_block"]
        self.assertNotIn({"x": 0, "y": 63, "z": 0}, mined)
        self.assertEqual(result["data"]["attempts"][0]["error"], "UNSAFE_TARGET")

    async def test_distant_resource_uses_standing_navigation_and_rechecks(self):
        self.node.blocks = [{"name": "oak_log", "position": {"x": 10, "y": 64, "z": 0}}]
        result = await self.finish({"count": 1})
        self.assertTrue(result["success"], result)
        self.assertIn("find_interaction_positions", self.names())
        self.assertIn("get_movement_task", self.names())
        self.assertEqual(self.names().count("goto_position"), 1)

    async def test_search_center_moves_after_confirmed_collection(self):
        self.node.blocks = [
            {"name": "oak_log", "position": {"x": 10, "y": 64, "z": 0}},
            {"name": "oak_log", "position": {"x": 20, "y": 64, "z": 0}},
        ]
        result = await self.finish({"count": 2})
        self.assertTrue(result["success"], result)
        mined = [a["position"] for n, a in self.node.calls if n == "mine_block"]
        self.assertEqual(mined, [{"x": 10, "y": 64, "z": 0}, {"x": 20, "y": 64, "z": 0}])
        self.assertNotEqual(result["data"]["origin"], result["data"]["search_center"])

    async def test_source_mismatch_ambiguity_unknown_data_stop_before_mining(self):
        for i, (flag, code) in enumerate((("source_mismatch", "MINING_SOURCE_MISMATCH"), ("ambiguous", "RESOURCE_AMBIGUOUS"))):
            setattr(self.node, flag, True)
            result = await self.finish(request_id=f"source{i}")
            self.assertEqual(result["error"]["code"], code)
            setattr(self.node, flag, False)
        self.node.source_available = False
        result = await self.finish(request_id="unavailable")
        self.assertEqual(result["error"]["code"], "MINING_SOURCE_UNKNOWN")
        self.assertNotIn("mine_block", self.names())

    async def test_partial_resource_shortage_preserves_real_count(self):
        self.node.blocks = self.node.blocks[:1]
        result = await self.finish()
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "RESOURCE_INSUFFICIENT")
        self.assertEqual(result["data"]["current_count"], 1)
        self.assertEqual(result["data"]["remaining"], 1)
        self.assertEqual(self.names().count("mine_block"), 1)

    async def test_no_resources_and_vertical_range_are_not_empty_success(self):
        for block in self.node.blocks:
            block["position"]["y"] = 90
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(result["data"]["blocks_mined"], 0)

    async def test_delayed_dropped_entity_can_be_picked_up(self):
        self.node.missing_count = 2
        result = await self.finish({"count": 1})
        self.assertTrue(result["success"], result)
        self.assertEqual(self.names().count("collect_item"), 3)
        self.assertEqual(self.names().count("mine_block"), 1)

    async def test_missing_drop_or_fake_collect_never_causes_more_destruction(self):
        for i, flag in enumerate(("no_pickup", "fake_collect")):
            setattr(self.node, flag, True)
            before = self.names().count("mine_block")
            result = await self.finish(request_id=f"pickup{i}")
            self.assertEqual(result["status"], "uncertain")
            self.assertEqual(result["error"]["code"], "PICKUP_NOT_CONFIRMED")
            self.assertEqual(self.names().count("mine_block") - before, 1)
            setattr(self.node, flag, False)

    async def test_full_inventory_and_missing_tool_report_real_failures(self):
        self.node.full = True
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "INVENTORY_FULL")
        self.assertEqual(self.names().count("mine_block"), 1)
        self.node.full = False
        self.node.mine_error = "TOOL_MISSING"
        result = await self.finish(request_id="no-tool")
        self.assertEqual(result["error"]["code"], "TOOL_MISSING")
        self.assertFalse(result["success"])

    async def test_uncertain_mining_and_unconfirmed_removal_do_not_collect(self):
        for i, flag in enumerate(("uncertain_mining", "fake_removal")):
            setattr(self.node, flag, True)
            result = await self.finish(request_id=f"uncertain{i}")
            self.assertEqual(result["status"], "uncertain")
            self.assertNotIn("collect_item", self.names())
            setattr(self.node, flag, False)

    async def test_navigation_or_changed_blocks_fail_without_mining(self):
        self.node.blocks = self.node.blocks[:1]
        self.node.blocks[0]["position"]["x"] = 10
        self.node.fail_navigation = True
        result = await self.finish({"count": 1})
        self.assertFalse(result["success"])
        self.assertLessEqual(self.names().count("goto_position"), 3)
        self.node.fail_navigation = False
        self.node.changed = True
        result = await self.finish({"count": 1}, request_id="changed")
        self.assertFalse(result["success"])
        self.assertNotIn("mine_block", self.names())

    async def test_line_of_sight_failure_clears_path_and_retries_target(self):
        self.node.blocked = True
        result = await self.finish({"count": 1})
        self.assertTrue(result["success"], result)
        self.assertIn("clear_path", self.names())
        self.assertEqual(self.names().count("mine_block"), 1)
        self.assertEqual(result["data"]["cleared_blocks"], 1)

    async def test_pickup_path_is_cleared_before_collecting_visible_drop(self):
        self.node.item_entity_visible = True
        self.node.pickup_blocked = True
        result = await self.finish({"count": 1})
        self.assertTrue(result["success"], result)
        names = self.names()
        self.assertLess(names.index("clear_path"), names.index("collect_item"))
        self.assertEqual(result["data"]["cleared_blocks"], 1)

    async def test_attempt_budget_and_call_budget_are_bounded(self):
        result = await self.finish({"count": 4, "max_blocks": 1})
        self.assertEqual(result["error"]["code"], "RESOURCE_INSUFFICIENT")
        self.assertEqual(result["data"]["blocks_mined"], 1)
        result = await self.finish(request_id="budget", max_tool_calls=1)
        self.assertEqual(result["error"]["code"], "CALL_LIMIT")

    async def test_transport_loss_recovers_receipt_and_request_is_deduplicated(self):
        self.node.transport_lost = True
        result = await self.finish({"count": 1})
        self.assertTrue(result["success"], result)
        again = await self.manager.submit(self.request({"count": 1}))
        self.assertEqual(again["execution_id"], result["execution_id"])
        self.assertEqual(self.names().count("mine_block"), 1)

    async def test_timeout_and_cancel_during_navigation_release_control(self):
        self.node.blocks[0]["position"]["x"] = 10
        self.node.blocks = self.node.blocks[:1]
        self.node.pending_forever = True
        result = await self.finish(timeout_seconds=0.03)
        self.assertEqual(result["error"]["code"], "TASK_TIMEOUT")
        previous_calls = len(self.node.calls)
        record = await self.manager.submit(self.request(request_id="cancel"))
        async with asyncio.timeout(2):
            while len(self.node.calls) <= previous_calls or self.names()[-1] != "get_movement_task":
                await asyncio.sleep(0.001)
        result = await self.manager.cancel(record["execution_id"], "Steve")
        self.assertEqual(result["status"], "cancelled")
        self.assertNotIn("mine_block", self.names())
        self.assertFalse(self.node.leased)

    async def test_disconnect_and_missing_inventory_stop_before_mining(self):
        self.node.offline = True
        result = await self.finish()
        self.assertFalse(result["success"])
        self.node.offline = False
        original = self.node.world

        def unavailable():
            world = original()
            world["availability"]["inventory"] = "unavailable"
            return world

        self.node.world = unavailable
        result = await self.finish(request_id="inventory")
        self.assertEqual(result["error"]["code"], "INVENTORY_UNAVAILABLE")
        self.assertNotIn("mine_block", self.names())

    async def test_final_verifier_reads_actual_inventory_not_claimed_tool_results(self):
        result = await self.finish()
        after = self.node.world()
        after["inventory"] = []
        checks = GatherResource().verify(GatherParameters(resource="oak_log", count=2), result["data"], self.node.world(), after).checks
        self.assertFalse(next(c.passed for c in checks if c.criterion == "inventory_target_reached"))

    async def test_http_submit_poll_and_player_isolation(self):
        with patch.object(main, "skill_manager", self.manager):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://agent") as client:
                response = await client.post("/api/agent/skills/execute", json=self.request().model_dump())
                self.assertEqual(response.status_code, 202)
                execution_id = response.json()["data"]["execution_id"]
                await self.executor._work[execution_id]
                path = f"/api/agent/skills/executions/{execution_id}"
                self.assertTrue((await client.get(path, params={"player": "Steve"})).json()["data"]["success"])
                self.assertEqual((await client.get(path, params={"player": "Alex"})).status_code, 404)

    async def test_real_node_http_mining_pickup_and_inventory_verification(self):
        # 原 Node 工具/注册表/租约与真实 HTTP，受控游戏数据；不连接真实 Minecraft。
        script = r"""
const {EventEmitter}=require('node:events');
const {Vec3}=require('vec3');
const {createToolSystem}=require('./src/tools');
const {createApiServer}=require('./src/httpServer');
const bot=new EventEmitter(), blocks=new Map(), inventory=[];
for(const x of [6,8]) {const p=new Vec3(x,64,0);blocks.set(p.toString(),{name:'oak_log',position:p,hardness:1,canHarvest:()=>true});}
bot.entity={id:1,position:new Vec3(0,64,0)};bot.entities={};bot.isAlive=true;
bot.game={dimension:'overworld',minY:-64,height:384};bot.registry=require('minecraft-data')('1.21.4');
bot.inventory={items:()=>inventory,emptySlotCount:()=>30};
bot.findBlocks=({matching,maxDistance,count})=>[...blocks.values()].filter(b=>bot.registry.blocksByName[b.name].id===matching&&b.position.distanceTo(bot.entity.position)<=maxDistance).map(b=>b.position).slice(0,count);
bot.blockAt=p=>blocks.get(p.floored().toString())??{name:p.y<64?'stone':'air',position:p.floored(),
 boundingBox:p.y<64?'block':'empty',shapes:p.y<64?[[0,0,0,1,1,1]]:[]};
bot.world={raycast:()=>({position:[...blocks.values()][0].position})};
bot.canDigBlock=()=>true;bot.canSeeBlock=()=>true;bot.stopDigging=()=>{};bot.clearControlStates=()=>{};
let dropId=100;
bot.dig=async b=>{blocks.delete(b.position.toString());const id=dropId++;bot.entities[id]={id,isValid:true,position:b.position,getDroppedItem:()=>({name:'oak_log'})};};
bot.pathfinder={goal:null,setGoal(goal){this.goal=goal;if(goal){bot.entity.position=new Vec3(goal.x,goal.y,goal.z);
 for(const e of Object.values(bot.entities)){if(e.isValid){inventory.push({name:'oak_log',count:1,type:bot.registry.itemsByName.oak_log.id});e.isValid=false;delete bot.entities[e.id];}}}}};
let task,taskId=0;
const movement={getState:()=>({type:'idle'}),stop:()=>({ok:true}),
 goTo:(who,x,y,z)=>{bot.entity.position=new Vec3(x,y,z);task={taskId:++taskId,status:'arrived'};return {ok:true,taskId};},getTask:()=>task};
const perception={getNearbyEntities:()=>({available:true,truncated:false,items:Object.values(bot.entities).filter(e=>e.isValid)
 .map(e=>({id:e.id,itemName:e.getDroppedItem?.()?.name,itemCount:e.getDroppedItem?.()?.count??1,
 position:{x:e.position.x,y:e.position.y,z:e.position.z}}))}),
getWorldSnapshot:()=>({availability:{player:'available',inventory:'available'},
 player:{position:{x:bot.entity.position.x,y:bot.entity.position.y,z:bot.entity.position.z},alive:true,dimension:'overworld'},
 inventory:inventory.map((item,i)=>({...item,slot:i+9})),nearbyEntities:[],nearbyBlocks:[],timestamp:Date.now()})};
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
            # 本地 HTTP 集成验证终态而非真实时间；短轮询避免默认 2 秒空等，
            # 外层留足 Windows HTTP/SSL 初始化耗时，产品超时另有专项断言。
            executor = TaskManager(ToolClient(Settings(f"http://127.0.0.1:{port}", 2, "127.0.0.1", 8000)), poll_interval=0.001)
            manager = SkillManager(create_skill_registry(), executor)
            record = await manager.submit(self.request())
            await asyncio.wait_for(executor._work[record["execution_id"]], 30)
            result = manager.view(record["execution_id"], "Steve")
            self.assertTrue(result["success"], result)
            self.assertEqual(result["data"]["current_count"], 2)
            names = [step["tool"] for step in result["steps"]]
            self.assertEqual(names.count("mine_block"), 2)
            self.assertEqual(names.count("collect_item"), 2)
            self.assertIn("find_interaction_positions", names)
            self.assertIn("get_movement_task", names)
            self.assertTrue(result["verification"]["verified"])
        finally:
            if executor:
                await executor.close()
            process.terminate()
            await process.communicate()


if __name__ == "__main__":
    unittest.main()
