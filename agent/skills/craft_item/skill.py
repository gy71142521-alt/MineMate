import logging
import math

from app.execution.manager import ExecutionError, inventory_counts

from ..base import Skill
from ..explore_and_find import distance, player, position
from ..models import SkillCheck, SkillVerification
from ..runtime import SkillRuntime
from . import metadata
from .metadata import CraftParameters

logger = logging.getLogger("minemate.skills")


class CraftItem(Skill):
    name = metadata.NAME
    description = metadata.DESCRIPTION
    parameters = CraftParameters
    preconditions = metadata.PRECONDITIONS
    success_criteria = metadata.SUCCESS_CRITERIA
    required_tools = metadata.REQUIRED_TOOLS

    async def execute(self, runtime: SkillRuntime, parameters: CraftParameters) -> dict:
        return await _Craft(runtime, parameters).run()

    def verify(self, parameters, data, before, after):
        _, dimension = player(after)
        item = data["item_name"]
        initial = max(inventory_counts(before).get(item, 0), data["initial_count"])
        final = inventory_counts(after).get(item, 0)
        return SkillVerification(checks=[
            SkillCheck(criterion="inventory_increase_reached", passed=final - initial >= parameters.count,
                       observed={"item": item, "before": initial, "after": final,
                                 "actual_increase": final - initial, "requested_increase": parameters.count}),
            SkillCheck(criterion="same_dimension",
                       passed=dimension == data["dimension"] == before.get("player", {}).get("dimension"),
                       observed=dimension),
        ])


