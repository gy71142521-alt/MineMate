import asyncio
import copy
import json
import logging
import re
import time
import uuid
from contextlib import suppress
from typing import Any, Awaitable, Callable

from .. import diagnostics
from ..llm_client import LLMError
from ..tool_client import ToolClient, validate
from .models import ExecutionRequest, Step, TaskRequest
from .references import ReferenceError, resolve, validate_template

logger = logging.getLogger("minemate.execution")


class ExecutionError(Exception):
    def __init__(self, code: str, *, uncertain: bool = False):
        self.code, self.uncertain = code, uncertain


def inventory_counts(snapshot: dict) -> dict[str, int]:
    if not isinstance(snapshot, dict) or snapshot.get("availability", {}).get("inventory") != "available":
        raise ExecutionError("INVENTORY_UNAVAILABLE")
    result: dict[str, int] = {}
    for item in snapshot.get("inventory", []):
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or type(item.get("count")) is not int or item["count"] < 0:
            raise ExecutionError("INVALID_INVENTORY")
        result[item["name"]] = result.get(item["name"], 0) + item["count"]
    return result


def evidence(snapshot: dict) -> dict:
    """账本只保存操作核对所需的自身/背包，避免每次熔炉轮询复制实体和方块列表。"""
    return {key: copy.deepcopy(snapshot.get(key)) for key in ("timestamp", "availability", "player", "inventory")}


