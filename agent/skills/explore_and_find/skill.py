import math

from app.execution.manager import ExecutionError

from ..base import Skill
from ..models import SkillCheck, SkillVerification
from ..runtime import SkillRuntime
from . import metadata
from .metadata import ExploreParameters


def position(value: object) -> dict:
    if not isinstance(value, dict) or any(type(value.get(a)) not in (int, float) or not math.isfinite(value[a]) for a in ("x", "y", "z")):
        raise ExecutionError("INVALID_POSITION")
    return {a: value[a] for a in ("x", "y", "z")}


def distance(a: dict, b: dict) -> float:
    return math.dist([a[k] for k in ("x", "y", "z")], [b[k] for k in ("x", "y", "z")])


def player(world: dict) -> tuple[dict, str]:
    own = world.get("player", {})
    if world.get("availability", {}).get("player") != "available" or own.get("alive") is False:
        raise ExecutionError("BOT_NOT_READY")
    if not isinstance(own.get("dimension"), str):
        raise ExecutionError("DIMENSION_UNAVAILABLE")
    return position(own.get("position")), own["dimension"]


class ExploreAndFind(Skill):
    name = metadata.NAME
    description = metadata.DESCRIPTION
    parameters = ExploreParameters
    preconditions = metadata.PRECONDITIONS
    success_criteria = metadata.SUCCESS_CRITERIA
    required_tools = metadata.REQUIRED_TOOLS

    async def execute(self, runtime: SkillRuntime, parameters: ExploreParameters) -> dict:
        # 每次执行独立状态；注册的 Skill 对象不保存目标或路点，避免下次复用旧任务。
        return await _Search(runtime, parameters).run()

    def verify(self, parameters, data, before, after):
        final, dimension = player(after)
        selected = data.get("selected")
        observation = data.get("observation") or {}
        observed = bool(selected and observation.get("confirmed") is True
                        and observation.get("target_name") == data.get("target_name")
                        and observation.get("target") == selected)
        in_range = bool(selected and distance(data["origin"], selected["position"]) <= parameters.search_radius)
        near = bool(selected and distance(final, selected["position"]) <= parameters.approach_distance)
        return SkillVerification(checks=[
            SkillCheck(criterion="target_observed", passed=observed, observed=observation),
            SkillCheck(criterion="within_search_range", passed=in_range, observed=parameters.search_radius),
            SkillCheck(criterion="approach_verified", passed=not parameters.approach or near,
                       observed={"requested": parameters.approach, "distance": distance(final, selected["position"]) if selected else None}),
            SkillCheck(criterion="same_dimension", passed=dimension == data.get("dimension") == before.get("player", {}).get("dimension"), observed=dimension)
        ])


