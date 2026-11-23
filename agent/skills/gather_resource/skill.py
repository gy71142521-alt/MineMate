import asyncio
import math

from app.execution.manager import ExecutionError, inventory_counts

from ..base import Skill
from ..explore_and_find import distance, player, position
from ..models import SkillCheck, SkillVerification
from ..runtime import SkillRuntime
from . import metadata
from .metadata import GatherParameters


class GatherResource(Skill):
    name = metadata.NAME
    description = metadata.DESCRIPTION
    parameters = GatherParameters
    preconditions = metadata.PRECONDITIONS
    success_criteria = metadata.SUCCESS_CRITERIA
    required_tools = metadata.REQUIRED_TOOLS

    async def execute(self, runtime: SkillRuntime, parameters: GatherParameters) -> dict:
        return await _Gather(runtime, parameters).run()

    def verify(self, parameters, data, before, after):
        _, dimension = player(after)
        count = inventory_counts(after).get(data["item_name"], 0)
        return SkillVerification(checks=[
            SkillCheck(criterion="inventory_target_reached", passed=count >= parameters.count,
                       observed={"item": data["item_name"], "count": count, "required_total": parameters.count}),
            SkillCheck(criterion="same_dimension", passed=dimension == data["dimension"] == before.get("player", {}).get("dimension"),
                       observed=dimension)
        ])


