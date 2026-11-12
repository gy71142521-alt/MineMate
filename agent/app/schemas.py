import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


USERNAME_PATTERN = r"^[A-Za-z0-9_]{3,16}$"


class MovementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=16, pattern=USERNAME_PATTERN)


class GotoRequest(MovementRequest):
    x: float
    y: float
    z: float

    @field_validator("x", "y", "z", mode="before")
    @classmethod
    def finite_number(cls, value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("坐标必须是有限数字。")
        return float(value)