class _Search:
    def __init__(self, runtime: SkillRuntime, parameters: ExploreParameters):
        self.runtime, self.p = runtime, parameters
        self.serial = 0
        self.attempted: set[tuple] = set()
        self.data = {"stage": "resolving", "target_kind": parameters.target_kind, "query": parameters.target,
                     "target_name": None, "found": False, "selected": None, "results": [], "observation": None,
                     "origin": None, "dimension": None, "approach_requested": parameters.approach, "approached": False,
                     "results_truncated": False,
                     "search_radius": parameters.search_radius, "scan_radius": min(16, parameters.search_radius),
                     "scans": [], "waypoints": [], "attempts": [], "planned_travel": 0.0,
                     "coverage_complete": False}

    def progress(self, stage: str) -> None:
        self.data["stage"] = stage
        self.runtime.report_progress(self.data)

    async def call(self, name: str, args: dict) -> dict:
        self.serial += 1
        result = await self.runtime.call(name, args, operation_id=f"search_{self.serial}", wait_seconds=75)
        return result["data"]

    async def current(self) -> dict:
        own, dimension = player(await self.runtime.snapshot())
        if self.data["dimension"] is not None and dimension != self.data["dimension"]:
            raise ExecutionError("DIMENSION_CHANGED")
        return own

    async def find(self, *, record: bool = True) -> list[dict]:
        own = await self.current()
        key = "blocks" if self.p.target_kind == "block" else "entities"
        args = {"block_name" if key == "blocks" else "entity_type": self.data["target_name"],
                "radius": self.data["scan_radius"], "max_results": 16}
        response = await self.call("find_block" if key == "blocks" else "find_entity", args)
        entries = response.get(key)
        if not isinstance(entries, list):
            raise ExecutionError("INVALID_SEARCH_RESULT")
        found = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ExecutionError("INVALID_SEARCH_RESULT")
            point = position(entry.get("position"))
            if distance(own, point) > self.data["scan_radius"] or distance(self.data["origin"], point) > self.p.search_radius:
                continue
            if key == "entities" and (entry.get("valid") is not True or type(entry.get("id")) is not int):
                continue
            if key == "blocks" and entry.get("name") != self.data["target_name"]:
                continue
            found.append({"name": entry.get("name"), "id": entry.get("id") if key == "entities" else None,
                          "position": point, "distance": distance(own, point)})
        found.sort(key=lambda e: e["distance"])
        if record:
            self.data["scans"].append({"position": own, "radius": self.data["scan_radius"],
                                       "matches": len(found), "truncated": bool(response.get("truncated"))})
            self.data["results"] = found[:self.p.max_results]
            self.data["results_truncated"] = bool(response.get("truncated")) or len(found) > self.p.max_results
            self.progress("searching")
        return found

    async def move(self, point: dict) -> bool:
        own = await self.current()
        point = {a: math.floor(point[a]) for a in ("x", "y", "z")}
        leg = distance(own, point)
        if (distance(self.data["origin"], point) > self.p.search_radius
                or self.data["planned_travel"] + leg > 256):
            return False
        self.data["planned_travel"] += leg
        self.progress("moving")
        try:
            await self.call("goto_position", {**point, "wait_seconds": 0})
            return True
        except ExecutionError as error:
            # 只跳过已确认失败；超时/失联/取消/未知结果必须立即结束，不能换 ID 重发。
            if error.uncertain or error.code not in {"NAVIGATION_FAILED", "MOVEMENT_REJECTED", "STEP_FAILED"}:
                raise
            self.data["attempts"].append({"position": point, "error": error.code})
            self.progress("navigation_failed")
            return False

    @staticmethod
    def identity(target: dict) -> tuple:
        return (target["id"],) if target["id"] is not None else tuple(target["position"][k] for k in ("x", "y", "z"))

    async def confirm(self, target: dict) -> dict | None:
        # 重新查询同一方块坐标/实体 ID，不能拿到另一个目标就宣布原目标已到达。
        return next((item for item in await self.find(record=False) if self.identity(item) == self.identity(target)), None)

    async def inspect_visible(self, target: dict) -> bool:
        try:
            block = await self.call("inspect_block", {"position": target["position"]})
            return block.get("name") == self.data["target_name"] and block.get("visible") is True
        except ExecutionError as error:
            if error.uncertain or error.code not in {"OUT_OF_RANGE", "CHUNK_UNAVAILABLE"}:
                raise
            return False

    async def approach(self, target: dict) -> dict | None:
        own = await self.current()
        if distance(own, target["position"]) <= self.p.approach_distance:
            if self.p.target_kind == "entity" or await self.inspect_visible(target):
                current = await self.confirm(target)
                if current is None:
                    return None
                if distance(await self.current(), current["position"]) <= self.p.approach_distance:
                    return current
                target = current
        if self.p.target_kind == "block":
            try:
                response = await self.call("find_interaction_positions", {"position": target["position"], "radius": 3, "max_results": 6})
            except ExecutionError as error:
                if error.uncertain or error.code not in {"OUT_OF_RANGE", "CHUNK_UNAVAILABLE"}:
                    raise
                return None
            points = [position(entry["position"]) for entry in response.get("candidates", [])]
            points.sort(key=lambda point: (distance(point, target["position"]), distance(point, own)))
            points = points[:3]
        else:
            # 实体只做有限次接近，不创建持续跟随任务；飞行或隔墙目标允许明确失败。
            points = [target["position"]] * 2
        for point in points:
            if self.p.target_kind == "entity":
                target = await self.confirm(target)
                if target is None:
                    return None
                point = target["position"]
            if not await self.move(point):
                continue
            current = await self.confirm(target)
            if current is None:
                return None
            own = await self.current()
            if distance(own, current["position"]) > self.p.approach_distance:
                continue
            if self.p.target_kind == "block" and not await self.inspect_visible(current):
                continue
            return current
        return None

    def waypoints(self) -> list[dict]:
        # 两圈有限水平路点；不是全范围穷举，也不把未加载区块当作空气。
        origin = self.data["origin"]
        result, visited = [], set()
        for radius in (self.p.search_radius / 2, max(1, self.p.search_radius - 1)):
            for dx, dz in ((1, 0), (0, 1), (-1, 0), (0, -1)):
                point = {"x": math.floor(origin["x"] + dx * radius), "y": math.floor(origin["y"]),
                         "z": math.floor(origin["z"] + dz * radius)}
                key = tuple(point.values())
                if key not in visited and distance(origin, point) <= self.p.search_radius:
                    visited.add(key)
                    result.append(point)
        return result[:self.p.max_waypoints]

    async def run(self) -> dict:
        world = await self.runtime.snapshot()
        self.data["origin"], self.data["dimension"] = player(world)
        self.progress("resolving")
        resolved = await self.call("resolve_resource", {"query": self.p.target.strip(), "kind": self.p.target_kind})
        candidates = resolved.get("candidates") or []
        if not candidates or resolved.get("ambiguous") is not False:
            self.data["candidates"] = candidates[:8]
            self.progress("clarification_required")
            raise ExecutionError("TARGET_AMBIGUOUS" if candidates else "TARGET_UNKNOWN")
        chosen = candidates[0]
        if chosen.get("kind") != self.p.target_kind or not isinstance(chosen.get("name"), str):
            raise ExecutionError("INVALID_SEARCH_RESULT")
        self.data["target_name"] = chosen["name"]
        route = self.waypoints() if self.p.explore else []
        for index in range(len(route) + 1):
            if index:
                waypoint = {"position": route[index - 1], "status": "running"}
                self.data["waypoints"].append(waypoint)
                self.progress("exploring")
                reached = await self.move(waypoint["position"])
                waypoint["status"] = "arrived" if reached else "failed"
                # 即使导航失败也可能已移动一段距离，在实际停留位置查询。
            found = await self.find()
            for target in found:
                key = self.identity(target)
                if key in self.attempted or len(self.attempted) >= 6:
                    continue
                self.attempted.add(key)
                # 前一候选的接近过程可能已改变自身位置，不能继续使用旧扫描位置。
                current = await self.confirm(target)
                if current is not None and self.p.approach:
                    current = await self.approach(current)
                if current is None:
                    self.data["attempts"].append({"target": target, "error": "TARGET_UNREACHABLE_OR_LOST"})
                    self.progress("target_skipped")
                    continue
                self.data.update(found=True, selected=current, approached=self.p.approach,
                                 observation={"confirmed": True, "target_name": self.data["target_name"], "target": current})
                self.progress("verifying")
                return self.data
            if len(self.attempted) >= 6:
                break
        self.progress("not_found")
        raise ExecutionError("TARGET_UNREACHABLE_OR_LOST" if self.attempted else "TARGET_NOT_FOUND")
