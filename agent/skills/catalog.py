from .explore_and_find import ExploreAndFind
from .registry import SkillRegistry


def create_skill_registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(ExploreAndFind())
    return registry
