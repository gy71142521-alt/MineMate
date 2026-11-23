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
from skills.craft_item import CraftItem, CraftParameters
from skills.explore_and_find import distance
from test_execution import MockNode


class CraftingNode(MockNode):
    def __init__(self, catalog):
        super().__init__(catalog)
        self.location = {"x": 0, "y": 64, "z": 0}
        self.inventory = {"oak_planks": 8}
        self.item, self.ingredient, self.required, self.batch = "crafting_table", "oak_planks", 4, 1
        self.requires_table = False
        self.tables = [{"name": "crafting_table", "position": {"x": 10, "y": 64, "z": 0}}]
        self.ambiguous = self.unknown = self.no_recipe = self.bad_recipe = False
        self.no_stands = self.bad_navigation = self.invisible = self.changed = False
        self.consume_after_move = self.dimension_change = self.invalid_inventory = False
        self.negative_delta = False
        self.table_alternative = False
        self.near_navigation = False
        self.recipe_queries = 0
        self.dimension = "overworld"

    def world(self):
        world = super().world()
        world["player"].update(position=copy.deepcopy(self.location), alive=True, dimension=self.dimension)
        if self.invalid_inventory:
            world["availability"]["inventory"] = "unavailable"
        return world

    def handle(self, request):
        inventory_before = copy.deepcopy(self.inventory)
        response = super().handle(request)
        if request.url.path != "/api/tools/execute" or not response.json()["data"]["success"]:
            return response
        payload = json.loads(request.content)
        name, args = payload["tool"], payload["arguments"]
        result = response.json()["data"]
        data = result["data"]
        if name == "resolve_resource":
            data.update(ambiguous=self.ambiguous, candidates=[] if self.unknown else [{"kind": "item", "name": self.item}])
        elif name == "inspect_recipe":
            self.recipe_queries += 1
            operations = (args["count"] + self.batch - 1) // self.batch
            required = operations * self.required
            available = self.inventory.get(self.ingredient, 0)
            recipe = {"requiresTable": self.requires_table, "operations": operations,
                      "output": {"name": self.item, "count": operations * self.batch},
                      "ingredients": [{"name": self.ingredient, "type": 2, "metadata": None, "count": required,
                                       "available": available, "missing": max(0, required - available)}],
                      "delta": [{"name": self.ingredient, "count": -required},
                                {"name": self.item, "count": -1 if self.negative_delta else operations * self.batch}]}
            data.update(recipes=[] if self.no_recipe else [recipe], truncated=False)
            if self.table_alternative:
                data["recipes"].insert(0, {**copy.deepcopy(recipe), "requiresTable": True})
            if self.bad_recipe:
                del recipe["delta"]
        elif name == "find_block":
            data.update(blocks=copy.deepcopy(self.tables), truncated=False)
        elif name == "inspect_block":
            table = any(t["position"] == args["position"] for t in self.tables)
            data.update(name="crafting_table" if table and not self.changed else "air",
                        visible=not self.invisible)
        elif name == "find_interaction_positions":
            p = args["position"]
            data["candidates"] = [] if self.no_stands else [{"position": {**p, "x": p["x"] - 2}}]
        elif name == "goto_position":
            if self.bad_navigation:
                result.update(success=False, data=None, error={"code": "NAVIGATION_FAILED", "message": "controlled failure"})
            elif not self.pending_forever:
                self.location = {k: args[k] for k in ("x", "y", "z")}
                if self.near_navigation:
                    # GoalNear 默认在离站位 2 格处就可到达；不是传送到精确坐标。
                    self.location["x"] -= args.get("arrival_range", 2)
                if self.consume_after_move:
                    self.inventory[self.ingredient] = 0
                if self.dimension_change:
                    self.dimension = "the_nether"
        elif name == "craft_item":
            self.inventory = inventory_before
            output = ((args["count"] + self.batch - 1) // self.batch) * self.batch
            if not self.lying:
                self.inventory[self.item] = self.inventory.get(self.item, 0) + output
                self.inventory[self.ingredient] -= output // self.batch * self.required
            data.update(item=self.item, expected=output, actualIncrease=0 if self.lying else output)
        self.receipts[payload["requestId"]]["result"] = copy.deepcopy(result)
        return httpx.Response(200, json={"success": True, "data": result, "error": None})


class CraftTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[2] / "minecraft-bot"
        command = "const {EventEmitter}=require('node:events');const {createToolSystem}=require('./src/tools');const r=createToolSystem(new EventEmitter());console.log(JSON.stringify(r.list()));r.dispose();"
        cls.catalog = json.loads(subprocess.check_output(["node", "-e", command], cwd=cls.root, text=True, encoding="utf-8"))

    async def asyncSetUp(self):
        self.node = CraftingNode(self.catalog)
        client = ToolClient(Settings("http://node", 1, "127.0.0.1", 8000), httpx.MockTransport(self.node.handle))
        self.executor = TaskManager(client, poll_interval=0.001, heartbeat_interval=0.01)
        self.registry = create_skill_registry()
        self.manager = SkillManager(self.registry, self.executor)

    async def asyncTearDown(self):
        await self.executor.close()

    def request(self, arguments=None, **extra):
        return SkillRequest.model_validate({"player": "Steve", "request_id": "craft1", "skill": "craft_item",
                                            "arguments": {"item_name": self.node.item, **(arguments or {})}, **extra})

    async def finish(self, arguments=None, **extra):
        record = await self.manager.submit(self.request(arguments, **extra))
        await asyncio.wait_for(asyncio.shield(self.executor._work[record["execution_id"]]), 4)
        return self.manager.view(record["execution_id"], "Steve")

    def names(self):
        return [n for n, _ in self.node.calls]

    async def test_registration_parameters_and_nested_position(self):
        self.assertEqual([s["name"] for s in self.registry.list()], ["explore_and_find", "gather_resource", "craft_item", "smelt_item"])
        for args in ({"count": 0}, {"count": 17}, {"count": True}, {"count": "1"}, {"search_radius": 17},
                     {"item_name": "\n"}, {"table_position": {"x": 1, "y": 64, "z": 0, "shell": "run"}},
                     {"table_position": {"x": True, "y": 64, "z": 0}},
                     {"table_position": {"x": 1, "y": 2048, "z": 0}}, {"tool": "shell"}):
            with self.subTest(args=args), self.assertRaises(LLMError):
                await self.manager.submit(self.request(args))
        self.assertEqual(self.node.calls, [])

    async def test_inventory_recipe_never_searches_or_moves(self):
        result = await self.finish()
        self.assertTrue(result["success"], result)
        self.assertEqual(self.names(), ["resolve_resource", "inspect_recipe", "inspect_recipe", "craft_item"])
        self.assertEqual(result["data"]["actual_increase"], 1)
        self.assertTrue(result["verification"]["verified"])
        self.assertFalse(self.node.leased)

    async def test_existing_product_does_not_replace_requested_increase(self):
        self.node.inventory["crafting_table"] = 3
        result = await self.finish({"count": 2})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["current_count"], 5)
        self.assertEqual(result["data"]["actual_increase"], 2)

    async def test_backpack_recipe_preferred_over_workbench_alternative(self):
        self.node.table_alternative = True
        result = await self.finish()
        self.assertTrue(result["success"], result)
        self.assertFalse(result["data"]["recipe"]["requiresTable"])
        self.assertNotIn("find_block", self.names())

    async def test_upper_count_boundary_uses_same_node_schema(self):
        self.node.inventory["oak_planks"] = 64
        result = await self.finish({"count": 16})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["actual_increase"], 16)

    async def test_batch_rounding_reports_real_output(self):
        self.node.item, self.node.ingredient, self.node.required, self.node.batch = "oak_planks", "oak_log", 1, 4
        self.node.inventory = {"oak_log": 2}
        result = await self.finish({"count": 5})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["actual_increase"], 8)
        self.assertEqual(result["data"]["recipe"]["operations"], 2)

    async def test_missing_materials_report_shortage_before_movement(self):
        self.node.inventory["oak_planks"] = 2
        self.node.requires_table = True
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "MATERIALS_MISSING")
        self.assertEqual(result["data"]["material_shortages"][0]["ingredients"][0]["missing"], 2)
        self.assertEqual(self.names(), ["resolve_resource", "inspect_recipe"])

    async def test_no_recipe_and_invalid_recipe_do_not_craft(self):
        for flag, code in (("no_recipe", "NO_RECIPE"), ("bad_recipe", "INVALID_RECIPE_RESULT"),
                           ("negative_delta", "UNSUPPORTED_RECIPE")):
            setattr(self.node, flag, True)
            result = await self.finish(request_id=flag)
            self.assertEqual(result["error"]["code"], code)
            setattr(self.node, flag, False)
        self.assertNotIn("craft_item", self.names())

    async def test_required_table_is_found_approached_and_rechecked(self):
        self.node.requires_table = True
        result = await self.finish()
        self.assertTrue(result["success"], result)
        self.assertIn("find_block", self.names())
        self.assertIn("get_movement_task", self.names())
        args = next(a for n, a in self.node.calls if n == "craft_item")
        self.assertEqual(args["position"], {"x": 10, "y": 64, "z": 0})
        self.assertEqual(self.node.recipe_queries, 2)

    async def test_workbench_approach_requests_exact_stand_not_default_near_arrival(self):
        self.node.requires_table = self.node.near_navigation = True
        result = await self.finish()
        self.assertTrue(result["success"], result)
        args = next(a for n, a in self.node.calls if n == "goto_position")
        self.assertEqual(args["arrival_range"], 0)
        self.assertEqual(result["data"]["workbench_check"]["reason"], "READY")

    async def test_explicit_table_skips_search_and_respects_range(self):
        self.node.requires_table = True
        self.node.tables[0]["position"]["x"] = 2
        result = await self.finish({"table_position": {"x": 2, "y": 64, "z": 0}})
        self.assertTrue(result["success"], result)
        self.assertNotIn("find_block", self.names())
        self.assertNotIn("goto_position", self.names())
        result = await self.finish({"table_position": {"x": 20, "y": 64, "z": 0}}, request_id="far")
        self.assertEqual(result["error"]["code"], "WORKBENCH_NOT_FOUND")

    async def test_no_table_or_only_vertical_outside_range(self):
        self.node.requires_table = True
        self.node.tables[0]["position"]["y"] = 90
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "WORKBENCH_NOT_FOUND")
        self.assertNotIn("goto_position", self.names())

    async def test_unreachable_invisible_and_changed_table_never_craft(self):
        self.node.requires_table = True
        for flag in ("no_stands", "bad_navigation", "invisible", "changed"):
            setattr(self.node, flag, True)
            result = await self.finish(request_id=flag)
            self.assertEqual(result["error"]["code"], "WORKBENCH_UNREACHABLE_OR_CHANGED")
            if flag == "invisible":
                self.assertEqual(result["data"]["workbench_check"]["reason"], "NO_LINE_OF_SIGHT")
            elif flag == "changed":
                self.assertEqual(result["data"]["workbench_check"]["reason"], "BLOCK_CHANGED")
            setattr(self.node, flag, False)
        self.assertNotIn("craft_item", self.names())

    async def test_materials_rechecked_after_navigation(self):
        self.node.requires_table = self.node.consume_after_move = True
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "MATERIALS_MISSING")
        self.assertNotIn("craft_item", self.names())

    async def test_unknown_or_ambiguous_resource_requests_clarification(self):
        for flag, code in (("unknown", "RESOURCE_UNKNOWN"), ("ambiguous", "RESOURCE_AMBIGUOUS")):
            setattr(self.node, flag, True)
            result = await self.finish(request_id=flag)
            self.assertEqual(result["error"]["code"], code)
            setattr(self.node, flag, False)
        self.assertNotIn("craft_item", self.names())

    async def test_fake_success_cannot_pass_inventory_verifier(self):
        self.node.lying = True
        result = await self.finish()
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "GOAL_NOT_VERIFIED")
        self.assertEqual(result["data"]["actual_increase"], 0)

    async def test_tool_failure_and_uncertainty_not_retried(self):
        self.node.fail_tool = "craft_item"
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "BLOCK_CHANGED")
        self.node.fail_tool = None
        self.node.uncertain = True
        result = await self.finish(request_id="uncertain")
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(self.names().count("craft_item"), 2)

    async def test_receipt_recovery_and_duplicate_request_do_not_recraft(self):
        self.node.transport_timeout = True
        result = await self.finish()
        self.assertTrue(result["success"], result)
        again = await self.manager.submit(self.request())
        self.assertEqual(again["execution_id"], result["execution_id"])
        self.assertEqual(self.names().count("craft_item"), 1)

    async def test_disconnect_inventory_unavailable_and_dimension_change(self):
        self.node.offline = True
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "BOT_NOT_READY")
        self.node.offline = False
        self.node.invalid_inventory = True
        result = await self.finish(request_id="inventory")
        self.assertEqual(result["error"]["code"], "INVENTORY_UNAVAILABLE")
        self.node.invalid_inventory = False
        self.node.requires_table = self.node.dimension_change = True
        result = await self.finish(request_id="dimension")
        self.assertEqual(result["error"]["code"], "DIMENSION_CHANGED")
        self.assertNotIn("craft_item", self.names())

    async def test_timeout_and_cancel_during_navigation(self):
        self.node.requires_table = self.node.pending_forever = True
        result = await self.finish(timeout_seconds=0.03)
        self.assertEqual(result["error"]["code"], "TASK_TIMEOUT")
        old_calls = len(self.node.calls)
        record = await self.manager.submit(self.request(request_id="cancel"))
        async with asyncio.timeout(2):
            while len(self.node.calls) <= old_calls or self.names()[-1] != "get_movement_task":
                await asyncio.sleep(0.001)
        result = await self.manager.cancel(record["execution_id"], "Steve")
        self.assertEqual(result["status"], "cancelled")
        self.assertNotIn("craft_item", self.names())
        self.assertFalse(self.node.leased)

    async def test_call_budget_stops_before_crafting(self):
        result = await self.finish(max_tool_calls=1)
        self.assertEqual(result["error"]["code"], "CALL_LIMIT")
        self.assertNotIn("craft_item", self.names())

    async def test_independent_verifier_uses_actual_snapshots(self):
        result = await self.finish()
        after = self.node.world()
        after["inventory"] = []
        checks = CraftItem().verify(CraftParameters(item_name="crafting_table"), result["data"], self.node.world(), after).checks
        self.assertFalse(next(c.passed for c in checks if c.criterion == "inventory_increase_reached"))

    async def test_http_lifecycle_and_player_isolation(self):
        with patch.object(main, "skill_manager", self.manager):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://agent") as client:
                listing = (await client.get("/api/agent/skills")).json()
                self.assertIn("craft_item", [s["name"] for s in listing["data"]["skills"]])
                response = await client.post("/api/agent/skills/execute", json=self.request().model_dump())
                self.assertEqual(response.status_code, 202)
                execution_id = response.json()["data"]["execution_id"]
                await self.executor._work[execution_id]
                path = f"/api/agent/skills/executions/{execution_id}"
                self.assertTrue((await client.get(path, params={"player": "Steve"})).json()["data"]["success"])
                self.assertEqual((await client.get(path, params={"player": "Alex"})).status_code, 404)

    async def test_real_node_http_uses_real_recipes_and_registered_crafting_tool(self):
        # 实际 Node HTTP/租约/工具/版本配方，受控背包和工作台，不是真实 Minecraft 验收。
        script = r"""
const {EventEmitter}=require('node:events');const {Vec3}=require('vec3');
const {createToolSystem}=require('./src/tools');const {createApiServer}=require('./src/httpServer');
const bot=new EventEmitter();bot.registry=require('minecraft-data')('1.21.4');
bot.game={dimension:'overworld',minY:-64,height:384};bot.isAlive=true;
bot.entity={id:1,position:new Vec3(0,64,0)};bot.entities={};const inventory=[];
const add=(name,count)=>{const type=bot.registry.itemsByName[name].id;const old=inventory.find(i=>i.type===type);
 if(old)old.count+=count;else inventory.push({name,type,count,metadata:0,slot:9+inventory.length});};
add('oak_log',2);add('oak_planks',4);add('iron_ingot',5);
bot.inventory={items:()=>inventory.filter(i=>i.count>0),count:(id,meta)=>inventory.filter(i=>i.type===id&&(meta==null||i.metadata===meta)).reduce((s,i)=>s+i.count,0)};
require('mineflayer/lib/plugins/craft')(bot);
let crafts=0;bot.craft=async(recipe,n,table)=>{if(recipe.requiresTable&&!table)throw Error('missing table');crafts++;
 for(const d of recipe.delta)add(bot.registry.items[d.id].name,d.count*n);};
const table=new Vec3(10,64,0);
bot.blockAt=p=>({name:p.floored().equals(table)?'crafting_table':p.y<64?'stone':'air',position:p.floored(),
 boundingBox:p.y<64?'block':'empty',shapes:p.y<64?[[0,0,0,1,1,1]]:[]});
bot.findBlocks=()=>[table];bot.canDigBlock=()=>true;bot.canSeeBlock=()=>true;
bot.world={raycast:()=>({position:table})};let task,taskId=0;
const movement={getState:()=>({type:'idle'}),stop:()=>({ok:true}),
 goTo:(who,x,y,z,options={})=>{bot.entity.position=new Vec3(x-(options.arrivalRange??2),y,z);task={taskId:++taskId,status:'arrived'};return {ok:true,taskId};},getTask:()=>task};
const perception={getWorldSnapshot:()=>({availability:{player:'available',inventory:'available'},
 player:{position:{x:bot.entity.position.x,y:bot.entity.position.y,z:bot.entity.position.z},alive:true,dimension:'overworld'},
 inventory:inventory.filter(i=>i.count>0),nearbyEntities:[],nearbyBlocks:[],timestamp:Date.now()})};
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
            executor = TaskManager(ToolClient(Settings(f"http://127.0.0.1:{port}", 2, "127.0.0.1", 8000)), poll_interval=0.001)
            manager = SkillManager(self.registry, executor)
            for name, count, expected in (("oak_planks", 5, 8), ("crafting_table", 1, 1), ("iron_helmet", 1, 1)):
                request = SkillRequest(player="Steve", request_id=name, skill="craft_item", arguments={"item_name": name, "count": count})
                record = await manager.submit(request)
                await asyncio.wait_for(executor._work[record["execution_id"]], 10)
                result = manager.view(record["execution_id"], "Steve")
                self.assertTrue(result["success"], result)
                self.assertEqual(result["data"]["actual_increase"], expected)
                names = [s["tool"] for s in result["steps"]]
                self.assertEqual(names.count("craft_item"), 1)
                if name == "iron_helmet":
                    self.assertIn("goto_position", names)
                    self.assertIn("get_movement_task", names)
                else:
                    self.assertNotIn("goto_position", names)
        finally:
            if executor:
                await executor.close()
            if process.returncode is None:
                process.kill()
            await process.wait()