class _Craft:
    def __init__(self, runtime: SkillRuntime, parameters: CraftParameters):
        self.runtime, self.p, self.serial = runtime, parameters, 0
        self.data = {"stage": "resolving", "query": parameters.item_name, "item_name": None,
                     "count_mode": "increase", "requested_increase": parameters.count,
                     "initial_count": None, "current_count": None, "actual_increase": 0,
                     "origin": None, "dimension": None, "recipe": None, "recipes_truncated": False,
                     "material_shortages": [], "table_position": None, "tables_truncated": False,
                     "table_attempts": [], "workbench_check": None,
                     "craft_submitted": False, "tool_result": None}

    def progress(self, stage: str) -> None:
        self.data["stage"] = stage
        self.runtime.report_progress(self.data)

    def workbench_failure(self) -> ExecutionError:
        self.progress("workbench_unavailable")
        check = self.data["workbench_check"] or {}
        logger.warning("[Skill] 工作台最后检查 reason=%s distance=%s actual=%s visible=%s target=%s bot=%s",
                       check.get("reason"), check.get("interaction_distance"), check.get("actual_name"),
                       check.get("visible"), check.get("position"), check.get("bot_position"))
        return ExecutionError("WORKBENCH_UNREACHABLE_OR_CHANGED")

    async def call(self, name: str, arguments: dict) -> dict:
        self.serial += 1
        result = await self.runtime.call(name, arguments, operation_id=f"craft_{self.serial}", wait_seconds=75)
        return result["data"]

    async def world(self) -> dict:
        world = await self.runtime.snapshot()
        _, dimension = player(world)
        if self.data["dimension"] is not None and self.data["dimension"] != dimension:
            raise ExecutionError("DIMENSION_CHANGED")
        inventory_counts(world)
        return world

    async def recipe(self) -> dict:
        self.progress("checking_recipe")
        result = await self.call("inspect_recipe", {"item_name": self.data["item_name"],
                                                   "count": self.p.count, "max_results": 16})
        recipes = result.get("recipes")
        if not isinstance(recipes, list) or len(recipes) > 16:
            raise ExecutionError("INVALID_RECIPE_RESULT")
        self.data["recipes_truncated"] = bool(result.get("truncated"))
        if not recipes:
            raise ExecutionError("NO_RECIPE")
        feasible = []
        shortages = []
        for recipe in recipes:
            output, ingredients, delta = recipe.get("output", {}), recipe.get("ingredients"), recipe.get("delta")
            if (type(recipe.get("requiresTable")) is not bool or type(recipe.get("operations")) is not int
                    or recipe["operations"] <= 0 or output.get("name") != self.data["item_name"]
                    or type(output.get("count")) is not int or not 1 <= output["count"] <= 64
                    or not isinstance(ingredients, list) or not isinstance(delta, list)):
                raise ExecutionError("INVALID_RECIPE_RESULT")
            missing = []
            for ingredient in ingredients:
                if any(type(ingredient.get(k)) is not int or ingredient[k] < 0 for k in ("count", "available", "missing")):
                    raise ExecutionError("INVALID_RECIPE_RESULT")
                if ingredient["missing"]:
                    missing.append({k: ingredient.get(k) for k in ("name", "type", "metadata", "count", "available", "missing")})
            # 修复/回收等配方可能同时消耗目标物品，不能把毛产量当成新增量。
            if any(type(d.get("count")) is not int for d in delta):
                raise ExecutionError("INVALID_RECIPE_RESULT")
            net = sum(d["count"] for d in delta if d.get("name") == self.data["item_name"])
            if net < self.p.count:
                continue
            if missing:
                shortages.append({"requiresTable": recipe["requiresTable"], "ingredients": missing})
            else:
                feasible.append(recipe)
        self.data["material_shortages"] = shortages[:4]
        if not feasible:
            self.progress("materials_missing" if shortages else "unsupported_recipe")
            raise ExecutionError("MATERIALS_MISSING" if shortages else "UNSUPPORTED_RECIPE")
        # 无台配方优先，避免仅因背包合成就移动或打开窗口。
        feasible.sort(key=lambda r: r["requiresTable"])
        selected = feasible[0]
        self.data["recipe"] = selected
        self.data["material_shortages"] = []
        self.runtime.report_progress(self.data)
        return selected

    async def inspect_table(self, target: dict) -> bool:
        own, _ = player(await self.world())
        center = {a: target[a] + 0.5 for a in ("x", "y", "z")}
        eye = {**own, "y": own["y"] + 1.5}
        check = {"position": target, "bot_position": own, "interaction_distance": distance(eye, center),
                 "maximum_distance": 4.5, "actual_name": None, "visible": None, "reason": "OUT_OF_RANGE"}
        self.data["workbench_check"] = check
        self.runtime.report_progress(self.data)
        if check["interaction_distance"] > 4.5:
            return False
        try:
            block = await self.call("inspect_block", {"position": target})
        except ExecutionError as error:
            if error.uncertain or error.code not in {"OUT_OF_RANGE", "CHUNK_UNAVAILABLE"}:
                raise
            check["reason"] = error.code
            self.runtime.report_progress(self.data)
            return False
        check.update(actual_name=block.get("name"), visible=block.get("visible"))
        ready = block.get("name") == "crafting_table" and block.get("visible") is True
        check["reason"] = "READY" if ready else "BLOCK_CHANGED" if block.get("name") != "crafting_table" else "NO_LINE_OF_SIGHT"
        self.runtime.report_progress(self.data)
        return ready

    async def approach(self, target: dict) -> bool:
        if await self.inspect_table(target):
            return True
        self.progress("approaching_workbench")
        try:
            result = await self.call("find_interaction_positions", {"position": target, "radius": 3, "max_results": 6})
        except ExecutionError as error:
            if error.uncertain or error.code not in {"OUT_OF_RANGE", "CHUNK_UNAVAILABLE"}:
                raise
            return False
        own, _ = player(await self.world())
        points = [position(entry.get("position")) for entry in result.get("candidates", [])]
        points.sort(key=lambda p: distance(own, p))
        for point in points[:3]:
            goal = {a: math.floor(point[a]) for a in ("x", "y", "z")}
            # 站位允许在搜索球边缘多 3 格，仍然有界；桌子本身必须位于搜索范围。
            if distance(goal, self.data["origin"]) > self.p.search_radius + 3:
                continue
            try:
                # 候选只是一个可交互脚部方块；默认 GoalNear(2) 会提前停下，
                # 将站位误差叠加到交互距离上，因此这里要求进入该方块。
                await self.call("goto_position", {**goal, "wait_seconds": 0, "arrival_range": 0})
            except ExecutionError as error:
                if error.uncertain or error.code not in {"NAVIGATION_FAILED", "MOVEMENT_REJECTED", "STEP_FAILED"}:
                    raise
                continue
            if await self.inspect_table(target):
                return True
        return False

    async def workbench(self) -> dict:
        self.progress("searching_workbench")
        if self.p.table_position is not None:
            targets = [self.p.table_position.model_dump()]
        else:
            found = await self.call("find_block", {"block_name": "crafting_table", "radius": self.p.search_radius, "max_results": 8})
            self.data["tables_truncated"] = bool(found.get("truncated"))
            targets = [position(entry.get("position")) for entry in found.get("blocks", [])
                       if entry.get("name") == "crafting_table"]
        targets = [p for p in targets if distance(self.data["origin"], p) <= self.p.search_radius]
        targets.sort(key=lambda p: distance(self.data["origin"], p))
        if not targets:
            raise ExecutionError("WORKBENCH_NOT_FOUND")
        seen = set()
        for target in targets[:8]:
            key = tuple(target[a] for a in ("x", "y", "z"))
            if key in seen:
                continue
            seen.add(key)
            entry = {"position": target, "accessible": False}
            self.data["table_attempts"].append(entry)
            if await self.approach(target):
                entry["accessible"] = True
                self.data["table_position"] = target
                self.runtime.report_progress(self.data)
                return target
        raise self.workbench_failure()

    async def run(self) -> dict:
        world = await self.world()
        self.data["origin"], self.data["dimension"] = player(world)
        self.progress("resolving")
        resolved = await self.call("resolve_resource", {"query": self.p.item_name.strip(), "kind": "item"})
        candidates = resolved.get("candidates") or []
        if not candidates or resolved.get("ambiguous") is not False:
            self.data["candidates"] = candidates[:8]
            self.progress("clarification_required")
            raise ExecutionError("RESOURCE_AMBIGUOUS" if candidates else "RESOURCE_UNKNOWN")
        if candidates[0].get("kind") != "item" or not isinstance(candidates[0].get("name"), str):
            raise ExecutionError("INVALID_RESOURCE_RESULT")
        self.data["item_name"] = candidates[0]["name"]
        self.data["initial_count"] = inventory_counts(world).get(self.data["item_name"], 0)
        self.data["current_count"] = self.data["initial_count"]
        recipe = await self.recipe()
        table = await self.workbench() if recipe["requiresTable"] else None
        # 移动后重新读材料/配方/工作台，避免按早先的可用状态提交修改。
        await self.world()
        recipe = await self.recipe()
        if recipe["requiresTable"] and (table is None or not await self.inspect_table(table)):
            raise self.workbench_failure()
        self.data["craft_submitted"] = True
        self.progress("crafting")
        args = {"item_name": self.data["item_name"], "count": self.p.count}
        if recipe["requiresTable"]:
            args["position"] = table
        # 一次性提交，底层 uncertain/超时不重新合成，避免重复消耗材料。
        self.data["tool_result"] = await self.call("craft_item", args)
        after = await self.world()
        self.data["current_count"] = inventory_counts(after).get(self.data["item_name"], 0)
        self.data["actual_increase"] = self.data["current_count"] - self.data["initial_count"]
        self.progress("verifying")
        return self.data
