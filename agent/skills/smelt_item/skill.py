import math

from app.execution.manager import ExecutionError, inventory_counts

from ..base import Skill
from ..explore_and_find import distance, player, position
from ..models import SkillCheck, SkillVerification
from ..runtime import SkillRuntime
from . import metadata
from .metadata import SmeltParameters

MAX_QUEUE = 64


class SmeltItem(Skill):
    name = metadata.NAME
    description = metadata.DESCRIPTION
    parameters = SmeltParameters
    preconditions = metadata.PRECONDITIONS
    success_criteria = metadata.SUCCESS_CRITERIA
    required_tools = metadata.REQUIRED_TOOLS

    async def execute(self, runtime: SkillRuntime, parameters: SmeltParameters) -> dict:
        return await _Smelt(runtime, parameters).run()

    def verify(self, parameters, data, before, after):
        _, dimension = player(after)
        old, new = inventory_counts(before), inventory_counts(after)
        consumed = old.get(data["input_name"], 0) - new.get(data["input_name"], 0)
        furnaces = data.get("furnaces", [])
        fuel_counts = {}
        for furnace in furnaces:
            if furnace.get("fuel_added", 0):
                name = furnace["fuel_name"]
                fuel_counts[name] = fuel_counts.get(name, 0) + furnace["fuel_added"]
        fuel_evidence = [{"name": name, "expected_decrease": amount,
                          "actual_decrease": old.get(name, 0) - new.get(name, 0) - (parameters.count if name == data["input_name"] else 0)}
                         for name, amount in fuel_counts.items()]

        def load_confirmed(furnace):
            first, last = furnace.get("before_furnace"), furnace.get("loaded_furnace")
            if not first or not last:
                return False
            def amount(state, key, name):
                item = state.get(key)
                return item["count"] if item and item["name"] == name else 0
            # 加工可与投料同时发生；按 Node 配方折算原料与产品守恒，不要求输入槽保持原数。
            ratio = furnace["output_per_input"]
            first_mass = amount(first, "input", data["input_name"]) * ratio + amount(first, "output", data["output_name"])
            last_mass = amount(last, "input", data["input_name"]) * ratio + amount(last, "output", data["output_name"])
            return (all(state[slot] is None or state[slot]["name"] == name for state in (first, last)
                        for slot, name in (("input", data["input_name"]), ("output", data["output_name"])))
                    and math.isclose(last_mass - first_mass, furnace["input_submitted_count"] * ratio))

        return SkillVerification(checks=[
            SkillCheck(criterion="input_deposited", passed=data["input_submitted_count"] == parameters.count and data["remaining_input"] == 0
                       and sum(f["input_submitted_count"] for f in furnaces) == parameters.count
                       and consumed >= parameters.count + fuel_counts.get(data["input_name"], 0),
                       observed={"requested": parameters.count, "submitted": data["input_submitted_count"], "inventory_decrease": consumed}),
            SkillCheck(criterion="fuel_deposited", passed=bool(furnaces) and all(
                f["fuel_added"] == f["fuel_plan"]["add"] for f in furnaces)
                and all(f["actual_decrease"] >= f["expected_decrease"] for f in fuel_evidence), observed=fuel_evidence),
            SkillCheck(criterion="furnace_load_confirmed", passed=bool(furnaces) and all(load_confirmed(f) for f in furnaces),
                       observed=[{"position": f["position"], "before": f.get("before_furnace"), "after": f.get("loaded_furnace")} for f in furnaces]),
            SkillCheck(criterion="same_dimension", passed=dimension == data["dimension"] == before.get("player", {}).get("dimension"), observed=dimension),
        ])


