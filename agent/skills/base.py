from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from .models import SkillParameters, SkillVerification

if TYPE_CHECKING:
    from .runtime import SkillRuntime


class Skill(ABC):
    """只由开发者注册 Python 实现；绝不接受模型生成的程序。"""

    name: str
    description: str
    parameters: type[SkillParameters]
    preconditions: tuple[str, ...]
    success_criteria: tuple[str, ...]
    required_tools: tuple[str, ...] = ()

    async def check_preconditions(self, runtime: "SkillRuntime", parameters: SkillParameters, world: dict) -> None:
        """子类在调用修改工具前检查材料、设施等；公共就绪检查由管理器执行。"""

    @abstractmethod
    async def execute(self, runtime: "SkillRuntime", parameters: SkillParameters) -> dict[str, Any]:
        """确定性的工具组合，返回执行数据，不直接宣布成功。"""

    @abstractmethod
    def verify(self, parameters: SkillParameters, data: dict, before: dict, after: dict) -> SkillVerification:
        """使用真实前后快照独立检查最终目标，不能只信工具的成功文本。"""
