import copy
import logging
import re

from pydantic import ValidationError

from app import diagnostics
from app.llm_client import LLMError

from .base import Skill
from .models import SkillParameters


logger = logging.getLogger("minemate.skills")


class SkillRegistry:
    """与 Node Tool Registry 分离；Skill Schema 从各参数模型生成。"""

    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        if not isinstance(skill, Skill) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", getattr(skill, "name", "")):
            raise ValueError("invalid skill")
        if skill.name in self._skills:
            raise ValueError("duplicate skill")
        if not isinstance(skill.parameters, type) or not issubclass(skill.parameters, SkillParameters):
            raise ValueError("invalid parameters model")
        if (skill.parameters.model_config.get("extra") != "forbid"
                or skill.parameters.model_config.get("strict") is not True
                or skill.parameters.model_config.get("allow_inf_nan") is not False):
            raise ValueError("skill parameters must be strict")
        if not skill.description or not skill.preconditions or not skill.success_criteria:
            raise ValueError("missing skill contract")
        if not all(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) for name in skill.required_tools):
            raise ValueError("invalid required tool")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill:
        if name not in self._skills:
            raise LLMError(404, "UNKNOWN_SKILL", "Skill 尚未注册。")
        return self._skills[name]

    def validate(self, name: str, arguments: dict) -> SkillParameters:
        skill = self.get(name)
        try:
            return skill.parameters.model_validate(copy.deepcopy(arguments))
        except ValidationError as error:
            logger.warning("[Skill] 参数无效 skill=%s fields=%s",
                           name, diagnostics.safe_validation_error(error, skill.parameters.model_fields))
            raise LLMError(422, "INVALID_SKILL_ARGUMENT", "Skill 参数不符合定义。") from None

    def list(self) -> list[dict]:
        return [{"name": skill.name, "description": skill.description,
                 "parameters": skill.parameters.model_json_schema(),
                 "preconditions": list(skill.preconditions), "success_criteria": list(skill.success_criteria),
                 "required_tools": list(skill.required_tools)} for skill in self._skills.values()]
