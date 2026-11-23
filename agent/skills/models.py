import json
from typing import Any, Literal

from pydantic import Field, model_validator

from app.execution.models import ExecutionRequest, StrictModel


class SkillParameters(StrictModel):
    """所有 Skill 参数都严格拒绝额外字段、字符串数字和非有限数值。"""


class SkillRequest(ExecutionRequest):
    timeout_seconds: float = Field(default=300, gt=0, le=1800)
    skill: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    arguments: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def smelting_budget(cls, value):
        # 保留熔炼 Skill 的历史多炉操作预算；投料完成就返回，不用它等待加工。
        if isinstance(value, dict) and value.get("skill") == "smelt_item":
            value = {"timeout_seconds": 1200, "max_tool_calls": 192, **value}
        return value

    @model_validator(mode="after")
    def other_skills_keep_timeout_limit(self):
        if self.skill != "smelt_item" and self.timeout_seconds > 600:
            raise ValueError("skill timeout exceeds 600 seconds")
        return self


class SkillCheck(StrictModel):
    criterion: str = Field(min_length=1, max_length=128)
    passed: bool
    observed: Any = None


class SkillVerification(StrictModel):
    checks: list[SkillCheck] = Field(min_length=1, max_length=16)


class SkillResult(StrictModel):
    execution_id: str
    player: str
    request_id: str
    skill: str
    success: bool
    status: Literal["pending", "running", "completed", "failed", "uncertain", "cancelled"]
    data: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    verification: dict[str, Any] | None = None
    steps: list[dict[str, Any]]
    calls: int
    created_at: float
    updated_at: float

    @model_validator(mode="after")
    def coherent_success(self):
        if self.success != (self.status == "completed") or (self.success and self.error is not None):
            raise ValueError("inconsistent skill result")
        json.dumps(self.model_dump(), allow_nan=False)
        return self
