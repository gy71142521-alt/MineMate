from .explore_and_find import ExploreAndFind
from .gather_resource import GatherResource
from .craft_item import CraftItem
from .smelt_item import SmeltItem
from .mine_vein import MineVein
from .registry import SkillRegistry


def create_skill_registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(ExploreAndFind())
    registry.register(GatherResource())
    registry.register(CraftItem())
    registry.register(SmeltItem())
    registry.register(MineVein())
    return registry
