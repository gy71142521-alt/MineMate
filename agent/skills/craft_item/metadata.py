"""合成技能的运行时元数据；配方和工具参数只从 Node 获取。"""

from pydantic import Field

from ..models import SkillParameters

NAME = "craft_item"
DESCRIPTION = "根据当前版本真实配方检查材料，必要时寻找并接近工作台，合成并核验背包新增产物；缺材料时报告缺口。"
PRECONDITIONS = ("bot_ready", "inventory_available", "recipe_available", "materials_available", "workbench_accessible_if_required")
SUCCESS_CRITERIA = ("inventory_increase_reached", "same_dimension")
REQUIRED_TOOLS = ("resolve_resource", "inspect_recipe", "find_block", "inspect_block",
                  "find_interaction_positions", "goto_position", "get_movement_task", "craft_item")


class TablePosition(SkillParameters):
    x: int = Field(ge=-29999984, le=29999984)
    y: int = Field(ge=-2048, le=2047)
    z: int = Field(ge=-29999984, le=29999984)


class CraftParameters(SkillParameters):
    item_name: str = Field(min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f]+$")
    count: int = Field(default=1, ge=1, le=16, description="本次期望新增的产物数；多产物配方按整批取整，不扣除已有产物。")
    search_radius: int = Field(default=16, ge=1, le=16)
    table_position: TablePosition | None = None
