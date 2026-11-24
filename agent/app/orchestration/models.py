"""Strict models for task planning, plan revision and explicit finish."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class PlanStep(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    success_criteria: str = Field(min_length=1, max_length=300)
    status: Literal["pending", "in_progress", "done", "blocked"] = "pending"


class PlanDecision(StrictModel):
    mode: Literal["direct", "plan"]
    goal: str | None = Field(default=None, max_length=300)
    steps: list[PlanStep] = Field(default_factory=list, max_length=8)
    reason: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_shape(self):
        if self.mode == "direct":
            if self.steps:
                raise ValueError("direct mode must not contain steps")
        else:
            if not self.goal or not self.goal.strip():
                raise ValueError("plan mode requires a goal")
            if not 1 <= len(self.steps) <= 8:
                raise ValueError("plan mode requires 1-8 steps")
        return self


class UpdatePlanArgs(StrictModel):
    reason: str | None = Field(default=None, max_length=300)
    goal: str | None = Field(default=None, max_length=300)
    steps: list[PlanStep] = Field(min_length=1, max_length=8)


class FinishArgs(StrictModel):
    status: Literal["completed", "partial", "blocked"]
    summary: str = Field(min_length=1, max_length=300)
    remaining: str | None = Field(default=None, max_length=300)
