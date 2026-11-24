import unittest
from unittest.mock import AsyncMock

from app.orchestration import Planner
from app.orchestration.models import PlanDecision


class PlannerTests(unittest.IsolatedAsyncioTestCase):
    def planner(self, responses):
        llm = AsyncMock()
        llm.plan.side_effect = responses
        return Planner(llm, max_attempts=2), llm

    async def test_direct_decision(self):
        planner, llm = self.planner([{"mode": "direct", "reason": "one action"}])
        decision = await planner.decide([{"role": "user", "content": "hi"}], [])
        self.assertEqual(decision.mode, "direct")
        self.assertEqual(llm.plan.await_count, 1)

    async def test_plan_decision(self):
        planner, _ = self.planner([{
            "mode": "plan", "goal": "做一个工作台",
            "steps": [{"title": "准备木板", "success_criteria": "背包有 4 块木板"},
                      {"title": "合成工作台", "success_criteria": "背包新增工作台"}],
        }])
        decision = await planner.decide([{"role": "user", "content": "做工作台"}], [])
        self.assertEqual(decision.mode, "plan")
        self.assertEqual(len(decision.steps), 2)

    async def test_invalid_plan_falls_back_to_direct(self):
        planner, llm = self.planner([{"mode": "plan"}, {"mode": "plan"}])
        decision = await planner.decide([{"role": "user", "content": "x"}], [])
        self.assertEqual(decision.mode, "direct")
        self.assertEqual(decision.reason, "planner_fallback")
        self.assertEqual(llm.plan.await_count, 2)

    async def test_disabled_planner_is_direct_without_call(self):
        planner, llm = self.planner([])
        planner.enabled = False
        decision = await planner.decide([], [])
        self.assertEqual(decision.mode, "direct")
        self.assertEqual(llm.plan.await_count, 0)
