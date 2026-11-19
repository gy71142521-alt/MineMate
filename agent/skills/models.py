import json
from typing import Any, Literal

from pydantic import Field, model_validator

from app.execution.models import ExecutionRequest, StrictModel


class SkillParameters(StrictModel):
    """所有 Skill 参数都严格拒绝额外字段、字符串数字和非有限数值。"""


class SkillRequest(ExecutionRequest):
    skill: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    arguments: dict[str, Any] = Field(default_factory=dict)


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
