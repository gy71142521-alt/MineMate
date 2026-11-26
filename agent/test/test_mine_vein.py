import asyncio
import copy
import json
from pathlib import Path
import subprocess
import unittest

import httpx

from app import main
from app.config import Settings
from app.execution.manager import TaskManager
from app.llm_client import LLMError
from app.tool_client import ToolClient
from skills import SkillManager, SkillRequest
from skills.catalog import create_skill_registry
from skills.mine_vein import MineVein, MineVeinParameters
from test_gather_resource import GatheringNode


class VeinNode(GatheringNode):
    def __init__(self, catalog):
        super().__init__(catalog)
        self.item_name, self.block_name = "raw_iron", "iron_ore"
        self.blocks = [{"name": "iron_ore", "position": {"x": x, "y": 64, "z": 0}} for x in [2, 3, 4, 10]]
        self.route_blocked = False
        self.ore_clear = self.drop_clear = 0
        self.route_move = False
        self.route_unreachable = False
        self.ore_uncertain = self.disappear_drop = self.merge_drop = False
        self.exposed = True
        self.drop_id = 900

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
        if name == "find_block":
            for block in data["blocks"]:
                block["visible"] = True
        elif name == "inspect_vein":
            target = args["position"]
            data.update(blocks=[{**copy.deepcopy(b), "visible": True} for b in self.blocks if b["name"] == args["block_name"]
                               and sum(abs(b["position"][a] - target[a]) for a in target) <= 1] if self.exposed else [])
        elif name == "inspect_mining_route":
            is_drop = args.get("purpose") == "pickup"
            remaining = self.drop_clear if is_drop else self.ore_clear
            if self.route_unreachable or remaining > args.get("max_clear_blocks", 0):
                data.update(reachable=False, next=None, reason="NO_SAFE_ROUTE")
            elif remaining:
                data.update(reachable=True, next={"action": "dig", "position": {"x": 1, "y": 65, "z": 0}, "block_name": "stone"})
            elif self.route_move:
                data.update(reachable=True, next={"action": "move", "position": {"x": 1, "y": 64, "z": 0}})
            else:
                data.update(reachable=True, next={"action": "ready"})
        elif name == "mine_route_block":
            if args.get("purpose") == "pickup":
                self.drop_clear -= 1
            else:
                self.ore_clear -= 1
            data.update(blockRemoved=True, clearedBlock={"position": {"x": 1, "y": 65, "z": 0}, "block_name": "stone"})
        elif name == "goto_position":
            self.route_move = False
        elif name == "mine_vein_block":
            if self.mine_error:
                result.update(success=False, data=None, error={"code": self.mine_error, "message": "controlled failure"})
            else:
                self.blocks = [b for b in self.blocks if b["position"] != args["position"]]
                self.pending_drop += 1
                if self.auto_pickup:
                    self.inventory[self.item_name] = self.inventory.get(self.item_name, 0) + self.pending_drop
                    self.pending_drop = 0
                data.update(blockRemoved=True, status="uncertain" if self.ore_uncertain else "completed")
        elif name == "nearby_entities":
            if self.merge_drop:
                self.drop_id += 1
                self.merge_drop = False
            data.update(items=[] if self.disappear_drop or not self.pending_drop else [{
                "id": self.drop_id, "itemName": self.item_name, "valid": True,
                "position": {"x": 2, "y": 64, "z": 0}, "itemCount": self.pending_drop}])
        self.receipts[payload["requestId"]]["result"] = copy.deepcopy(result)
        return httpx.Response(200, json={"success": True, "data": result, "error": None})


class MineVeinTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[2] / "minecraft-bot"
        program = "const {EventEmitter}=require('node:events');const {createToolSystem}=require('./src/tools');const r=createToolSystem(new EventEmitter());console.log(JSON.stringify(r.list()));r.dispose();"
        cls.catalog = json.loads(subprocess.check_output(["node", "-e", program], cwd=root, text=True, encoding="utf-8"))

    async def asyncSetUp(self):
        self.node = VeinNode(self.catalog)
        self.client = ToolClient(Settings("http://node", 1, "127.0.0.1", 8000), httpx.MockTransport(self.node.handle))
        self.executor = TaskManager(self.client, poll_interval=0.001)
        self.registry = create_skill_registry()
        self.manager = SkillManager(self.registry, self.executor)

    async def asyncTearDown(self):
        await self.executor.close()

    def request(self, arguments=None, **extra):
        return SkillRequest.model_validate({"player": "Steve", "request_id": "vein1", "skill": "mine_vein",
            "arguments": {"resource": "raw_iron", "count": 2, **(arguments or {})}, **extra})

    async def finish(self, arguments=None, **extra):
        record = await self.manager.submit(self.request(arguments, **extra))
        return await asyncio.wait_for(self.manager.wait_view(record["execution_id"], "Steve"), 4)

    def names(self):
        return [n for n, _ in self.node.calls]

    async def test_single_vein_only_mines_connected_exposed_ores_and_verifies_inventory(self):
        result = await self.finish({"count": 3})
        self.assertTrue(result["success"], result)
        self.assertEqual([e["position"]["x"] for e in result["data"]["mined"]], [2, 3, 4])
        self.assertTrue(result["verification"]["verified"])
        self.assertEqual(self.node.blocks[0]["position"]["x"], 10)
        self.assertEqual(self.names().count("mine_vein_block"), 3)

    async def test_tunnel_and_pickup_share_clear_budget_and_replan(self):
        self.node.ore_clear, self.node.drop_clear = 2, 1
        result = await self.finish({"count": 1, "max_clear_blocks": 3})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["cleared_blocks"], 3)
        self.assertEqual(result["data"]["clear_budget"], 0)
        self.assertLess(self.names().index("mine_route_block"), self.names().index("mine_vein_block"))
        self.assertLess(self.names().index("nearby_entities"), self.names().index("collect_item"))

    async def test_navigation_waits_original_task_and_exact_standing(self):
        self.node.route_move = True
        result = await self.finish({"count": 1})
        self.assertTrue(result["success"], result)
        args = next(a for n, a in self.node.calls if n == "goto_position")
        self.assertEqual(args["arrival_range"], 0)
        self.assertIn("get_movement_task", self.names())

    async def test_inventory_already_enough_does_not_mine(self):
        self.node.inventory["raw_iron"] = 3
        result = await self.finish()
        self.assertTrue(result["success"], result)
        self.assertEqual(self.names(), ["resolve_resource"])

    async def test_no_exposed_seed_and_vein_exhaustion_are_failures(self):
        self.node.exposed = False
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "ORE_SEED_NOT_FOUND")
        self.node.exposed = True
        result = await self.finish({"count": 4}, request_id="exhausted")
        self.assertEqual(result["error"]["code"], "VEIN_INSUFFICIENT")
        self.assertEqual(result["data"]["current_count"], 3)

    async def test_zero_clear_budget_does_not_dig_or_mine(self):
        self.node.ore_clear = 1
        result = await self.finish({"max_clear_blocks": 0})
        self.assertFalse(result["success"])
        self.assertNotIn("mine_route_block", self.names())
        self.assertNotIn("mine_vein_block", self.names())

    async def test_uncertain_mining_prevents_pickup_or_more_destruction(self):
        self.node.ore_uncertain = True
        result = await self.finish()
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(self.names().count("mine_vein_block"), 1)
        self.assertNotIn("collect_item", self.names())

    async def test_missing_tool_and_unsafe_target_preserve_error(self):
        for i, code in enumerate(["TOOL_MISSING", "UNSAFE_MINING_TARGET"]):
            self.node.mine_error = code
            result = await self.finish(request_id=f"bad{i}")
            self.assertEqual(result["error"]["code"], code)

    async def test_missing_drop_does_not_start_mining_next_ore(self):
        self.node.disappear_drop = True
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "PICKUP_NOT_CONFIRMED")
        self.assertEqual(self.names().count("mine_vein_block"), 1)

    async def test_partial_face_can_use_node_verified_surface(self):
        self.node.blocked = True  # 中心射线不可见，但 route 给出可见表面。
        result = await self.finish({"count": 1})
        self.assertTrue(result["success"], result)

    async def test_final_verifier_rejects_fake_count_or_disconnected_vein(self):
        result = await self.finish()
        after = self.node.world()
        after["inventory"] = []
        checks = MineVein().verify(MineVeinParameters(resource="raw_iron", count=2), result["data"], self.node.world(), after).checks
        self.assertFalse(checks[0].passed)
        result["data"]["mined"][-1]["position"]["x"] = 10
        checks = MineVein().verify(MineVeinParameters(resource="raw_iron", count=2), result["data"], self.node.world(), self.node.world()).checks
        self.assertFalse(checks[-1].passed)

    async def test_cancel_and_timeout_release_lease_during_navigation(self):
        self.node.route_move = self.node.pending_forever = True
        result = await self.finish(timeout_seconds=0.05)
        self.assertEqual(result["error"]["code"], "TASK_TIMEOUT")
        self.assertFalse(self.node.leased)
        self.node.route_move = True
        record = await self.manager.submit(self.request(request_id="cancel"))
        await asyncio.sleep(0.02)
        result = await self.manager.cancel(record["execution_id"], "Steve")
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(self.node.leased)
        self.assertNotIn("mine_vein_block", self.names())

    async def test_strict_parameters_call_budget_disconnect_and_deduplication(self):
        for args in [{"count": True}, {"vein_radius": 9}, {"max_clear_blocks": 33}, {"max_route_steps": 129},
                     {"seed_position": {"x": "1", "y": 64, "z": 0}}]:
            with self.assertRaises(LLMError):
                await self.manager.submit(self.request(args))
        result = await self.finish(max_tool_calls=1)
        self.assertEqual(result["error"]["code"], "CALL_LIMIT")
        self.node.offline = True
        result = await self.finish(request_id="offline")
        self.assertEqual(result["error"]["code"], "BOT_NOT_READY")
        self.node.offline = False
        result = await self.finish(request_id="dedup")
        before = len(self.node.calls)
        again = await self.manager.submit(self.request(request_id="dedup"))
        self.assertEqual(again["execution_id"], result["execution_id"])
        self.assertEqual(len(self.node.calls), before)

    async def test_real_node_http_skill_tunnel_mining_pickup_and_final_verification(self):
        root = Path(__file__).resolve().parents[2] / "minecraft-bot"
        # 由 node -e 在 minecraft-bot cwd 中加载，复用实际依赖与模块。
        script = (Path(__file__).parent / "fixtures" / "vein_http.js").read_text(encoding="utf-8")
        process = await asyncio.create_subprocess_exec("node", "-e", script, cwd=root,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        executor = None
        try:
            port = int(await asyncio.wait_for(process.stdout.readline(), 5))
            client = ToolClient(Settings(f"http://127.0.0.1:{port}", 2, "127.0.0.1", 8000))
            executor = TaskManager(client, poll_interval=0.001)
            manager = SkillManager(create_skill_registry(), executor)
            submitted = await manager.submit(self.request({"seed_position": {"x": 6, "y": 64, "z": 0}}, max_tool_calls=192))
            result = await asyncio.wait_for(manager.wait_view(submitted["execution_id"], "Steve"), 45)
            self.assertTrue(result["success"], result)
            self.assertEqual(result["data"]["current_count"], 2)
            names = [s["tool"] for s in result["steps"]]
            self.assertIn("mine_route_block", names)
            self.assertEqual(names.count("mine_vein_block"), 2)
            self.assertEqual(names.count("collect_item"), 2)
            self.assertTrue(result["verification"]["verified"])
        finally:
            if executor:
                await executor.close()
            process.terminate()
            await process.communicate()