class _Smelt:
    def __init__(self, runtime: SkillRuntime, parameters: SmeltParameters):
        self.runtime, self.p, self.serial = runtime, parameters, 0
        self.current = None
        self.used = set()
        self.data = {"stage": "resolving", "query": parameters.item_name, "input_name": None, "output_name": None,
                     "count_mode": "increase", "requested_input": parameters.count, "expected_increase": None,
                     "initial_input_count": None, "initial_output_count": None, "current_output_count": None,
                     "actual_increase": 0, "origin": None, "dimension": None, "recipe": None,
                     "furnace_position": None, "furnace_attempts": [], "furnaces_truncated": False,
                     "furnace_check": None, "furnace_state": None, "final_furnace": None,
                     "use_existing": parameters.use_existing, "existing_collected": 0, "existing_input": 0,
                     "expected_total_take": 0, "input_submit_attempted": False, "input_submitted_count": 0,
                     "fuel_name": None, "fuel_plan": None, "fuel_added": 0, "fuel_refills": 0,
                     # 兼容旧观测字段；本 Skill 不等待、收取或自动续补，以下均保持零。
                     "output_taken": 0, "polls": 0, "wait_elapsed": 0,
                     "completion_scope": "furnace_loading", "smelting_completed": None,
                     "fuel_pending_count": 0, "requires_fuel_followup": False,
                     "remaining_input": parameters.count, "furnaces": []}

    def progress(self, stage: str) -> None:
        self.data["stage"] = stage
        self.runtime.report_progress(self.data)

    async def call(self, name: str, args: dict) -> dict:
        self.serial += 1
        return (await self.runtime.call(name, args, operation_id=f"smelt_{self.serial}", wait_seconds=75))["data"]

    async def world(self) -> dict:
        world = await self.runtime.snapshot()
        _, dimension = player(world)
        if self.data["dimension"] is not None and self.data["dimension"] != dimension:
            raise ExecutionError("DIMENSION_CHANGED")
        inventory_counts(world)
        return world

    async def resolve(self, query: str) -> str:
        result = await self.call("resolve_resource", {"query": query.strip(), "kind": "item"})
        candidates = result.get("candidates") or []
        if not candidates or result.get("ambiguous") is not False:
            self.data["candidates"] = candidates[:8]
            self.progress("clarification_required")
            raise ExecutionError("RESOURCE_AMBIGUOUS" if candidates else "RESOURCE_UNKNOWN")
        if candidates[0].get("kind") != "item" or not isinstance(candidates[0].get("name"), str):
            raise ExecutionError("INVALID_RESOURCE_RESULT")
        return candidates[0]["name"]

    async def recipe(self, count: int) -> dict:
        result = await self.call("inspect_smelting_recipe", {"item_name": self.data["input_name"], "count": count})
        source, output, fuels = result.get("input", {}), result.get("output", {}), result.get("fuels")
        if (source.get("name") != self.data["input_name"] or source.get("count") != count
                or not isinstance(output.get("name"), str) or type(output.get("count")) is not int or output["count"] <= 0
                or not isinstance(fuels, list) or len(fuels) > 128):
            raise ExecutionError("INVALID_RECIPE_RESULT")
        for fuel in fuels:
            # 弱燃料的总需求可超过一栈；查询结果有效不代表能一次投入燃料槽。
            if not isinstance(fuel.get("name"), str) or type(fuel.get("count")) is not int or not 1 <= fuel["count"] <= MAX_QUEUE * 4:
                raise ExecutionError("INVALID_RECIPE_RESULT")
        if self.data["output_name"] is not None and self.data["output_name"] != output["name"]:
            raise ExecutionError("RECIPE_CHANGED")
        return result

    async def furnace(self) -> dict:
        await self.world()
        state = await self.call("open_furnace", {"position": self.data["furnace_position"]})
        if state.get("type") != "furnace" or any(key not in state for key in ("input", "fuel", "output")):
            raise ExecutionError("INVALID_FURNACE_RESULT")
        for key in ("input", "fuel", "output"):
            item = state[key]
            if item is not None and (not isinstance(item, dict) or not isinstance(item.get("name"), str)
                                     or type(item.get("count")) is not int or not 1 <= item["count"] <= 64):
                raise ExecutionError("INVALID_FURNACE_RESULT")
        self.data["furnace_state"] = state
        self.runtime.report_progress(self.data)
        return state

    def compatible(self, state: dict) -> bool:
        return ((state["input"] is None or state["input"]["name"] == self.data["input_name"])
                and (state["output"] is None or state["output"]["name"] == self.data["output_name"]))

    async def approach(self, target: dict) -> bool:
        async def inspect() -> bool:
            own, _ = player(await self.world())
            reach = distance({**own, "y": own["y"] + 1.5}, {a: target[a] + 0.5 for a in ("x", "y", "z")})
            check = {"position": target, "bot_position": own, "interaction_distance": reach, "reason": "OUT_OF_RANGE"}
            self.data["furnace_check"] = check
            self.runtime.report_progress(self.data)
            if reach > 4.5:
                return False
            try:
                result = await self.call("inspect_block", {"position": target})
            except ExecutionError as error:
                if error.uncertain or error.code not in {"OUT_OF_RANGE", "CHUNK_UNAVAILABLE"}:
                    raise
                check["reason"] = error.code
                self.runtime.report_progress(self.data)
                return False
            ok = result.get("name") == "furnace" and result.get("visible") is True
            check.update(actual_name=result.get("name"), visible=result.get("visible"),
                         reason="READY" if ok else "BLOCK_CHANGED" if result.get("name") != "furnace" else "NO_LINE_OF_SIGHT")
            self.runtime.report_progress(self.data)
            return ok

        if await inspect():
            return True
        self.progress("approaching_furnace")
        try:
            result = await self.call("find_interaction_positions", {"position": target, "radius": 3, "max_results": 6})
        except ExecutionError as error:
            if error.uncertain or error.code not in {"OUT_OF_RANGE", "CHUNK_UNAVAILABLE"}:
                raise
            return False
        own, _ = player(await self.world())
        points = sorted([position(c.get("position")) for c in result.get("candidates", [])], key=lambda p: distance(own, p))
        for point in points[:3]:
            goal = {a: math.floor(point[a]) for a in ("x", "y", "z")}
            if distance(goal, self.data["origin"]) > self.p.search_radius + 3:
                continue
            try:
                await self.call("goto_position", {**goal, "wait_seconds": 0, "arrival_range": 0})
            except ExecutionError as error:
                if error.uncertain or error.code not in {"NAVIGATION_FAILED", "MOVEMENT_REJECTED", "STEP_FAILED"}:
                    raise
                continue
            if await inspect():
                return True
        return False

    def fuel_plan(self, state: dict, recipe: dict, inventory: dict, *, reserve_input: int) -> dict | None:
        current = state["fuel"]
        names = {f["name"]: f for f in recipe["fuels"]}
        specified = self.data["fuel_name"]
        if current is not None:
            if current["name"] not in names or specified is not None and specified != current["name"]:
                return None
            choices = [names[current["name"]]]
        else:
            choices = [names[specified]] if specified in names else [] if specified else recipe["fuels"]
        for fuel in choices:
            stored = current["count"] if current is not None else 0
            needed = max(0, fuel["count"] - stored)
            available = inventory.get(fuel["name"], 0) - (reserve_input if fuel["name"] == self.data["input_name"] else 0)
            # 前一座炉尚未放入的弱燃料不能再承诺给下一座炉。
            available -= sum(f.get("fuel_reserved", 0) for f in self.data["furnaces"]
                             if f.get("fuel_name") == fuel["name"])
            if available >= needed:
                # 弱燃料先装满燃料槽；剩余需求只记录，本 Skill 不等待或自动续补。
                return {"name": fuel["name"], "required": fuel["count"], "stored": stored,
                        "add": min(needed, 64 - stored), "active_only": False}
        # 允许沿用已观察到的燃烧；剩余加工容量未知，结果明确要求后续检查。
        active = state.get("fuelSeconds")
        if specified is None and isinstance(active, (int, float)) and active > 0:
            return {"name": None, "required": None, "stored": 0, "add": 0, "active_only": True}
        return None

    async def select_furnace(self) -> dict:
        self.progress("searching_furnace")
        if self.p.furnace_position is not None:
            targets = [self.p.furnace_position.model_dump()]
        else:
            found = await self.call("find_block", {"block_name": "furnace", "radius": self.p.search_radius, "max_results": 8})
            targets = [position(e.get("position")) for e in found.get("blocks", []) if e.get("name") == "furnace"]
            self.data["furnaces_truncated"] = bool(found.get("truncated"))
        targets = sorted({tuple(p[a] for a in ("x", "y", "z")): p for p in targets
                          if distance(self.data["origin"], p) <= self.p.search_radius}.values(), key=lambda p: distance(self.data["origin"], p))
        if not targets:
            raise ExecutionError("FURNACE_NOT_FOUND")
        for target in targets[:8]:
            if tuple(target[a] for a in ("x", "y", "z")) in self.used:
                continue
            entry = {"position": target, "reason": "UNREACHABLE"}
            self.data["furnace_attempts"].append(entry)
            if not await self.approach(target):
                continue
            self.data["furnace_position"] = target
            try:
                state = await self.furnace()
            except ExecutionError as error:
                if error.uncertain or error.code not in {"WRONG_BLOCK", "OUT_OF_RANGE", "CHUNK_UNAVAILABLE"}:
                    raise
                entry["reason"] = error.code
                continue
            if not self.compatible(state):
                entry["reason"] = "OTHER_PROCESS"
                continue
            if not self.p.use_existing and (state["input"] is not None or state["output"] is not None):
                entry["reason"] = "EXISTING_ITEMS"
                continue
            if state["output"] is not None and state["output"]["count"] >= MAX_QUEUE:
                entry["reason"] = "FURNACE_OUTPUT_FULL"
                continue
            queued = state["input"]["count"] if state["input"] else 0
            amount = min(self.data["remaining_input"], MAX_QUEUE - queued)
            entry.update(existing_input=queued, requested_input=self.data["remaining_input"],
                         accepted_input=amount, total_input=queued + amount, queue_limit=MAX_QUEUE)
            if amount <= 0:
                entry["reason"] = "FURNACE_INPUT_FULL"
                continue
            recipe = await self.recipe(queued + amount)
            inventory = inventory_counts(await self.world())
            if inventory.get(self.data["input_name"], 0) < self.data["remaining_input"]:
                raise ExecutionError("MATERIALS_MISSING")
            plan = self.fuel_plan(state, recipe, inventory, reserve_input=self.data["remaining_input"])
            if plan is None:
                entry["reason"] = "FUEL_MISSING_OR_CONFLICT"
                continue
            entry["reason"] = "SELECTED"
            self.data["fuel_plan"] = plan
            self.runtime.report_progress(self.data)
            return state
        self.progress("furnace_unavailable")
        raise ExecutionError("NO_COMPATIBLE_FURNACE")

    async def add_fuel(self, plan: dict) -> None:
        self.current.update(fuel_name=plan["name"], fuel_reserved=0 if plan["active_only"] else max(
            0, plan["required"] - plan["stored"] - plan["add"]))
        if plan["add"]:
            self.progress("adding_fuel")
            await self.call("furnace_add_fuel", {"position": self.data["furnace_position"], "fuel_name": plan["name"], "count": plan["add"]})
            self.data["fuel_added"] += plan["add"]
            self.current["fuel_added"] += plan["add"]
            self.runtime.report_progress(self.data)
        self.current["fuel_status"] = ("additional_fuel_needed" if self.current["fuel_reserved"] else
                                       "remaining_capacity_unknown" if plan["active_only"] else "prepared")
        self.data["fuel_pending_count"] = sum(f.get("fuel_reserved", 0) for f in self.data["furnaces"])
        self.data["requires_fuel_followup"] = any(f.get("fuel_status") in {
            "additional_fuel_needed", "remaining_capacity_unknown"} for f in self.data["furnaces"])

    async def run(self) -> dict:
        world = await self.world()
        self.data["origin"], self.data["dimension"] = player(world)
        self.progress("resolving")
        self.data["input_name"] = await self.resolve(self.p.item_name)
        if self.p.fuel_name:
            self.data["fuel_name"] = await self.resolve(self.p.fuel_name)
        recipe = await self.recipe(self.p.count)
        if self.data["fuel_name"] is not None and self.data["fuel_name"] not in {f["name"] for f in recipe["fuels"]}:
            raise ExecutionError("UNSUPPORTED_FUEL")
        self.data.update(output_name=recipe["output"]["name"], expected_increase=recipe["output"]["count"],
                         recipe={"input": recipe["input"], "output": recipe["output"]})
        counts = inventory_counts(world)
        self.data.update(initial_input_count=counts.get(self.data["input_name"], 0),
                         initial_output_count=counts.get(self.data["output_name"], 0),
                         current_output_count=counts.get(self.data["output_name"], 0))
        if self.data["initial_input_count"] < self.p.count:
            self.data["material_shortage"] = self.p.count - self.data["initial_input_count"]
            self.progress("materials_missing")
            raise ExecutionError("MATERIALS_MISSING")
        # 先填当前炉，装满后把剩余原料分配给其它兼容炉；全部副作用仍串行执行。
        while self.data["remaining_input"] > 0:
            try:
                await self.select_furnace()
            except ExecutionError as error:
                if error.code == "NO_COMPATIBLE_FURNACE" and self.data["input_submitted_count"]:
                    self.progress("partial_capacity")
                    raise ExecutionError("INSUFFICIENT_FURNACE_CAPACITY") from None
                raise
            await self.load_furnace()
        self.progress("loaded")
        return self.data

    async def load_furnace(self) -> None:
        # 每座炉只提交一次原料；写入结果不确定时停止，不换炉重投。
        state = await self.furnace()
        if not self.compatible(state) or not self.p.use_existing and (state["input"] or state["output"]):
            raise ExecutionError("FURNACE_CHANGED")
        if state["output"] is not None and state["output"]["count"] >= MAX_QUEUE:
            raise ExecutionError("FURNACE_OUTPUT_FULL")
        self.current = {"position": self.data["furnace_position"], "input_submitted_count": 0,
                        "existing_input": 0, "existing_output": state["output"]["count"] if state["output"] else 0,
                        "expected_output": 0, "fuel_added": 0, "fuel_reserved": 0,
                        "before_furnace": state, "loaded_furnace": None}
        self.data["furnaces"].append(self.current)
        queued = state["input"]["count"] if state["input"] else 0
        amount = min(self.data["remaining_input"], MAX_QUEUE - queued)
        if amount <= 0:
            raise ExecutionError("FURNACE_INPUT_FULL")
        recipe = await self.recipe(queued + amount)
        inventory = inventory_counts(await self.world())
        if inventory.get(self.data["input_name"], 0) < self.data["remaining_input"]:
            raise ExecutionError("MATERIALS_MISSING")
        plan = self.fuel_plan(state, recipe, inventory, reserve_input=self.data["remaining_input"])
        if plan is None:
            raise ExecutionError("FUEL_MISSING")
        expected = self.current["existing_output"] + recipe["output"]["count"]
        self.current.update(existing_input=queued, expected_output=expected, fuel_plan=plan,
                            output_per_input=recipe["output"]["count"] / recipe["input"]["count"])
        self.data.update(existing_input=self.data["existing_input"] + queued, fuel_plan=plan)
        self.data["input_submit_attempted"] = True
        self.progress("adding_input")
        await self.call("furnace_add_input", {"position": self.data["furnace_position"], "item_name": self.data["input_name"], "count": amount})
        self.current["input_submitted_count"] = amount
        self.data["input_submitted_count"] += amount
        self.data["remaining_input"] -= amount
        self.used.add(tuple(self.data["furnace_position"][a] for a in ("x", "y", "z")))
        await self.add_fuel(plan)
        # 只重读一次确认实际投料，可观察自然加工变化；不轮询、不等待产品。
        self.current["loaded_furnace"] = await self.furnace()
        if not self.compatible(self.current["loaded_furnace"]):
            raise ExecutionError("FURNACE_CHANGED", uncertain=True)