class TaskManager:
    """一个后台串行执行槽；不接收代码、不生成计划、不自动重试副作用。"""

    def __init__(self, client: ToolClient, *, poll_interval: float = 2, heartbeat_interval: float = 5):
        self.client = client
        self.poll_interval, self.heartbeat_interval = poll_interval, heartbeat_interval
        self.records: dict[str, dict] = {}
        self._work: dict[str, asyncio.Task] = {}
        self._admission = asyncio.Lock()
        self._closed = False

    async def submit(self, request: TaskRequest) -> dict:
        def prepare(catalog: dict) -> None:
            previous: set[str] = set()
            step = None
            try:
                for step in request.steps:
                    if step.tool not in catalog:
                        raise ReferenceError("tool not registered")
                    if not validate_template(catalog[step.tool]["parameters"], step.arguments, previous):
                        raise ReferenceError("invalid arguments or reference")
                    if step.tool in ("stop_movement", "follow_player"):
                        raise ReferenceError("continuous or preemptive tool")
                    previous.add(step.step_id)
            except (ReferenceError, ValueError, TypeError, RecursionError) as error:
                diagnostics.log_error(
                    logger, "[Execution] 任务步骤校验失败", error,
                    secrets=(self.client.settings.llm_api_key,), level=logging.WARNING,
                    include_traceback=False, player=request.player, request=request.request_id,
                    step=getattr(step, "step_id", "-"), tool=getattr(step, "tool", "-"),
                )
                raise LLMError(422, "INVALID_TASK", "步骤工具、参数或前序结果引用无效；持续跟随和停止请使用原接口。") from error

        return await self.submit_program(request, prepare)

    async def submit_program(self, request: ExecutionRequest, prepare: Callable[[dict], None],
                             program: Callable[["ExecutionContext"], Awaitable[None]] | None = None) -> dict:
        """只接受进程内注册的程序，复用原执行槽；HTTP 不接受可执行内容。"""
        async with self._admission:
            if self._closed:
                raise LLMError(503, "EXECUTOR_CLOSED", "后台执行器已关闭。")
            fingerprint = json.dumps([type(request).__name__, request.model_dump()], sort_keys=True, allow_nan=False)
            for record in self.records.values():
                if record["player"].lower() == request.player.lower() and record["request_id"] == request.request_id:
                    if record["fingerprint"] != fingerprint:
                        raise LLMError(409, "REQUEST_CONFLICT", "提交 ID 已用于另一任务。")
                    return self.view(record["task_id"])
            if any(not work.done() for work in self._work.values()):
                raise LLMError(409, "TASK_BUSY", "已有后台任务，请先取消后再提交。")
            # 不驱逐记录后接受同一 ID，避免误认为全局持久幂等。重启后任务不会自动恢复。
            if len(self.records) >= 64:
                raise LLMError(429, "TASK_HISTORY_FULL", "本进程任务记录已满，请保存记录后重启 Agent。")
            catalog, context = await self.client.session()
            prepare(catalog)
            acquired = await self.client.lease("acquire", request.player, context)
            context = {**context, "leaseId": acquired["lease"]["leaseId"]}
            task_id = uuid.uuid4().hex
            record = {"task_id": task_id, "player": request.player, "request_id": request.request_id, "status": "pending",
                      "created_at": time.time(), "updated_at": time.time(), "steps": [], "ledger": [], "error": None,
                      "verification": None, "fingerprint": fingerprint, "context": context, "calls": 0}
            self.records[task_id] = record
            self._work[task_id] = asyncio.create_task(self._run(record, request, catalog, program))
            return self.view(task_id)

    def view(self, task_id: str, player: str | None = None) -> dict:
        record = self.records.get(task_id)
        if not record or player and player.lower() != record["player"].lower():
            raise LLMError(404, "TASK_NOT_FOUND", "没有该玩家的任务记录。")
        return copy.deepcopy({key: value for key, value in record.items() if key not in ("fingerprint", "context")})

    async def wait_settled(self, task_id: str) -> None:
        """等待任务的 finally 清理完成后再让调用方启动下一项副作用。

        ExecutionContext.finish() 会先写入终态，随后 `_run()` 才释放 Node
        租约。只看 status 可能在这个窗口内让下一项操作撞上 BUSY。
        """
        work = self._work.get(task_id)
        if work is not None and not work.done():
            await asyncio.shield(work)

    async def cancel(self, task_id: str, player: str) -> dict:
        self.view(task_id, player)
        work = self._work.get(task_id)
        if work and not work.done():
            work.cancel()
            with suppress(asyncio.CancelledError):
                await work
            # create_task 后立即取消时，协程可能尚未进入 try/finally。
            if self.records[task_id]["status"] == "pending":
                record = self.records[task_id]
                record["status"], record["error"] = "cancelled", {"code": "CANCELLED", "message": "任务尚未执行即被取消。"}
                with suppress(LLMError):
                    await self.client.lease("release", player, record["context"])
        return self.view(task_id, player)

    async def close(self) -> None:
        self._closed = True
        for work in self._work.values():
            if not work.done():
                work.cancel()
        await asyncio.gather(*self._work.values(), return_exceptions=True)
        for record in self.records.values():
            if record["status"] == "pending":
                record["status"] = "cancelled"
                with suppress(LLMError):
                    await self.client.lease("release", record["player"], record["context"])

    async def _heartbeat(self, record: dict) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            await self.client.lease("heartbeat", record["player"], record["context"])

    async def _snapshot(self, record: dict) -> dict:
        # 前后都检查实例/代次/租约；不能用 Node 重启后的新世界验证旧任务。
        await self.client.lease("heartbeat", record["player"], record["context"])
        snapshot = await self.client.request("GET", "/api/world")
        await self.client.lease("heartbeat", record["player"], record["context"])
        if not isinstance(snapshot, dict):
            raise ExecutionError("INVALID_WORLD_SNAPSHOT")
        return snapshot

    async def _run(self, record: dict, request: ExecutionRequest, catalog: dict,
                   program: Callable[["ExecutionContext"], Awaitable[None]] | None = None) -> None:
        worker = heartbeat = None
        execution = ExecutionContext(self, record, request, catalog)
        try:
            record["status"] = "running"
            async with asyncio.timeout(request.timeout_seconds):
                worker = asyncio.create_task(program(execution) if program else self._sequence(record, request, catalog))
                heartbeat = asyncio.create_task(self._heartbeat(record))
                done, _ = await asyncio.wait({worker, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
                if heartbeat in done:
                    await heartbeat  # 租约失效立即阻断后续调用。
                await worker
        except asyncio.CancelledError:
            record["status"], record["error"] = "cancelled", {"code": "CANCELLED", "message": "任务已取消；已发生的游戏操作不会回滚。"}
            logger.info("[Execution] 任务取消 task=%s calls=%d", record["task_id"], record["calls"])
        except TimeoutError:
            record["status"], record["error"] = "uncertain", {"code": "TASK_TIMEOUT", "message": "任务超时，已请求停止；请根据现场确认已发生的操作。"}
            logger.warning("[Execution] 任务超时 task=%s calls=%d timeout=%.1fs",
                           record["task_id"], record["calls"], request.timeout_seconds)
        except ExecutionError as error:
            record["status"] = "uncertain" if error.uncertain else "failed"
            record["error"] = {"code": error.code, "message": "执行未确认或条件不满足，请查看执行记录与游戏现场；未自动重试。"}
            logger.warning("[Execution] 任务失败 task=%s code=%s uncertain=%s calls=%d",
                           record["task_id"], error.code, error.uncertain, record["calls"])
        except LLMError as error:
            record["status"], record["error"] = "failed", {"code": error.code, "message": "游戏服务不可用或任务控制权已失效，已停止后续调用。"}
            diagnostics.log_error(
                logger, "[Execution] 任务控制失败", error,
                secrets=(self.client.settings.llm_api_key,),
                level=logging.ERROR if error.status_code >= 500 else logging.WARNING,
                include_traceback=error.status_code >= 500,
                task=record["task_id"], code=error.code, status=error.status_code, calls=record["calls"],
            )
        except Exception as error:
            record["status"], record["error"] = "uncertain", {"code": "EXECUTION_FAILED", "message": "后台执行异常，结果需要核对；未自动重试。"}
            diagnostics.log_error(
                logger, "[Execution] 后台执行异常", error,
                secrets=(self.client.settings.llm_api_key,), level=logging.ERROR,
                task=record["task_id"], player=request.player, calls=record["calls"],
            )
        finally:
            execution.close()
            for child in (worker, heartbeat):
                if child and not child.done():
                    child.cancel()
            await asyncio.gather(*(child for child in (worker, heartbeat) if child), return_exceptions=True)
            for step in record["steps"]:
                if step["status"] == "running":
                    step["status"] = record["status"]
            for entry in record["ledger"]:
                if entry["status"] == "submitted":
                    entry["status"] = "uncertain"
            # 精确租约清理；旧任务清理遇到新实例/新任务会被 Node 拒绝。
            try:
                await self.client.lease("release", request.player, record["context"])
            except (LLMError, ExecutionError) as error:
                record["cleanup"] = "lease_already_lost_or_node_unavailable"
                logger.warning("[Execution] 释放租约失败 task=%s code=%s",
                               record["task_id"], getattr(error, "code", type(error).__name__))
            record["updated_at"] = time.time()
            logger.info("[Execution] task=%s status=%s", record["task_id"], record["status"])

    async def _call(self, record: dict, request: ExecutionRequest, catalog: dict, name: str, arguments: dict) -> dict:
        if name not in catalog or not validate(catalog[name]["parameters"], arguments):
            raise ExecutionError("INVALID_ARGUMENT")
        if record["calls"] >= request.max_tool_calls:
            raise ExecutionError("CALL_LIMIT")
        record["calls"] += 1
        request_id = f'{record["task_id"]}_{record["calls"]}'
        call_started = time.monotonic()
        logger.info("[Execution] 工具调用开始 task=%s tool=%s request_id=%s call=%d",
                    record["task_id"], name, request_id, record["calls"])
        entry: dict[str, Any] = {"request_id": request_id, "tool": name, "arguments": copy.deepcopy(arguments),
                                 "status": "submitted", "result": None, "before": None, "after": None}
        record["ledger"].append(entry)
        record["updated_at"] = time.time()
        if catalog[name].get("mutates", True):
            entry["before"] = evidence(await self._snapshot(record))
        try:
            result = await self.client.execute(name, arguments, request.player, request_id,
                                               record["context"]["generation"], context=record["context"])
        except LLMError as error:
            logger.warning("[Execution] 工具调用失败 task=%s tool=%s request_id=%s code=%s status=%d elapsed_ms=%d",
                           record["task_id"], name, request_id, error.code, error.status_code,
                           int((time.monotonic() - call_started) * 1000))
            if error.code in {"STALE_TASK", "STALE_INSTANCE", "STALE_LEASE", "FORBIDDEN", "BUSY", "BOT_NOT_READY"}:
                entry["status"] = "rejected"
                raise
            # 网络断开不能推断未执行。仅查询原请求回执，不重新 POST 副作用。
            try:
                receipt = await self.client.receipt(request.player, request_id, record["context"]["instanceId"])
                entry["receipt"] = receipt
                if receipt.get("running") is not False:
                    raise ExecutionError("UNCONFIRMED_REQUEST", uncertain=True)
                result = ToolClient.validate_result(name, receipt.get("result"))
            except (LLMError, ExecutionError) as receipt_error:
                logger.warning("[Execution] 回执无法确认 task=%s tool=%s request_id=%s error=%s",
                               record["task_id"], name, request_id, type(receipt_error).__name__)
                entry["status"] = "uncertain"
                with suppress(LLMError, ExecutionError):
                    entry["after"] = evidence(await self._snapshot(record))
                raise ExecutionError("UNCONFIRMED_REQUEST", uncertain=True) from receipt_error
        entry["result"] = copy.deepcopy(result)
        data = result.get("data") or {}
        error_code = (result.get("error") or {}).get("code", "TOOL_FAILED")
        logger.info("[Execution] 工具调用完成 task=%s tool=%s request_id=%s success=%s status=%s error=%s elapsed_ms=%d",
                    record["task_id"], name, request_id, result["success"], data.get("status"), error_code,
                    int((time.monotonic() - call_started) * 1000))
        if not result["success"] or data.get("status") in ("uncertain", "failed", "cancelled"):
            uncertain = data.get("status") == "uncertain" or error_code in ("TIMEOUT", "EXECUTION_FAILED", "CANCELLED")
            entry["status"] = "uncertain" if uncertain else "failed"
            with suppress(LLMError, ExecutionError):
                entry["after"] = evidence(await self._snapshot(record))
            raise ExecutionError(error_code if not result["success"] else "UNCONFIRMED_RESULT" if uncertain else "STEP_FAILED", uncertain=uncertain)
        entry["status"] = data.get("status", "completed")
        return result

    async def _wait(self, record: dict, request: ExecutionRequest, catalog: dict, step, arguments: dict, result: dict) -> dict:
        data = result["data"]
        if data.get("status") != "pending":
            return result
        deadline = asyncio.get_running_loop().time() + step.wait_seconds
        if step.tool == "goto_position":
            task_id = data.get("movement", {}).get("taskId")
            if type(task_id) is not int:
                raise ExecutionError("MISSING_MOVEMENT_ID", uncertain=True)
            while asyncio.get_running_loop().time() < deadline:
                current = await self._call(record, request, catalog, "get_movement_task", {"task_id": task_id})
                if current["data"]["status"] == "completed":
                    return current
                if current["data"]["status"] != "pending":
                    raise ExecutionError("NAVIGATION_FAILED")
                await asyncio.sleep(self.poll_interval)
        elif step.tool == "smelt_item" and arguments.get("action") == "start":
            output, count = data.get("expectedOutput"), data.get("expectedCount")
            if not isinstance(output, str) or type(count) is not int:
                raise ExecutionError("INVALID_SMELTING_RESULT", uncertain=True)
            status_args = {"position": arguments["position"], "action": "status", "item_name": output, "count": count}
            while asyncio.get_running_loop().time() < deadline:
                current = await self._call(record, request, catalog, "smelt_item", status_args)
                if current["data"].get("outputReady") is True and current["data"].get("status") == "completed":
                    return await self._call(record, request, catalog, "smelt_item", {**status_args, "action": "take"})
                await asyncio.sleep(self.poll_interval)
        else:
            raise ExecutionError("UNSUPPORTED_PENDING", uncertain=True)
        raise ExecutionError("WAIT_TIMEOUT", uncertain=True)

    async def _sequence(self, record: dict, request: TaskRequest, catalog: dict) -> None:
        before = await self._snapshot(record)
        results: dict[str, dict] = {}
        for step in request.steps:
            arguments = resolve(step.arguments, results, set(results))
            entry = {"step_id": step.step_id, "tool": step.tool, "status": "running", "result": None}
            record["steps"].append(entry)
            result = await self._call(record, request, catalog, step.tool, arguments)
            try:
                async with asyncio.timeout(step.wait_seconds):
                    result = await self._wait(record, request, catalog, step, arguments, result)
            except TimeoutError:
                raise ExecutionError("WAIT_TIMEOUT", uncertain=True) from None
            entry["status"], entry["result"] = "completed", result
            results[step.step_id] = result
        after = await self._snapshot(record)
        record["verification"] = {"before": before, "after": after, "checks": [], "verified": False}
        if request.verify_inventory:
            initial, final = inventory_counts(before), inventory_counts(after)
            for check in request.verify_inventory:
                observed = final.get(check.item, 0) - (initial.get(check.item, 0) if check.mode == "increase" else 0)
                record["verification"]["checks"].append({**check.model_dump(), "observed": observed, "passed": observed >= check.count})
            if not all(check["passed"] for check in record["verification"]["checks"]):
                raise ExecutionError("GOAL_NOT_VERIFIED")
            record["verification"]["verified"] = True
            record["status"] = "completed"
        else:
            record["status"] = "finished_unverified"


class ExecutionContext:
    """提供给确定性 Skill 的受限工具入口，共用账本、等待和租约语义。"""

    def __init__(self, manager: TaskManager, record: dict, request: ExecutionRequest, catalog: dict):
        self._manager, self._record, self._request, self._catalog = manager, record, request, catalog
        self._closed = False
        self._busy = False
        self._unconfirmed = False
        self._operations: dict[str, dict] = {}

    def close(self) -> None:
        self._closed = True

    def _check(self) -> None:
        if self._closed:
            raise ExecutionError("EXECUTION_CLOSED")
        if self._unconfirmed:
            raise ExecutionError("UNCONFIRMED_RESULT", uncertain=True)
        if asyncio.current_task().cancelling():
            raise asyncio.CancelledError

    async def snapshot(self) -> dict:
        self._check()
        value = await self._manager._snapshot(self._record)
        self._check()
        return copy.deepcopy(value)

    def report_progress(self, data: dict) -> None:
        self._check()
        if not isinstance(data, dict) or len(json.dumps(data, allow_nan=False).encode()) > 16384:
            raise ExecutionError("INVALID_SKILL_RESULT")
        self._record["program_data"] = copy.deepcopy(data)
        self._record["updated_at"] = time.time()

    async def call(self, name: str, arguments: dict, *, operation_id: str, wait_seconds: float = 90) -> dict:
        self._check()
        # operation_id 表示一次逻辑操作；相同 ID 只返回原结果，绝不重复副作用。
        if not isinstance(operation_id, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}", operation_id):
            raise ExecutionError("INVALID_OPERATION_ID")
        if name in ("stop_movement", "follow_player"):
            raise ExecutionError("UNBOUNDED_OR_PREEMPTIVE_TOOL")
        step = Step(step_id="operation", tool=name, arguments=arguments, wait_seconds=wait_seconds)
        fingerprint = json.dumps([name, arguments, wait_seconds], sort_keys=True, allow_nan=False)
        previous = self._operations.get(operation_id)
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise ExecutionError("OPERATION_CONFLICT")
            if previous["result"] is None:
                raise ExecutionError("OPERATION_ALREADY_ATTEMPTED", uncertain=True)
            return copy.deepcopy(previous["result"])
        if self._busy:
            raise ExecutionError("CONCURRENT_TOOL_CALL")
        self._busy = True
        operation = {"fingerprint": fingerprint, "result": None}
        self._operations[operation_id] = operation
        try:
            result = await self._manager._call(self._record, self._request, self._catalog, name, arguments)
            self._check()
            async with asyncio.timeout(wait_seconds):
                result = await self._manager._wait(self._record, self._request, self._catalog, step, arguments, result)
            self._check()
            operation["result"] = copy.deepcopy(result)
            return result
        except (ExecutionError, LLMError) as error:
            if isinstance(error, LLMError) or error.uncertain:
                self._unconfirmed = True
            raise
        except TimeoutError:
            self._unconfirmed = True
            raise ExecutionError("WAIT_TIMEOUT", uncertain=True) from None
        finally:
            self._busy = False

    def finish(self, data: dict, verification: dict) -> None:
        self._check()
        # 即使 Tool 返回 completed，也必须由 Skill 的独立验收钩子给出证据。
        self._record["program_data"] = copy.deepcopy(data)
        self._record["verification"] = copy.deepcopy(verification)
        checks = verification.get("checks")
        if not isinstance(checks, list) or not checks or not all(check.get("passed") is True for check in checks):
            raise ExecutionError("GOAL_NOT_VERIFIED")
        self._record["status"] = "completed"
