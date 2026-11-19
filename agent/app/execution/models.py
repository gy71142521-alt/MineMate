import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Step(StrictModel):
    step_id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,31}$")
    tool: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    arguments: dict[str, Any] = Field(default_factory=dict)
    wait_seconds: float = Field(default=90, gt=0, le=180)


class InventoryCheck(StrictModel):
    item: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    count: int = Field(ge=1, le=4096)
    mode: Literal["at_least", "increase"] = "increase"


class TaskRequest(StrictModel):
    player: str = Field(pattern=r"^[A-Za-z0-9_]{3,16}$")
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    steps: list[Step] = Field(min_length=1, max_length=24)
    verify_inventory: list[InventoryCheck] = Field(default_factory=list, max_length=16)
    timeout_seconds: float = Field(default=300, gt=0, le=600)
    max_tool_calls: int = Field(default=96, ge=1, le=192)

    @model_validator(mode="after")
    def unique_and_bounded(self):
        if len({step.step_id for step in self.steps}) != len(self.steps):
            raise ValueError("duplicate step")
        if len(json.dumps(self.model_dump(), allow_nan=False).encode()) > 16384:
            raise ValueError("task too large")
        return self
