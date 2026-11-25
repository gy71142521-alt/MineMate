"""Planner adapter: decide direct execution vs an explicit plan."""

from __future__ import annotations

import json
import logging
from typing import Any

from ..llm_client import LLMClient, LLMError
from .models import PlanDecision
from .prompts import PLANNER_PROMPT

logger = logging.getLogger("minemate.orchestration.planner")


class Planner:
    def __init__(self, llm: LLMClient, *, enabled: bool = True, max_attempts: int = 2):
        self.llm = llm
        self.enabled = enabled
        self.max_attempts = max(1, min(3, max_attempts))

    async def decide(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> PlanDecision:
        if not self.enabled:
            return PlanDecision(mode="direct", reason="planner disabled")
        summaries = []
        for definition in tools[:64]:
            function = definition.get("function") if isinstance(definition, dict) else None
            if not isinstance(function, dict):
                continue
            summary = {
                "name": function.get("name"),
                "description": str(function.get("description", ""))[:240],
            }
            if function.get("name") == "execute_skill":
                # 描述截断不能让 Planner 只看到第一个 Skill；名称直接来自动态 Schema。
                summary["skills"] = function.get("parameters", {}).get("properties", {}).get("skill", {}).get("enum", [])
            summaries.append(summary)
        system_prompt = PLANNER_PROMPT.replace("{tools}", json.dumps(summaries, ensure_ascii=False))
        for attempt in range(self.max_attempts):
            try:
                data = await self.llm.plan(messages, summaries, system_prompt=system_prompt)
                decision = PlanDecision.model_validate(data)
                logger.info("[Planner] mode=%s steps=%d attempt=%d",
                            decision.mode, len(decision.steps), attempt + 1)
                return decision
            except (LLMError, ValueError, TypeError, KeyError) as error:
                logger.warning("[Planner] 规划输出无效 attempt=%d error=%s",
                               attempt + 1, type(error).__name__)
        logger.warning("[Planner] 规划失败，回退直接执行。")
        return PlanDecision(mode="direct", reason="planner_fallback")
