"""技能元数据与参数的唯一代码来源，由 Skill Registry 生成对外定义。"""

from typing import Literal

from pydantic import Field

from ..models import SkillParameters


NAME = "explore_and_find"
DESCRIPTION = "有界搜索已加载的方块或实体类型，可选择探索路点和接近目标；不挖掘、不攻击、不跟随。"
PRECONDITIONS = ("bot_ready", "dimension_available", "runtime_target_resolved")
SUCCESS_CRITERIA = ("target_observed", "within_search_range", "approach_verified", "same_dimension")
REQUIRED_TOOLS = ("resolve_resource", "find_block", "find_entity", "inspect_block",
                  "find_interaction_positions", "goto_position", "get_movement_task")


class ExploreParameters(SkillParameters):
    target_kind: Literal["block", "entity"]
    target: str = Field(min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f]+$")
    search_radius: int = Field(default=16, ge=1, le=64)
    explore: bool = False
    approach: bool = False
    approach_distance: float = Field(default=3.5, ge=3, le=4.5)
    max_waypoints: int = Field(default=4, ge=1, le=8)
    max_results: int = Field(default=4, ge=1, le=8)
