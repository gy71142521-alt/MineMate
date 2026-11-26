"""单个矿脉的确定性执行，不在 Skill 内调用 LLM 或创建第二套任务队列。"""
import asyncio
import math

from app.execution.manager import ExecutionError, inventory_counts
from ..base import Skill
from ..explore_and_find import distance, player, position
from ..gather_resource.skill import _Gather, GatherResource
from ..models import SkillCheck, SkillVerification
from . import metadata


def key(point: dict) -> tuple:
    return tuple(point[a] for a in ("x", "y", "z"))


class MineVein(Skill):
    name = metadata.NAME
    description = metadata.DESCRIPTION
    parameters = metadata.MineVeinParameters
    preconditions = metadata.PRECONDITIONS
    success_criteria = metadata.SUCCESS_CRITERIA
    required_tools = metadata.REQUIRED_TOOLS

    async def execute(self, runtime, parameters) -> dict:
        return await _MineVein(runtime, parameters).run()

    def verify(self, parameters, data, before, after):
        checks = GatherResource().verify(parameters, data, before, after).checks
        # 除种子外，每个矿石必须连接一个更早已采集的矿石；证据不依赖模型文字。
        seen = set()
        connected = True
        for entry in data.get("mined", []):
            p = key(entry["position"])
            if not seen:
                connected &= data.get("seed") is not None and p == key(data["seed"]["position"])
            else:
                connected &= any(sum(abs(p[i] - q[i]) for i in range(3)) == 1 for q in seen)
            connected &= p not in seen and distance(data["seed"]["position"], entry["position"]) <= parameters.vein_radius
            seen.add(p)
        checks.append(SkillCheck(criterion="vein_connected", passed=bool(connected),
                                 observed={"mined": len(seen), "seed": data.get("seed")}))
        return SkillVerification(checks=checks)