class _Gather:
    def __init__(self, runtime: SkillRuntime, parameters: GatherParameters):
        self.runtime, self.p = runtime, parameters
        self.serial = 0
        self.attempted: set[tuple] = set()
        self.data = {"stage": "resolving", "query": parameters.resource, "item_name": None,
                     "count_mode": "total", "requested_total": parameters.count, "initial_count": None,
                     "current_count": None, "collected": 0, "remaining": parameters.count,
                     "origin": None, "search_center": None, "dimension": None, "sources": [], "sources_truncated": False,
                     "scans": 0, "search_radius": parameters.search_radius, "coverage_complete": False,
                     "candidates_truncated": False,
                     "attempt_count": 0, "blocks_mined": 0, "attempts": [], "tool_name": None}

    def progress(self, stage: str) -> None:
        self.data["stage"] = stage
        self.runtime.report_progress(self.data)

    async def call(self, name: str, args: dict) -> dict:
        self.serial += 1
        result = await self.runtime.call(name, args, operation_id=f"gather_{self.serial}", wait_seconds=75)
        return result["data"]

    async def snapshot(self) -> dict:
        world = await self.runtime.snapshot()
        _, dimension = player(world)
        if self.data["dimension"] is not None and dimension != self.data["dimension"]:
            raise ExecutionError("DIMENSION_CHANGED")
        inventory_counts(world)  # 不把不可用背包当作空背包。
        return world

    async def update_count(self) -> int:
        count = inventory_counts(await self.snapshot()).get(self.data["item_name"], 0)
        self.data.update(current_count=count, remaining=max(0, self.p.count - count),
                         collected=max(0, count - self.data["initial_count"]))
        return count

    async def resolve(self, query: str, kind: str) -> str:
        result = await self.call("resolve_resource", {"query": query.strip(), "kind": kind})
        candidates = result.get("candidates") or []
        if not candidates or result.get("ambiguous") is not False:
            self.data["candidates"] = candidates[:8]
            self.progress("clarification_required")
            raise ExecutionError("RESOURCE_AMBIGUOUS" if candidates else "RESOURCE_UNKNOWN")
        if candidates[0].get("kind") != kind or not isinstance(candidates[0].get("name"), str):
            raise ExecutionError("INVALID_RESOURCE_RESULT")
        return candidates[0]["name"]

    async def find(self) -> list[dict]:
        own, _ = player(await self.snapshot())
        # 采集成功并移动到新位置后，下一轮以当前位置重新划定有限搜索范围；
        # origin 仅用于记录任务起点，不能把大批量采集锁死在起点附近。
        self.data["search_center"] = own
        candidates = []
        for source in self.data["sources"]:
            result = await self.call("find_block", {"block_name": source, "radius": self.p.search_radius, "max_results": 16})
            self.data["scans"] += 1
            self.data["candidates_truncated"] = self.data["candidates_truncated"] or bool(result.get("truncated"))
            for entry in result.get("blocks", []):
                point = position(entry.get("position"))
                key = tuple(point[a] for a in ("x", "y", "z"))
                if entry.get("name") != source or key in self.attempted:
                    continue
                if distance(self.data["search_center"], point) <= self.p.search_radius and distance(own, point) <= self.p.search_radius:
                    candidates.append({"name": source, "position": point})
        candidates.sort(key=lambda item: distance(own, item["position"]))
        self.progress("searching")
        return candidates

    async def is_support(self, target: dict) -> bool:
        own, _ = player(await self.snapshot())
        return (math.floor(own["x"]) == target["x"] and math.floor(own["z"]) == target["z"]
                and math.floor(own["y"]) - 1 == target["y"])

    async def inspect(self, target: dict) -> dict | None:
        try:
            return await self.call("inspect_block", {"position": target})
        except ExecutionError as error:
            if error.uncertain or error.code not in {"OUT_OF_RANGE", "CHUNK_UNAVAILABLE"}:
                raise
            return None

    async def approach(self, target: dict, name: str) -> dict | None:
        own, _ = player(await self.snapshot())
        if distance(own, target) <= 4.5:
            block = await self.inspect(target)
            if block and block.get("name") != name:
                return None
            if block and block.get("visible") is True and block.get("diggable") is True:
                return block
        self.progress("approaching")
        try:
            result = await self.call("find_interaction_positions", {"position": target, "radius": 3, "max_results": 6})
        except ExecutionError as error:
            if error.uncertain or error.code not in {"OUT_OF_RANGE", "CHUNK_UNAVAILABLE"}:
                raise
            return None
        points = [position(item["position"]) for item in result.get("candidates", [])]
        points.sort(key=lambda p: (distance(p, target), distance(p, own)))
        for point in points[:3]:
            goal = {a: math.floor(point[a]) for a in ("x", "y", "z")}
            if distance(goal, self.data["search_center"]) > self.p.search_radius:
                continue
            try:
                await self.call("goto_position", {**goal, "wait_seconds": 0})
            except ExecutionError as error:
                if error.uncertain or error.code not in {"NAVIGATION_FAILED", "MOVEMENT_REJECTED", "STEP_FAILED"}:
                    raise
                continue
            # GoalNear 到达不保证可以挖；重新检查实际方块、视线和脚下支撑。
            if await self.is_support(target):
                continue
            block = await self.inspect(target)
            if block and block.get("name") != name:
                return None
            if block and block.get("visible") is True and block.get("diggable") is True:
                return block
        return None

    async def pickup(self, previous: int) -> None:
        self.progress("picking_up")
        # 掉落实体可能晚于方块更新到达；只有“无实体”可短暂重查，未知执行结果不重试。
        for attempt in range(5):
            if await self.update_count() > previous:
                return
            try:
                await self.call("collect_item", {"item_name": self.data["item_name"], "radius": 8})
            except ExecutionError as error:
                if error.uncertain or error.code != "ITEM_ENTITY_MISSING":
                    raise
                if attempt < 4:
                    await asyncio.sleep(0.25)
                continue
            if await self.update_count() > previous:
                return
            break
        # 最后再读一次，覆盖无实体错误与被动拾取同时到达的情况。
        if await self.update_count() <= previous:
            self.progress("pickup_unconfirmed")
            raise ExecutionError("PICKUP_NOT_CONFIRMED", uncertain=True)

    async def run(self) -> dict:
        world = await self.snapshot()
        self.data["origin"], self.data["dimension"] = player(world)
        self.data["search_center"] = self.data["origin"]
        self.progress("resolving")
        self.data["item_name"] = await self.resolve(self.p.resource, "item")
        self.data["initial_count"] = inventory_counts(world).get(self.data["item_name"], 0)
        if await self.update_count() >= self.p.count:
            self.progress("verifying")
            return self.data
        args = {"item_name": self.data["item_name"], "max_results": 16}
        if self.p.block_name:
            args["block_name"] = await self.resolve(self.p.block_name, "block")
        sources = await self.call("inspect_mining_sources", args)
        self.data["sources"] = [source["name"] for source in sources.get("sources", [])][:4]
        self.data["sources_truncated"] = bool(sources.get("truncated")) or len(sources.get("sources", [])) > 4
        if not self.data["sources"]:
            self.progress("source_unavailable")
            raise ExecutionError("MINING_SOURCE_UNKNOWN" if sources.get("available") is not True else "MINING_SOURCE_MISMATCH")
        if self.p.tool_name:
            self.data["tool_name"] = await self.resolve(self.p.tool_name, "item")
        for _ in range(self.p.max_blocks):
            if await self.update_count() >= self.p.count:
                self.progress("verifying")
                return self.data
            targets = await self.find()
            if not targets:
                break
            target = targets[0]
            point, name = target["position"], target["name"]
            self.attempted.add(tuple(point[a] for a in ("x", "y", "z")))
            entry = {"position": point, "block_name": name, "status": "running", "error": None}
            self.data["attempt_count"] += 1
            self.data["attempts"] = [*self.data["attempts"], entry][-16:]
            if await self.is_support(point):
                entry.update(status="skipped", error="UNSAFE_TARGET")
                self.progress("target_skipped")
                continue
            if not await self.approach(point, name) or await self.is_support(point):
                entry.update(status="skipped", error="TARGET_UNREACHABLE_OR_CHANGED")
                self.progress("target_skipped")
                continue
            previous = await self.update_count()
            if previous >= self.p.count:
                self.progress("verifying")
                return self.data
            self.progress("mining")
            try:
                result = await self.call("mine_block", {"block_name": name, "position": point,
                    **({"tool_name": self.data["tool_name"]} if self.data["tool_name"] else {})})
            except ExecutionError as error:
                if error.uncertain or error.code not in {"UNSAFE_TARGET", "NO_LINE_OF_SIGHT", "OUT_OF_RANGE", "BLOCK_CHANGED", "NOT_DIGGABLE", "CHUNK_UNAVAILABLE", "PROTECTED_BLOCK"}:
                    raise
                entry.update(status="skipped", error=error.code)
                self.progress("target_skipped")
                continue
            if result.get("blockRemoved") is not True:
                raise ExecutionError("MINING_NOT_CONFIRMED", uncertain=True)
            self.data["blocks_mined"] += 1
            entry["status"] = "mined"
            await self.pickup(previous)
            entry.update(status="collected", inventory_after=self.data["current_count"])
            self.progress("collected")
        if await self.update_count() < self.p.count:
            self.progress("insufficient")
            raise ExecutionError("RESOURCE_INSUFFICIENT" if self.attempted else "RESOURCE_NOT_FOUND")
        self.progress("verifying")
        return self.data
