"""熔炼 Skill 参数与运行时说明；不在 Python 复制熔炼/燃料表。"""
from pydantic import Field

from ..models import SkillParameters

NAME = "smelt_item"
DESCRIPTION = "查询真实配方，寻找并接近兼容普通熔炉，按容量投入原料和燃料，核验实际投放后结束；不等待加工或取出产品。"
PRECONDITIONS = ("bot_ready", "inventory_available", "recipe_available", "materials_available", "compatible_furnace_available")
SUCCESS_CRITERIA = ("input_deposited", "fuel_deposited", "furnace_load_confirmed", "same_dimension")
REQUIRED_TOOLS = ("resolve_resource", "inspect_smelting_recipe", "find_block", "inspect_block",
                  "find_interaction_positions", "goto_position", "get_movement_task", "open_furnace",
                  "furnace_add_input", "furnace_add_fuel")


class FurnacePosition(SkillParameters):
    x: int = Field(ge=-29999984, le=29999984)
    y: int = Field(ge=-2048, le=2047)
    z: int = Field(ge=-29999984, le=29999984)


class SmeltParameters(SkillParameters):
    item_name: str = Field(min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f]+$")
    count: int = Field(default=1, ge=1, le=64, description="本次新增投入的原料总数；优先填满当前兼容熔炉，剩余原料使用其它兼容炉。")
    fuel_name: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f]+$")
    search_radius: int = Field(default=16, ge=1, le=16)
    furnace_position: FurnacePosition | None = None
    use_existing: bool = Field(default=False, description="允许续接同工艺炉内已有原料/产品；不取出产品，输出满槽时跳过。")
    wait_timeout: float = Field(default=900, ge=1, le=1500, deprecated=True,
                                description="兼容旧请求的参数，不再生效；当前 Skill 投料后立即结束，不等待熔炼。")
