"""矿脉参数；资源掉落、工具和地形判定均取自 Node 实际工具。"""
from pydantic import Field

from ..models import SkillParameters
from ..gather_resource.metadata import GatherParameters

NAME = "mine_vein"
DESCRIPTION = "优先用于矿石/矿脉采集：从已发现的矿石出发，沿六面邻接的新暴露矿石采集，必要时有限挖通道，拾取并验证背包目标总数。不是区域挖空或全世界找矿，不搭块。"
PRECONDITIONS = ("bot_ready", "inventory_available", "runtime_mining_source", "observed_ore_seed")
SUCCESS_CRITERIA = ("inventory_target_reached", "same_dimension", "vein_connected")
REQUIRED_TOOLS = ("resolve_resource", "inspect_mining_sources", "find_block", "inspect_block", "inspect_vein",
                  "inspect_mining_route", "mine_route_block", "mine_vein_block", "find_interaction_positions",
                  "goto_position", "get_movement_task", "nearby_entities", "collect_item")


class SeedPosition(SkillParameters):
    x: int = Field(ge=-29999984, le=29999984)
    y: int = Field(ge=-2048, le=2047)
    z: int = Field(ge=-29999984, le=29999984)


class MineVeinParameters(GatherParameters):
    seed_position: SeedPosition | None = Field(default=None, description="可选，玩家明确指定的已暴露矿石坐标。")
    vein_radius: int = Field(default=8, ge=1, le=8, description="相对初始矿石的三维半径，不会扩展到无关矿脉。")
    max_blocks: int = Field(default=16, ge=1, le=32, description="最多尝试的矿石数量。")
    max_clear_blocks: int = Field(default=16, ge=0, le=32, description="整项 Skill 共用的通道清障预算；0 表示只走现有通道。")
    max_route_steps: int = Field(default=64, ge=1, le=128, description="整项任务中通道诊断/移动/清障的轮数上限。")