class _MineVein(_Gather):
    """复用采集的资源解析、快照与库存核验；矿脉选择/开路/拾取独立实现。"""
    def __init__(self, runtime, parameters):
        super().__init__(runtime, parameters)
        self.data.update(seed=None, mined=[], bounds=None, route_steps=0, route_history=[],
                         completion_scope="inventory_target", vein_exhausted=False)
        self.frontier = []
        self.discovered = set()

    async def call(self, name: str, args: dict) -> dict:
        self.serial += 1
        return (await self.runtime.call(name, args, operation_id=f"vein_{self.serial}", wait_seconds=75))["data"]

    def remember(self, point: dict, name: str) -> None:
        if key(point) in self.discovered or distance(self.data["seed"]["position"], point) > self.p.vein_radius:
            return
        if len(self.discovered) >= 64:
            self.data["candidates_truncated"] = True
            return
        self.discovered.add(key(point))
        self.frontier.append({"name": name, "position": point})

    async def neighbors(self, point: dict) -> None:
        for source in self.data["sources"]:
            try:
                result = await self.call("inspect_vein", {"position": point, "block_name": source})
            except ExecutionError as error:
                if error.uncertain or error.code != "NOT_ORE":
                    raise
                continue
            for block in result.get("blocks", []):
                p = position(block.get("position"))
                if block.get("name") == source and sum(abs(p[a] - point[a]) for a in p) == 1:
                    self.remember(p, source)

    async def seed(self) -> None:
        self.progress("searching_vein")
        candidates = []
        for source in self.data["sources"]:
            if self.p.seed_position:
                points = [{"name": source, "position": self.p.seed_position.model_dump()}]
            else:
                result = await self.call("find_block", {"block_name": source, "radius": self.p.search_radius, "max_results": 16})
                self.data["candidates_truncated"] |= bool(result.get("truncated"))
                points = result.get("blocks", [])
            for block in points:
                p = position(block.get("position"))
                if distance(self.data["origin"], p) > self.p.search_radius:
                    continue
                candidates.append({"name": source, "position": p})
        candidates.sort(key=lambda e: distance(self.data["origin"], e["position"]))
        for candidate in candidates[:8]:
            p, source = candidate["position"], candidate["name"]
            try:
                result = await self.call("inspect_vein", {"position": p, "block_name": source})
            except ExecutionError as error:
                if error.uncertain or error.code != "NOT_ORE":
                    raise
                continue
            # 玩家明确给坐标时允许已暴露但暂时有遮挡的目标；自动选种子必须真实可见。
            if any(e.get("position") == p and e.get("name") == source and
                   (self.p.seed_position is not None or e.get("visible") is True) for e in result.get("blocks", [])):
                self.data["seed"] = candidate
                self.remember(p, source)
                return
        raise ExecutionError("ORE_SEED_NOT_FOUND")

    async def reach(self, point: dict, purpose: str, name: str | None = None,
                    entity_id: int | None = None) -> bool:
        while self.data["route_steps"] < self.p.max_route_steps:
            await self.snapshot()
            mine_ready = False
            if purpose == "mine":
                block = await self.inspect(point)
                if block and block.get("name") != name:
                    raise ExecutionError("BLOCK_CHANGED")
                # 实际可见面由 Node 通道诊断/单矿石工具重新射线核验；inspect_block
                # 的中心射线标记不能否定露出的一部分矿石表面。
                mine_ready = bool(block and block.get("diggable") is True
                                  and not await self.is_support(point))
            else:
                entities = (await self.call("nearby_entities", {})).get("items", [])
                entity = next((e for e in entities if e.get("id") == entity_id and e.get("itemName") == self.data["item_name"]), None)
                if entity is None:
                    return False
                point = position(entity.get("position"))
            target = {a: math.floor(point[a]) for a in point}
            if distance(target, self.data["origin"]) > self.p.search_radius + self.p.vein_radius:
                raise ExecutionError("MINING_AREA_LIMIT")
            args = {"position": target, "purpose": purpose, "radius": min(16, self.p.search_radius),
                    "max_clear_blocks": max(0, self.data["clear_budget"]) if self.p.allow_clearing else 0}
            if name:
                args["block_name"] = name
            if self.data["tool_name"] and purpose == "mine":
                args["tool_name"] = self.data["tool_name"]
            route = await self.call("inspect_mining_route", args)
            self.data["route_steps"] += 1
            if route.get("reachable") is not True:
                self.data["route_history"] = [*self.data["route_history"], {"reason": route.get("reason"), "target": target}][-8:]
                self.progress("route_blocked")
                return False
            step = route.get("next") or {}
            action = step.get("action")
            if action == "ready":
                return purpose == "pickup" or mine_ready
            if action == "dig":
                self.progress("excavating_tunnel")
                cleared = await self.call("mine_route_block", args)
                if cleared.get("blockRemoved") is not True:
                    raise ExecutionError("PATH_CLEAR_UNCONFIRMED", uncertain=True)
                self.data["clear_budget"] -= 1
                self.data["cleared_blocks"] += 1
            elif action == "move":
                self.progress("approaching_ore" if purpose == "mine" else "approaching_drop")
                goal = position(step.get("position"))
                await self.call("goto_position", {**goal, "wait_seconds": 0, "arrival_range": 0})
            else:
                raise ExecutionError("INVALID_MINING_ROUTE")
            self.data["route_history"] = [*self.data["route_history"], {"action": action, "target": target,
                                                                        "position": step.get("position")}][-8:]
            self.runtime.report_progress(self.data)
        raise ExecutionError("MINING_STEP_LIMIT")

    async def pickup(self, previous: int) -> None:
        self.progress("picking_up")
        last_position = None
        for _ in range(5):
            if await self.update_count() > previous:
                return
            world = await self.snapshot()
            own, _ = player(world)
            nearby = await self.call("nearby_entities", {})
            drops = [e for e in nearby.get("items", []) if e.get("itemName") == self.data["item_name"]
                     and e.get("valid") is not False and isinstance(e.get("id"), int)]
            if last_position:
                drops = [e for e in drops if distance(position(e.get("position")), last_position) <= 3]
            drops.sort(key=lambda e: distance(own, position(e.get("position"))))
            if drops:
                drop = drops[0]
                last_position = position(drop.get("position"))
                if await self.reach(last_position, "pickup", entity_id=drop["id"]):
                    if await self.update_count() > previous:
                        return
                    try:
                        await self.call("collect_item", {"item_name": self.data["item_name"], "entity_id": drop["id"], "radius": 8})
                    except ExecutionError as error:
                        if error.uncertain or error.code not in {"ITEM_ENTITY_MISSING", "OUT_OF_RANGE"}:
                            raise
                elif await self.update_count() <= previous:
                    # 实体合并/消失可能使原ID失效；先有界刷新，不能直接开挖下一块。
                    self.data["last_pickup_target"] = last_position
            await asyncio.sleep(0.25)
        if await self.update_count() <= previous:
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
        self.data["sources"] = [s["name"] for s in sources.get("sources", [])][:4]
        self.data["sources_truncated"] = bool(sources.get("truncated")) or len(sources.get("sources", [])) > 4
        if not self.data["sources"]:
            raise ExecutionError("MINING_SOURCE_UNKNOWN" if sources.get("available") is not True else "MINING_SOURCE_MISMATCH")
        if self.p.tool_name:
            self.data["tool_name"] = await self.resolve(self.p.tool_name, "item")
        await self.seed()
        for _ in range(self.p.max_blocks):
            if await self.update_count() >= self.p.count:
                self.progress("verifying")
                return self.data
            if not self.frontier:
                self.data["vein_exhausted"] = True
                break
            own, _ = player(await self.snapshot())
            self.frontier.sort(key=lambda e: distance(own, e["position"]))
            entry = self.frontier.pop(0)
            point, name = entry["position"], entry["name"]
            self.data["attempt_count"] += 1
            # 不通过不相邻的其它矿脉补齐数量；一个不可达点允许尝试矿脉其它前沿。
            if not await self.reach(point, "mine", name):
                continue
            previous = await self.update_count()
            if previous >= self.p.count:
                return self.data
            self.progress("mining_ore")
            mined = await self.call("mine_vein_block", {"position": point, "block_name": name,
                 **({"tool_name": self.data["tool_name"]} if self.data["tool_name"] else {})})
            if mined.get("blockRemoved") is not True:
                raise ExecutionError("MINING_NOT_CONFIRMED", uncertain=True)
            self.data["blocks_mined"] += 1
            self.data["mined"].append({"name": name, "position": point})
            coords = [m["position"] for m in self.data["mined"]]
            self.data["bounds"] = {"min": {a: min(p[a] for p in coords) for a in point},
                                   "max": {a: max(p[a] for p in coords) for a in point}}
            await self.pickup(previous)
            if self.data["current_count"] >= self.p.count:
                self.progress("verifying")
                return self.data
            await self.neighbors(point)
            self.progress("collected")
        if await self.update_count() < self.p.count:
            raise ExecutionError("VEIN_INSUFFICIENT")
        self.progress("verifying")
        return self.data
