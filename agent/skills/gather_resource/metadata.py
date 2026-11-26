"""采集技能的元数据及严格参数；不在 Python 维护静态掉落表。"""

from pydantic import Field

from ..models import SkillParameters

NAME = "gather_resource"
DESCRIPTION = "采集指定物品直到背包目标总数达标；查询真实采集来源、有限搜索、受控清障、接近、挖掘、拾取并验证数量。"
PRECONDITIONS = ("bot_ready", "inventory_available", "runtime_resource_resolved", "mining_source_available")
SUCCESS_CRITERIA = ("inventory_target_reached", "same_dimension")
REQUIRED_TOOLS = ("resolve_resource", "inspect_mining_sources", "find_block", "find_interaction_positions",
                  "goto_position", "get_movement_task", "inspect_block", "clear_path", "mine_block",
                  "collect_item", "nearby_entities")

Query = Field(min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f]+$")


class GatherParameters(SkillParameters):
    resource: str = Query
    count: int = Field(ge=1, le=256, description="背包中的目标总数，先扣除已有数量；不是额外采集数量。")
    block_name: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f]+$")
    tool_name: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f]+$")
    search_radius: int = Field(default=16, ge=1, le=16)
    max_blocks: int = Field(default=16, ge=1, le=64, description="最多尝试的不同方块数量，包括被跳过或失败的候选。")
    allow_clearing: bool = Field(default=True, description="目标或掉落物不可达时，是否允许有限清除路径上的天然方块。")
    max_clear_blocks: int = Field(default=4, ge=1, le=8, description="本次 Skill 最多自动清除的路径方块数量。")
