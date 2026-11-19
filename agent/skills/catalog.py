from .explore_and_find import ExploreAndFind
from .gather_resource import GatherResource
from .registry import SkillRegistry


def create_skill_registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(ExploreAndFind())
    registry.register(GatherResource())
    return registry
