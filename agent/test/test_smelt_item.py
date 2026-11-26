import asyncio
import copy
import json
import math
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import httpx

from app import main
from app.config import Settings
from app.execution.manager import TaskManager
from app.execution.models import TaskRequest
from app.llm_client import LLMError
from app.tool_client import ToolClient
from skills import SkillManager, SkillRequest
from skills.catalog import create_skill_registry
from skills.smelt_item import SmeltItem, SmeltParameters
from test_execution import MockNode


class SmeltingNode(MockNode):
    def __init__(self, catalog, rules):
        super().__init__(catalog)
        self.rules = rules
        self.location = {"x": 0, "y": 64, "z": 0}
        self.dimension = "overworld"
        self.inventory = {"raw_iron": 8, "coal": 2}
        self.input_name = "raw_iron"
        self.furnaces = [{"position": {"x": 10, "y": 64, "z": 0}, "type": "furnace", "input": None,
                          "fuel": None, "output": None, "fuelSeconds": 0, "fuelRemaining": 0, "progress": 0}]
        self.started = self.ambiguous = self.unknown = self.invisible = self.no_stands = False
        self.block_changed = self.never_progress = self.fake_take = self.full = self.invalid_inventory = False
        self.uncertain_input = self.lost_input_response = self.change_after_input = self.remove_input = False
        self.exhaust_after_input = self.active_only = self.consume_materials_on_move = False
        self.exhaust_always = self.exhaust_on_first_poll = False
        self.stale_burn_report = False
        self.last_product_on_take = False
        self.open_count = 0
        self.waiting = asyncio.Event()

    def world(self):
        world = super().world()
        world["player"].update(position=copy.deepcopy(self.location), alive=True, dimension=self.dimension)
        if self.invalid_inventory:
            world["availability"]["inventory"] = "unavailable"
        return world

    def handle(self, request):
        response = super().handle(request)
        if request.url.path != "/api/tools/execute" or not response.json()["data"]["success"]:
            return response
        payload = json.loads(request.content)
        name, args = payload["tool"], payload["arguments"]
        result, body = response.json()["data"], response.json()
        data = result["data"]
        furnace = next((f for f in self.furnaces if f["position"] == args.get("position")), None)

        def fail(code):
            result.update(success=False, data=None, error={"code": code, "message": "controlled failure"})

        if name == "resolve_resource":
            data.update(ambiguous=self.ambiguous, candidates=[] if self.unknown else [{"kind": "item", "name": args["query"]}])
        elif name == "inspect_smelting_recipe":
            item, count = args["item_name"], args["count"]
            if item not in self.rules["SMELTING"]:
                fail("UNSUPPORTED_RECIPE")
            else:
                available = self.inventory.get(item, 0)
                data.update(input={"name": item, "count": count, "available": available, "missing": max(0, count - available)},
                            output={"name": self.rules["SMELTING"][item], "count": count},
                            fuels=[{"name": fuel, "count": math.ceil(count / capacity), "available": self.inventory.get(fuel, 0),
                                    "missing": max(0, math.ceil(count / capacity) - self.inventory.get(fuel, 0))}
                                   for fuel, capacity in self.rules["FUEL"].items()])
        elif name == "find_block":
            data.update(blocks=[{"name": "furnace", "position": f["position"]} for f in self.furnaces], truncated=False)
        elif name == "inspect_block":
            data.update(name="furnace" if furnace and not self.block_changed else "air", visible=not self.invisible)
        elif name == "find_interaction_positions":
            p = args["position"]
            data["candidates"] = [] if self.no_stands else [{"position": {**p, "x": p["x"] - 2}}]
        elif name == "goto_position" and not self.pending_forever:
            self.location = {k: args[k] for k in ("x", "y", "z")}
            self.location["x"] -= args.get("arrival_range", 2)
            if self.consume_materials_on_move:
                self.inventory[self.input_name] = 0
        elif name == "open_furnace":
            self.open_count += 1
            if self.started:
                self.waiting.set()
                if self.change_after_input:
                    furnace["input"] = {"name": "raw_gold", "count": 1}
                elif self.remove_input:
                    furnace["input"] = None
                elif self.exhaust_on_first_poll:
                    furnace["fuel"] = None
                    furnace["fuelSeconds"] = 0
                    self.exhaust_on_first_poll = False
                elif not self.never_progress and furnace["input"] and (furnace["fuelSeconds"] > 0 or furnace["fuel"]):
                    while furnace["fuelSeconds"] < 10 and furnace["fuel"]:
                        furnace["fuelSeconds"] += self.rules["FUEL"][furnace["fuel"]["name"]] * 10
                        furnace["fuel"]["count"] -= 1
                        if not furnace["fuel"]["count"]:
                            furnace["fuel"] = None
                    if furnace["fuelSeconds"] >= 10:
                        furnace["fuelSeconds"] -= 10
                        product = self.rules["SMELTING"][furnace["input"]["name"]]
                        furnace["input"]["count"] -= 1
                        if not furnace["input"]["count"]:
                            furnace["input"] = None
                        furnace["output"] = {"name": product, "count": 1 + (furnace["output"]["count"] if furnace["output"] else 0)}
                    else:
                        furnace["fuelSeconds"] = 0
            data.update(copy.deepcopy(furnace))
            if self.stale_burn_report:
                data["fuelSeconds"] = 0
        elif name == "furnace_add_input":
            assert args["count"] + (furnace["input"]["count"] if furnace["input"] else 0) <= 64
            self.inventory[args["item_name"]] -= args["count"]
            furnace["input"] = {"name": args["item_name"], "count": args["count"] + (furnace["input"]["count"] if furnace["input"] else 0)}
            self.started = True
            if self.uncertain_input:
                data["status"] = "uncertain"
            data.update(inventoryDelta=-args["count"])
        elif name == "furnace_add_fuel":
            fuel, count = args["fuel_name"], args["count"]
            assert count + (furnace["fuel"]["count"] if furnace["fuel"] else 0) <= 64
            self.inventory[fuel] -= count
            furnace["fuel"] = {"name": fuel, "count": count + (furnace["fuel"]["count"] if furnace["fuel"] else 0)}
            if self.exhaust_after_input:
                furnace["fuel"] = None
                furnace["fuelSeconds"] = 0
                if not self.exhaust_always:
                    self.exhaust_after_input = False
            data.update(inventoryDelta=-count)
        elif name == "furnace_take_output":
            if self.full:
                fail("INVENTORY_FULL")
            elif not self.fake_take:
                self.inventory[args["item_name"]] = self.inventory.get(args["item_name"], 0) + furnace["output"]["count"]
                furnace["output"] = None
                if self.last_product_on_take and furnace["input"]:
                    furnace["input"]["count"] -= 1
                    if not furnace["input"]["count"]:
                        furnace["input"] = None
                    furnace["output"] = {"name": args["item_name"], "count": 1}
                    self.last_product_on_take = False
            data.update(taken=1)
        self.receipts[payload["requestId"]]["result"] = copy.deepcopy(result)
        body["data"] = result
        if name == "furnace_add_input" and self.lost_input_response:
            raise httpx.ReadTimeout("controlled lost response")
        return httpx.Response(200, json=body)


class SmeltTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[2] / "minecraft-bot"
        script = "const {EventEmitter}=require('node:events');const {createToolSystem}=require('./src/tools');const r=createToolSystem(new EventEmitter());console.log(JSON.stringify({catalog:r.list(),rules:require('./src/tools/crafting/data')}));r.dispose();"
        content = json.loads(subprocess.check_output(["node", "-e", script], cwd=cls.root, text=True, encoding="utf-8"))
        cls.catalog, cls.rules = content["catalog"], content["rules"]

    async def asyncSetUp(self):
        self.node = SmeltingNode(self.catalog, self.rules)
        client = ToolClient(Settings("http://node", 1, "127.0.0.1", 8000), httpx.MockTransport(self.node.handle))
        self.executor = TaskManager(client, poll_interval=0.001, heartbeat_interval=0.01)
        self.registry = create_skill_registry()
        self.manager = SkillManager(self.registry, self.executor)

    async def asyncTearDown(self):
        await self.executor.close()

    def request(self, args=None, **extra):
        return SkillRequest.model_validate({"player": "Steve", "request_id": "smelt1", "skill": "smelt_item",
                                            "arguments": {"item_name": self.node.input_name, "count": 3, **(args or {})}, **extra})

    async def finish(self, args=None, **extra):
        record = await self.manager.submit(self.request(args, **extra))
        await asyncio.wait_for(asyncio.shield(self.executor._work[record["execution_id"]]), 5)
        return self.manager.view(record["execution_id"], "Steve")

    def calls(self, name):
        return [a for n, a in self.node.calls if n == name]

    async def test_registry_strict_parameters_and_position(self):
        self.assertIn("smelt_item", [s["name"] for s in self.registry.list()])
        for args in ({"count": 0}, {"count": 65}, {"count": True}, {"count": "3"}, {"search_radius": 17},
                     {"use_existing": "true"}, {"wait_timeout": 0}, {"wait_timeout": 1501},
                     {"furnace_position": {"x": 1, "y": 64, "z": True}}, {"shell": "run"}):
            with self.subTest(args=args), self.assertRaises(LLMError):
                await self.manager.submit(self.request(args))
        self.assertEqual(self.node.calls, [])

    async def test_find_navigate_deposit_and_finish_without_waiting_or_taking(self):
        self.node.never_progress = True
        result = await self.finish()
        self.assertTrue(result["success"], result)
        self.assertEqual(len(self.calls("furnace_add_input")), 1)
        self.assertEqual(len(self.calls("furnace_add_fuel")), 1)
        self.assertEqual(self.calls("furnace_add_fuel")[0]["fuel_name"], "coal")
        self.assertEqual(self.calls("goto_position")[0]["arrival_range"], 0)
        self.assertEqual(self.node.furnaces[0]["input"]["count"], 3)
        self.assertNotIn("iron_ingot", self.node.inventory)
        self.assertEqual(result["data"]["completion_scope"], "furnace_loading")
        self.assertIsNone(result["data"]["smelting_completed"])
        self.assertEqual(result["data"]["stage"], "loaded")
        self.assertEqual(len(self.calls("open_furnace")), 3)
        self.assertEqual(result["data"]["polls"], 0)
        self.assertTrue(result["verification"]["verified"])
        self.assertFalse(self.node.leased)
        self.assertFalse(self.calls("smelt_item"))
        self.assertFalse(self.calls("furnace_take_output"))
        self.assertNotIn("furnace_take_output", SmeltItem.required_tools)

    async def test_existing_products_are_untouched_and_not_claimed_as_new_production(self):
        self.node.inventory["iron_ingot"] = 10
        result = await self.finish()
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["input_submitted_count"], 3)
        self.assertEqual(result["data"]["actual_increase"], 0)
        self.assertEqual(self.node.inventory["iron_ingot"], 10)
        self.assertFalse(self.calls("furnace_take_output"))

    async def test_missing_input_before_navigation(self):
        self.node.inventory["raw_iron"] = 2
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "MATERIALS_MISSING")
        self.assertEqual(result["data"]["material_shortage"], 1)
        self.assertFalse(self.calls("goto_position"))

    async def test_unknown_ambiguous_and_unsupported_recipe_stop_without_writes(self):
        for flag, code in (("unknown", "RESOURCE_UNKNOWN"), ("ambiguous", "RESOURCE_AMBIGUOUS")):
            setattr(self.node, flag, True)
            result = await self.finish(request_id=flag)
            self.assertEqual(result["error"]["code"], code)
            setattr(self.node, flag, False)
        result = await self.finish({"item_name": "diamond"}, request_id="no_recipe")
        self.assertEqual(result["error"]["code"], "UNSUPPORTED_RECIPE")
        self.assertFalse(self.calls("furnace_add_input"))

    async def test_explicit_fuel_and_fallback_use_actual_node_rules(self):
        self.node.inventory = {"raw_iron": 8, "oak_planks": 5}
        result = await self.finish()
        self.assertTrue(result["success"], result)
        self.assertEqual(self.calls("furnace_add_fuel")[0]["count"], 2)
        self.assertEqual(self.calls("furnace_add_fuel")[0]["fuel_name"], "oak_planks")

    async def test_explicit_supported_fuel_is_honoured_even_when_coal_exists(self):
        self.node.inventory["charcoal"] = 1
        result = await self.finish({"fuel_name": "charcoal"})
        self.assertTrue(result["success"], result)
        self.assertEqual(self.calls("furnace_add_fuel")[0]["fuel_name"], "charcoal")
        self.assertEqual(self.node.inventory["coal"], 2)

    async def test_existing_fuel_reused_and_different_explicit_fuel_rejected(self):
        self.node.furnaces[0]["fuel"] = {"name": "charcoal", "count": 1}
        result = await self.finish()
        self.assertTrue(result["success"], result)
        self.assertFalse(self.calls("furnace_add_fuel"))
        result = await self.finish({"fuel_name": "diamond"}, request_id="badfuel")
        self.assertEqual(result["error"]["code"], "UNSUPPORTED_FUEL")

    async def test_explicit_fuel_cannot_replace_existing_different_fuel(self):
        self.node.furnaces[0]["fuel"] = {"name": "charcoal", "count": 1}
        result = await self.finish({"fuel_name": "coal"})
        self.assertEqual(result["error"]["code"], "NO_COMPATIBLE_FURNACE")
        self.assertFalse(self.calls("furnace_add_input"))

    async def test_fuel_transfer_failure_preserves_partial_input_without_retry(self):
        self.node.fail_tool = "furnace_add_fuel"
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "BLOCK_CHANGED")
        self.assertEqual(result["data"]["input_submitted_count"], 3)
        self.assertEqual(len(self.calls("furnace_add_input")), 1)
        self.assertEqual(len(self.calls("furnace_add_fuel")), 1)
        self.assertFalse(self.calls("furnace_take_output"))

    async def test_existing_fire_without_spare_fuel_reports_unknown_remaining_capacity(self):
        self.node.inventory = {"raw_iron": 8}
        self.node.furnaces[0]["fuelSeconds"] = 1
        result = await self.finish()
        self.assertTrue(result["success"], result)
        self.assertTrue(result["data"]["requires_fuel_followup"])
        self.assertEqual(result["data"]["furnaces"][0]["fuel_status"], "remaining_capacity_unknown")
        self.assertFalse(self.calls("furnace_add_fuel"))
        self.assertFalse(self.calls("furnace_take_output"))

    async def test_no_automatic_refill_even_if_fire_is_exhausted_after_loading(self):
        self.node.inventory["coal"] = 10
        self.node.exhaust_after_input = self.node.exhaust_always = True
        result = await self.finish()
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["fuel_refills"], 0)
        self.assertEqual(len(self.calls("furnace_add_fuel")), 1)
        self.assertEqual(len(self.calls("furnace_add_input")), 1)
        self.assertEqual(self.node.furnaces[0]["input"]["count"], 3)

    async def test_stale_zero_burn_counter_does_not_trigger_a_refill(self):
        self.node.stale_burn_report = True
        result = await self.finish()
        self.assertTrue(result["success"], result)
        self.assertEqual(len(self.calls("furnace_add_fuel")), 1)
        self.assertEqual(result["data"]["fuel_refills"], 0)

    async def test_reserve_input_when_same_item_is_also_fuel(self):
        self.node.input_name = "oak_log"
        self.node.inventory = {"oak_log": 3}
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "NO_COMPATIBLE_FURNACE")
        self.assertFalse(self.calls("furnace_add_input"))

    async def test_no_fuel_never_deposits_input(self):
        self.node.inventory = {"raw_iron": 8}
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "NO_COMPATIBLE_FURNACE")
        self.assertEqual(result["data"]["furnace_attempts"][0]["reason"], "FUEL_MISSING_OR_CONFLICT")
        self.assertFalse(self.calls("furnace_add_input"))

    async def test_other_process_skipped_and_next_furnace_used(self):
        self.node.furnaces.append(copy.deepcopy(self.node.furnaces[0]))
        self.node.furnaces[1]["position"]["x"] = 12
        self.node.furnaces[0]["input"] = {"name": "raw_gold", "count": 1}
        result = await self.finish()
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["furnace_position"]["x"], 12)
        self.assertEqual(self.node.furnaces[0]["input"], {"name": "raw_gold", "count": 1})

    async def test_existing_items_require_explicit_opt_in(self):
        self.node.furnaces[0]["output"] = {"name": "iron_ingot", "count": 64}
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "NO_COMPATIBLE_FURNACE")
        self.assertFalse(self.calls("furnace_take_output"))

    async def test_full_output_skipped_without_collecting_existing_products(self):
        self.node.furnaces[0].update(input={"name": "raw_iron", "count": 2}, output={"name": "iron_ingot", "count": 64})
        result = await self.finish({"use_existing": True})
        self.assertEqual(result["error"]["code"], "NO_COMPATIBLE_FURNACE")
        self.assertEqual(result["data"]["furnace_attempts"][0]["reason"], "FURNACE_OUTPUT_FULL")
        self.assertFalse(self.calls("furnace_take_output"))
        self.assertFalse(self.calls("furnace_add_input"))
        self.assertEqual(self.node.furnaces[0]["output"]["count"], 64)

    async def test_full_existing_furnace_is_skipped_without_writes(self):
        self.node.furnaces[0]["input"] = {"name": "raw_iron", "count": 64}
        result = await self.finish({"use_existing": True})
        self.assertEqual(result["error"]["code"], "NO_COMPATIBLE_FURNACE")
        self.assertEqual(result["data"]["furnace_attempts"][0]["reason"], "FURNACE_INPUT_FULL")
        self.assertFalse(self.calls("furnace_add_input"))

    async def test_64_inputs_are_loaded_without_waiting_640_seconds(self):
        self.node.inventory = {"raw_iron": 64, "coal": 8}
        self.node.never_progress = True
        result = await self.finish({"count": 64})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["input_submitted_count"], 64)
        self.assertEqual([a["count"] for a in self.calls("furnace_add_input")], [64])
        self.assertEqual([a["count"] for a in self.calls("furnace_add_fuel")], [8])
        self.assertEqual(self.node.furnaces[0]["input"]["count"], 64)
        self.assertIsNone(self.node.furnaces[0]["output"])
        self.assertLessEqual(result["calls"], 20)
        self.assertFalse(self.node.leased)

    async def test_fill_first_furnace_then_load_remainder_into_second_and_return(self):
        self.node.inventory = {"raw_iron": 64, "coal": 10}
        self.node.never_progress = True
        self.node.furnaces[0]["input"] = {"name": "raw_iron", "count": 10}
        second = copy.deepcopy(self.node.furnaces[0])
        second.update(position={"x": 12, "y": 64, "z": 0}, input=None)
        self.node.furnaces.append(second)
        result = await self.finish({"count": 64, "use_existing": True})
        self.assertTrue(result["success"], result)
        self.assertEqual([a["count"] for a in self.calls("furnace_add_input")], [54, 10])
        self.assertEqual(result["data"]["input_submitted_count"], 64)
        self.assertEqual(result["data"]["remaining_input"], 0)
        self.assertEqual([f["input"]["count"] for f in self.node.furnaces], [64, 10])
        self.assertEqual(len(result["data"]["furnaces"]), 2)
        self.assertTrue(all(f["loaded_furnace"] for f in result["data"]["furnaces"]))
        self.assertFalse(self.calls("furnace_take_output"))

    async def test_insufficient_total_furnace_capacity_keeps_confirmed_partial_deposit(self):
        self.node.furnaces[0]["input"] = {"name": "raw_iron", "count": 62}
        self.node.inventory["coal"] = 8
        result = await self.finish({"use_existing": True})
        self.assertEqual(result["error"]["code"], "INSUFFICIENT_FURNACE_CAPACITY")
        self.assertEqual([a["count"] for a in self.calls("furnace_add_input")], [2])
        self.assertEqual(result["data"]["remaining_input"], 1)
        furnace = self.node.furnaces[0]
        self.assertEqual(furnace["input"]["count"] + furnace["output"]["count"], 64)
        self.assertEqual(self.node.inventory["raw_iron"], 6)
        self.assertFalse(result["success"])

    async def test_full_furnace_uses_next_available_furnace(self):
        self.node.furnaces[0]["input"] = {"name": "raw_iron", "count": 64}
        second = copy.deepcopy(self.node.furnaces[0])
        second.update(position={"x": 12, "y": 64, "z": 0}, input=None)
        self.node.furnaces.append(second)
        result = await self.finish({"use_existing": True})
        self.assertTrue(result["success"], result)
        self.assertEqual(self.calls("furnace_add_input")[0]["position"]["x"], 12)
        self.assertEqual(self.node.furnaces[0]["input"]["count"], 64)

    async def test_full_output_uses_another_furnace_without_taking_products(self):
        self.node.furnaces[0]["output"] = {"name": "iron_ingot", "count": 64}
        second = copy.deepcopy(self.node.furnaces[0])
        second.update(position={"x": 12, "y": 64, "z": 0}, output=None)
        self.node.furnaces.append(second)
        result = await self.finish({"use_existing": True})
        self.assertTrue(result["success"], result)
        self.assertEqual(self.calls("furnace_add_input")[0]["position"]["x"], 12)
        self.assertEqual(self.node.furnaces[0]["output"]["count"], 64)
        self.assertFalse(self.calls("furnace_take_output"))

    async def test_loading_releases_control_for_next_movement_task(self):
        self.node.never_progress = True
        result = await self.finish()
        self.assertTrue(result["success"], result)
        open_count = len(self.calls("open_furnace"))
        request = TaskRequest.model_validate({"player": "Steve", "request_id": "after_loading",
            "steps": [{"step_id": "walk", "tool": "goto_position", "arguments": {"x": 9, "y": 64, "z": 1}}]})
        record = await self.executor.submit(request)
        await asyncio.wait_for(self.executor._work[record["task_id"]], 3)
        self.assertEqual(self.executor.view(record["task_id"], "Steve")["status"], "finished_unverified")
        self.assertEqual(self.node.location, {"x": 7, "y": 64, "z": 1})
        self.assertEqual(len(self.calls("open_furnace")), open_count)
        self.assertEqual(len(self.calls("furnace_add_input")), 1)

    async def test_smelting_default_budget_and_explicit_limits(self):
        request = self.request({"count": 64})
        self.assertEqual((request.timeout_seconds, request.max_tool_calls), (1200, 192))
        self.assertEqual(self.request(timeout_seconds=1800).timeout_seconds, 1800)
        with self.assertRaises(ValueError):
            self.request(timeout_seconds=1801)

    async def test_later_furnace_cannot_spend_fuel_reserved_for_first_furnace_refills(self):
        self.node.inventory = {"raw_iron": 8, "bamboo": 260}
        self.node.furnaces[0]["input"] = {"name": "raw_iron", "count": 63}
        second = copy.deepcopy(self.node.furnaces[0])
        second.update(position={"x": 12, "y": 64, "z": 0}, input=None)
        self.node.furnaces.append(second)
        result = await self.finish({"count": 8, "use_existing": True})
        self.assertEqual(result["error"]["code"], "INSUFFICIENT_FURNACE_CAPACITY")
        self.assertEqual([a["count"] for a in self.calls("furnace_add_input")], [1])
        self.assertEqual([a["count"] for a in self.calls("furnace_add_fuel")], [64])
        self.assertEqual(result["data"]["furnaces"][0]["fuel_reserved"], 192)
        self.assertEqual(result["data"]["remaining_input"], 7)

    async def test_reuse_existing_two_raw_iron_and_add_seven(self):
        self.node.furnaces[0].update(input={"name": "raw_iron", "count": 2}, fuelSeconds=20)
        result = await self.finish({"count": 7, "use_existing": True})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["existing_input"], 2)
        self.assertEqual(result["data"]["expected_increase"], 7)
        self.assertEqual(result["data"]["input_submitted_count"], 7)
        self.assertEqual([a["count"] for a in self.calls("furnace_add_input")], [7])
        self.assertEqual(self.calls("furnace_add_fuel")[0]["count"], 2)
        self.assertTrue(result["verification"]["verified"])

    async def test_reuse_full_input_stack_boundary_uses_real_fuel_requirements(self):
        self.node.furnaces[0]["input"] = {"name": "raw_iron", "count": 57}
        self.node.inventory["coal"] = 8
        result = await self.finish({"count": 7, "use_existing": True}, max_tool_calls=192)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["input_submitted_count"], 7)
        self.assertEqual(self.calls("furnace_add_fuel")[0]["count"], 8)
        self.assertEqual(len(self.calls("furnace_add_input")), 1)

    async def test_weak_fuel_fills_one_stack_and_reports_followup_without_refilling(self):
        self.node.furnaces[0]["input"] = {"name": "raw_iron", "count": 57}
        self.node.inventory = {"raw_iron": 7, "bamboo": 256}
        self.node.never_progress = True
        result = await self.finish({"count": 7, "use_existing": True})
        self.assertTrue(result["success"], result)
        self.assertEqual([a["count"] for a in self.calls("furnace_add_fuel")], [64])
        self.assertEqual(result["data"]["fuel_pending_count"], 192)
        self.assertTrue(result["data"]["requires_fuel_followup"])
        self.assertEqual(self.node.inventory["bamboo"], 192)
        self.assertEqual(len(self.calls("furnace_add_input")), 1)
        self.assertFalse(self.calls("furnace_take_output"))

    async def test_existing_partial_output_is_preserved_with_explicit_opt_in(self):
        self.node.never_progress = True
        self.node.furnaces[0].update(input={"name": "raw_iron", "count": 2}, output={"name": "iron_ingot", "count": 5})
        result = await self.finish({"count": 7, "use_existing": True})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["furnaces"][0]["existing_output"], 5)
        self.assertEqual(self.node.furnaces[0]["output"]["count"], 5)
        self.assertEqual(self.node.furnaces[0]["input"]["count"], 9)
        self.assertFalse(self.calls("furnace_take_output"))

    async def test_no_furnace_outside_vertical_range_or_invisible(self):
        self.node.furnaces[0]["position"]["y"] = 90
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "FURNACE_NOT_FOUND")
        self.node.furnaces[0]["position"]["y"] = 64
        self.node.invisible = True
        result = await self.finish(request_id="invisible")
        self.assertEqual(result["error"]["code"], "NO_COMPATIBLE_FURNACE")
        self.assertEqual(result["data"]["furnace_check"]["reason"], "NO_LINE_OF_SIGHT")

    async def test_explicit_position_skips_search(self):
        self.node.furnaces[0]["position"]["x"] = 2
        result = await self.finish({"furnace_position": {"x": 2, "y": 64, "z": 0}})
        self.assertTrue(result["success"], result)
        self.assertFalse(self.calls("find_block"))
        self.assertFalse(self.calls("goto_position"))

    async def test_full_inventory_for_products_does_not_block_loading(self):
        self.node.full = True
        result = await self.finish()
        self.assertTrue(result["success"], result)
        self.assertFalse(self.calls("furnace_take_output"))

    async def test_normal_processing_during_load_is_accounted_for_in_verification(self):
        result = await self.finish()
        self.assertTrue(result["success"], result)
        furnace = result["data"]["furnaces"][0]
        self.assertEqual(furnace["loaded_furnace"]["output"]["count"], 1)
        self.assertEqual(furnace["loaded_furnace"]["input"]["count"], 2)
        self.assertTrue(result["verification"]["verified"])
        self.assertNotIn("iron_ingot", self.node.inventory)

    async def test_lost_input_response_uses_receipt_and_duplicate_request_not_repeated(self):
        self.node.lost_input_response = True
        result = await self.finish()
        self.assertTrue(result["success"], result)
        again = await self.manager.submit(self.request())
        self.assertEqual(again["execution_id"], result["execution_id"])
        self.assertEqual(len(self.calls("furnace_add_input")), 1)

    async def test_uncertain_input_stops_without_fuel_or_second_input(self):
        self.node.uncertain_input = True
        result = await self.finish()
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse(self.calls("furnace_add_fuel"))
        self.assertEqual(len(self.calls("furnace_add_input")), 1)

    async def test_known_tool_failure_is_preserved_without_second_input(self):
        self.node.fail_tool = "furnace_add_input"
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "BLOCK_CHANGED")
        self.assertFalse(self.calls("furnace_add_fuel"))
        self.assertEqual(len(self.calls("furnace_add_input")), 1)

    async def test_materials_changed_during_navigation_are_rechecked(self):
        self.node.consume_materials_on_move = True
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "MATERIALS_MISSING")
        self.assertFalse(self.calls("furnace_add_input"))

    async def test_other_process_after_deposit_stops_without_switching(self):
        self.node.change_after_input = True
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "FURNACE_CHANGED")
        self.assertEqual(len(self.calls("find_block")), 1)
        self.assertEqual(len(self.calls("furnace_add_input")), 1)

    async def test_removed_input_without_output_cannot_claim_loading_success(self):
        self.node.remove_input = True
        result = await self.finish()
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "GOAL_NOT_VERIFIED")

    async def test_legacy_wait_timeout_is_accepted_but_does_not_wait_for_products(self):
        self.node.never_progress = True
        result = await self.finish({"wait_timeout": 1})
        self.assertTrue(result["success"], result)
        self.assertEqual(len(self.calls("open_furnace")), 3)
        self.assertFalse(self.calls("furnace_take_output"))

    async def test_cancel_during_fuel_submission_retains_input_and_releases_lease(self):
        entered = asyncio.Event()
        blocked = asyncio.Event()

        async def handle(request):
            if request.url.path == "/api/tools/execute" and json.loads(request.content)["tool"] == "furnace_add_fuel":
                entered.set()
                await blocked.wait()
            return self.node.handle(request)

        self.executor.client.transport = httpx.MockTransport(handle)
        record = await self.manager.submit(self.request())
        await asyncio.wait_for(entered.wait(), 2)
        result = await self.manager.cancel(record["execution_id"], "Steve")
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(len(self.calls("furnace_add_input")), 1)
        self.assertEqual(self.node.furnaces[0]["input"]["count"], 3)
        self.assertFalse(self.calls("furnace_add_fuel"))
        self.assertFalse(self.node.leased)

    async def test_total_timeout_call_limit_and_disconnect(self):
        self.node.pending_forever = True
        result = await self.finish(timeout_seconds=0.03)
        self.assertEqual(result["error"]["code"], "TASK_TIMEOUT")
        self.node.pending_forever = False
        result = await self.finish(request_id="budget", max_tool_calls=1)
        self.assertEqual(result["error"]["code"], "CALL_LIMIT")
        self.node.offline = True
        result = await self.finish(request_id="offline")
        self.assertEqual(result["error"]["code"], "BOT_NOT_READY")

    async def test_disconnect_and_dimension_change_after_loading_prevent_success(self):
        for flag, code in (("offline", "BOT_NOT_READY"), ("dimension", "DIMENSION_CHANGED")):
            self.node.offline = False
            self.node.dimension = "overworld"
            self.node.started = False
            self.node.inventory = {"raw_iron": 8, "coal": 2}
            self.node.furnaces[0].update(input=None, output=None, fuel=None, fuelSeconds=0)

            def handle(request):
                if request.url.path == "/api/world" and self.node.started:
                    setattr(self.node, flag, True if flag == "offline" else "the_nether")
                return self.node.handle(request)

            self.executor.client.transport = httpx.MockTransport(handle)
            result = await self.finish(request_id=flag)
            self.assertEqual(result["error"]["code"], code)
            self.assertFalse(result["success"])
            self.assertFalse(self.calls("furnace_take_output"))
            self.assertFalse(self.node.leased)

    async def test_unavailable_inventory_is_not_treated_as_zero_resources(self):
        self.node.invalid_inventory = True
        result = await self.finish()
        self.assertEqual(result["error"]["code"], "INVENTORY_UNAVAILABLE")
        self.assertFalse(self.node.calls)

    async def test_independent_verifier_checks_inventory_and_input_consumption(self):
        before = self.node.world()
        result = await self.finish()
        after = self.node.world()
        after["inventory"] = before["inventory"]
        verification = SmeltItem().verify(SmeltParameters(item_name="raw_iron", count=3), result["data"], before, after)
        self.assertFalse(all(c.passed for c in verification.checks))

    async def test_independent_verifier_rejects_inconsistent_per_furnace_load(self):
        before = self.node.world()
        result = await self.finish()
        data = copy.deepcopy(result["data"])
        data["furnaces"][0]["input_submitted_count"] = 1
        verification = SmeltItem().verify(SmeltParameters(item_name="raw_iron", count=3), data, before, self.node.world())
        self.assertFalse(next(c for c in verification.checks if c.criterion == "input_deposited").passed)

    async def test_independent_verifier_requires_actual_fuel_inventory_decrease(self):
        before = self.node.world()
        result = await self.finish()
        after = self.node.world()
        next(i for i in after["inventory"] if i["name"] == "coal")["count"] = 2
        verification = SmeltItem().verify(SmeltParameters(item_name="raw_iron", count=3), result["data"], before, after)
        self.assertFalse(next(c for c in verification.checks if c.criterion == "fuel_deposited").passed)

    async def test_http_lifecycle_and_player_isolation(self):
        with patch.object(main, "skill_manager", self.manager):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://agent") as client:
                response = await client.post("/api/agent/skills/execute", json=self.request().model_dump())
                self.assertEqual(response.status_code, 202)
                execution_id = response.json()["data"]["execution_id"]
                await self.executor._work[execution_id]
                path = f"/api/agent/skills/executions/{execution_id}"
                self.assertTrue((await client.get(path, params={"player": "Steve"})).json()["data"]["success"])
                self.assertEqual((await client.get(path, params={"player": "Alex"})).status_code, 404)

    async def test_real_node_http_and_official_furnace_plugin_complete_skill(self):
        # 实际 HTTP、Node 工具和 Mineflayer 熔炉插件；炉况/背包与加工时钟受控，不连接 Minecraft。
        script = r"""
const {EventEmitter}=require('node:events');const {Vec3}=require('vec3');
const {createToolSystem}=require('./src/tools');const {createApiServer}=require('./src/httpServer');
const bot=new EventEmitter();bot.registry=require('prismarine-registry')('1.21.4');
const Item=require('prismarine-item')(bot.registry),windows=require('prismarine-windows')(bot.registry);
const win=windows.createWindow(1,'minecraft:furnace','Furnace');
bot.inventory=windows.createWindow(0,'minecraft:inventory','Inventory');
const item=(name,n,slot)=>{const i=new Item(bot.registry.itemsByName[name].id,n,0);i.slot=slot;return i;};
win.slots[3]=item('raw_iron',64,3);win.slots[4]=item('coal',8,4);
bot.inventory.slots[9]=item('raw_iron',64,9);bot.inventory.slots[10]=item('coal',8,10);
bot.entity={id:1,position:new Vec3(0,64,0)};bot.entities={};bot.isAlive=true;
bot.game={dimension:'overworld',minY:-64,height:384};const target=new Vec3(10,64,0);
bot.blockAt=p=>({name:p.floored().equals(target)?'furnace':p.y<64?'stone':'air',position:p.floored(),
 boundingBox:p.y<64?'block':'empty',shapes:p.y<64?[[0,0,0,1,1,1]]:[]});
bot.findBlocks=()=>[target];bot.canDigBlock=()=>true;bot.canSeeBlock=()=>true;
bot.world={raycast:()=>({position:target})};bot._client=new EventEmitter();let heat=0;
const processOne=()=>{if(win.slots[0]&&(heat>0||win.slots[1])){if(!heat){win.slots[1].count--;if(!win.slots[1].count)win.slots[1]=null;heat=80;}
 win.slots[0].count--;if(!win.slots[0].count)win.slots[0]=null;
 if(win.slots[2])win.slots[2].count++;else win.slots[2]=item('iron_ingot',1,2);heat-=10;}};
bot.openBlock=async()=>{processOne();bot.currentWindow=win;setImmediate(()=>{
 bot._client.emit('craft_progress_bar',{windowId:1,property:1,value:1600});
 bot._client.emit('craft_progress_bar',{windowId:1,property:0,value:heat*20});});return win;};
bot.closeWindow=async w=>{for(let i=3;i<39;i++)bot.inventory.slots[i+6]=w.slots[i]?item(w.slots[i].name,w.slots[i].count,i+6):null;
 bot.currentWindow=null;win.emit('close');};
const move=(start,end,dstart,dend,type,n)=>{let left=n;for(let i=start;i<end&&left;i++){const src=win.slots[i];if(!src||src.type!==type)continue;
 for(let j=dstart;j<dend&&left&&src.count;j++){const dest=win.slots[j];if(dest&&dest.type!==type)continue;const m=Math.min(left,src.count,64-(dest?.count??0));
 if(!m)continue;if(dest)dest.count+=m;else win.slots[j]=item(src.name,m,j);src.count-=m;left-=m;}if(!src.count)win.slots[i]=null;}if(left)throw Error('fixture lacks items/space');};
bot.transfer=async a=>{if(a.destStart===2)throw Error('output is read-only');move(a.sourceStart,a.sourceEnd,a.destStart,a.destEnd,a.itemType,a.count);};
bot.putAway=async slot=>{if(slot!==2)throw Error('only output');move(2,3,3,39,win.slots[2].type,win.slots[2].count);};
require('mineflayer/lib/plugins/furnace')(bot);
let task,taskId=0;const movement={getState:()=>({type:'idle'}),stop:()=>({ok:true}),
 goTo:(who,x,y,z,options={})=>{bot.entity.position=new Vec3(x-(options.arrivalRange??2),y,z);task={taskId:++taskId,status:'arrived'};return {ok:true,taskId};},getTask:()=>task};
const perception={getWorldSnapshot:()=>({availability:{player:'available',inventory:'available'},
 player:{position:{x:bot.entity.position.x,y:bot.entity.position.y,z:bot.entity.position.z},alive:true,dimension:'overworld'},
 inventory:bot.inventory.items().map(i=>({name:i.name,count:i.count,slot:i.slot})),nearbyEntities:[],nearbyBlocks:[],timestamp:Date.now()})};
bot.tools=createToolSystem(bot,{movement,perception,logger:{log(){},error(){}}});bot.tools.guardMovement();bot.emit('spawn');
bot.mineMateStatus=()=>({connected:true,spawned:true});
const server=createApiServer({bot,movement,perception,logger:{log(){},error(){}}});server.listen(0,'127.0.0.1',()=>console.log(server.address().port));
"""
        process = await asyncio.create_subprocess_exec("node", "-e", script, cwd=self.root,
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        executor = None
        try:
            port = int(await asyncio.wait_for(process.stdout.readline(), 5))
            executor = TaskManager(ToolClient(Settings(f"http://127.0.0.1:{port}", 2, "127.0.0.1", 8000)), poll_interval=0.001)
            manager = SkillManager(self.registry, executor)
            record = await manager.submit(self.request({"count": 64}))
            await asyncio.wait_for(executor._work[record["execution_id"]], 45)
            result = manager.view(record["execution_id"], "Steve")
            self.assertTrue(result["success"], result)
            self.assertEqual(result["data"]["input_submitted_count"], 64)
            names = [s["tool"] for s in result["steps"]]
            self.assertEqual(names.count("furnace_add_input"), 1)
            self.assertEqual(names.count("furnace_add_fuel"), 1)
            self.assertNotIn("smelt_item", names)
            self.assertNotIn("furnace_take_output", names)
            self.assertEqual(result["data"]["output_taken"], 0)
            self.assertEqual(result["data"]["furnaces"][0]["loaded_furnace"]["input"]["count"], 63)
            self.assertIn("get_movement_task", names)
            self.assertTrue(result["verification"]["verified"])
        finally:
            if executor:
                await executor.close()
            if process.returncode is None:
                process.kill()
            await process.wait()
