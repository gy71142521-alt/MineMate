import copy
import json

from app.execution.manager import ExecutionContext, ExecutionError, TaskManager, evidence
from app.llm_client import LLMError

from .models import SkillRequest, SkillResult, SkillVerification
from .registry import SkillRegistry


class SkillRuntime:
    """Skill 只拿到受限的工具入口，不持有 Minecraft 连接或裸 HTTP 客户端。"""

    def __init__(self, execution: ExecutionContext):
        self._execution = execution

    async def call(self, name: str, arguments: dict, *, operation_id: str, wait_seconds: float = 90) -> dict:
        return await self._execution.call(name, arguments, operation_id=operation_id, wait_seconds=wait_seconds)

    async def snapshot(self) -> dict:
        return await self._execution.snapshot()

    def report_progress(self, data: dict) -> None:
        """只更新有界执行记录，供 HTTP 观察；不改变游戏状态。"""
        self._execution.report_progress(data)


class SkillManager:
    def __init__(self, registry: SkillRegistry, executor: TaskManager):
        self.registry, self.executor = registry, executor
        self._names: dict[str, str] = {}

    async def submit(self, request: SkillRequest) -> dict:
        skill = self.registry.get(request.skill)
        parameters = self.registry.validate(request.skill, request.arguments)

        def prepare(catalog: dict) -> None:
            if any(tool not in catalog for tool in skill.required_tools):
                raise LLMError(409, "SKILL_TOOL_UNAVAILABLE", "该 Skill 依赖的游戏工具尚未注册。")

        async def program(execution: ExecutionContext) -> None:
            runtime = SkillRuntime(execution)
            before = await runtime.snapshot()
            if before.get("availability", {}).get("player") != "available" or before.get("player", {}).get("alive") is False:
                raise ExecutionError("BOT_NOT_READY")
            await skill.check_preconditions(runtime, parameters, copy.deepcopy(before))
            data = await skill.execute(runtime, parameters)
            if not isinstance(data, dict) or len(json.dumps(data, allow_nan=False).encode()) > 16384:
                raise ExecutionError("INVALID_SKILL_RESULT", uncertain=True)
            after = await runtime.snapshot()
            verification = skill.verify(parameters, copy.deepcopy(data), before, after)
            if not isinstance(verification, SkillVerification):
                raise ExecutionError("INVALID_SKILL_VERIFICATION", uncertain=True)
            if {check.criterion for check in verification.checks} != set(skill.success_criteria):
                raise ExecutionError("MISSING_SUCCESS_CRITERION", uncertain=True)
            execution.finish(data, {"before": evidence(before), "after": evidence(after),
                                    **verification.model_dump(),
                                    "verified": all(check.passed for check in verification.checks)})

        record = await self.executor.submit_program(request, prepare, program)
        self._names[record["task_id"]] = request.skill
        return self.view(record["task_id"], request.player)

    def view(self, execution_id: str, player: str) -> dict:
        if execution_id not in self._names:
            raise LLMError(404, "SKILL_EXECUTION_NOT_FOUND", "没有该 Skill 执行记录。")
        record = self.executor.view(execution_id, player)
        return SkillResult(
            execution_id=execution_id, player=record["player"], request_id=record["request_id"],
            skill=self._names[execution_id], success=record["status"] == "completed", status=record["status"],
            data=record.get("program_data"), error=record["error"], verification=record["verification"],
            steps=record["ledger"], calls=record["calls"],
            created_at=record["created_at"], updated_at=record["updated_at"]
        ).model_dump()

    async def cancel(self, execution_id: str, player: str) -> dict:
        self.view(execution_id, player)
        await self.executor.cancel(execution_id, player)
        return self.view(execution_id, player)
