"""Append-only session log and derived model surface.

Mirrors the DSH contract in Python: events are the durable truth; Chat
Completions messages are a projection. Compaction records which older events are
shadowed by a summary, so replay does not need a separate message database.
"""

from __future__ import annotations

import json
import logging
import math
import time
from typing import Any, Iterable


from .types import (
    EventType,
    MemoryCorruptError,
    SessionEvent,
    SessionRecord,
)


logger = logging.getLogger("minemate.memory.session")

CHECKPOINT_PREAMBLE = (
    "这是此前对话的自动压缩摘要，用于释放上下文空间。"
    "请把它当作已经确认的背景，不要复述摘要，直接从后面的最近消息继续。"
)
SUMMARY_OPEN = "<compacted-summary>"
SUMMARY_CLOSE = "</compacted-summary>"
_IGNORED_SURFACE_TYPES = {
    EventType.SESSION_CREATED,
    EventType.SYSTEM_MESSAGE,
    EventType.TURN_START,
    EventType.TURN_END,
    EventType.STEP_START,
    EventType.STEP_END,
    EventType.COMPACTION_START,
    EventType.COMPACTION_SUMMARY,
    EventType.COMPACTION_END,
}


def _json_safe(value: Any) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError("event data must be lossless JSON") from error


def _require_text(value: Any, field: str, *, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()) or len(value) > maximum:
        raise ValueError(f"{field} must be a non-empty string <= {maximum} chars")
    return value


def _require_int(value: Any, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


class Session:
    """One player's durable event log plus its derived surface."""

    def __init__(self, record: SessionRecord, events: Iterable[SessionEvent]):
        self.record = record
        self._events = list(events)
        self._validate_sequence()
        self._next_seq = self._events[-1].seq + 1 if self._events else 0
        self._degraded = False

    @property
    def id(self) -> str:
        return self.record.id

    @property
    def player(self) -> str:
        return self.record.player

    @property
    def events(self) -> tuple[SessionEvent, ...]:
        return tuple(self._events)

    @property
    def last_seq(self) -> int:
        return self._next_seq - 1

    @property
    def degraded(self) -> bool:
        return self._degraded

    def mark_degraded(self) -> None:
        self._degraded = True

    def _validate_sequence(self) -> None:
        for index, event in enumerate(self._events):
            if event.seq != index:
                raise MemoryCorruptError(f"会话 {self.id} 的事件序号不连续。")
            if event.time < 0 or not math.isfinite(event.time):
                raise MemoryCorruptError(f"会话 {self.id} 的事件时间无效。")

    def append(self, event_type: EventType, data: dict[str, Any], *, now: float | None = None) -> SessionEvent:
        self._validate_data(event_type, data)
        event = SessionEvent(
            seq=self._next_seq,
            time=time.time() if now is None else float(now),
            type=event_type,
            data=data,
        )
        self._events.append(event)
        self._next_seq += 1
        return event

    def _validate_data(self, event_type: EventType, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise ValueError("event data must be an object")
        _json_safe(data)
        if event_type in {EventType.TURN_START, EventType.TURN_END, EventType.STEP_START,
                          EventType.STEP_END, EventType.COMPACTION_START, EventType.COMPACTION_END}:
            _require_int(data.get("turn"), "turn", minimum=0)
        if event_type in {EventType.STEP_START, EventType.STEP_END}:
            _require_int(data.get("step"), "step", minimum=0)
        if event_type == EventType.USER_MESSAGE:
            _require_text(data.get("text"), "text", maximum=8000)
        elif event_type == EventType.SYSTEM_MESSAGE:
            _require_text(data.get("text"), "text", maximum=20000)
            _require_text(data.get("hash"), "hash", maximum=128)
        elif event_type == EventType.ASSISTANT_MESSAGE:
            content = data.get("content")
            if content is not None and (not isinstance(content, str) or len(content) > 16000):
                raise ValueError("assistant content must be null or a string <= 16000 chars")
            calls = data.get("tool_calls") or []
            if not isinstance(calls, list) or len(calls) > 8:
                raise ValueError("assistant tool_calls must be a list <= 8 items")
            for call in calls:
                if not isinstance(call, dict) or set(call) != {"id", "type", "function"}:
                    raise ValueError("assistant tool call has an invalid shape")
                if call.get("type") != "function" or not isinstance(call.get("id"), str) or not call["id"]:
                    raise ValueError("assistant tool call has an invalid id/type")
                function = call.get("function")
                if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not isinstance(function.get("arguments"), str):
                    raise ValueError("assistant tool call function is invalid")
            usage = data.get("usage")
            if usage is not None and not isinstance(usage, dict):
                raise ValueError("usage must be an object or null")
            _require_int(data.get("turn"), "turn", minimum=0)
            _require_int(data.get("step"), "step", minimum=0)
        elif event_type == EventType.TOOL_CALL:
            _require_text(data.get("call_id"), "call_id", maximum=512)
            _require_text(data.get("name"), "name", maximum=64)
            _require_text(data.get("arguments"), "arguments", maximum=4096, allow_empty=True)
            _require_int(data.get("turn"), "turn", minimum=0)
            _require_int(data.get("step"), "step", minimum=0)
        elif event_type == EventType.TOOL_RESULT:
            _require_text(data.get("call_id"), "call_id", maximum=512)
            _require_text(data.get("tool"), "tool", maximum=64)
            if not isinstance(data.get("result"), dict):
                raise ValueError("tool result must be an object")
            _require_int(data.get("turn"), "turn", minimum=0)
            _require_int(data.get("step"), "step", minimum=0)
        elif event_type == EventType.COMPACTION_SUMMARY:
            _require_text(data.get("summary"), "summary", maximum=20000)
            shadowed = data.get("shadowed_seqs")
            if not isinstance(shadowed, list) or any(type(seq) is not int or seq < 0 for seq in shadowed):
                raise ValueError("shadowed_seqs must be a list of non-negative integers")
            for key in ("tokens_before", "tokens_after"):
                if type(data.get(key)) is not int or data[key] < 0:
                    raise ValueError(f"{key} must be a non-negative integer")
        elif event_type == EventType.TURN_END:
            _require_text(data.get("reason"), "reason", maximum=64)
        elif event_type == EventType.COMPACTION_END:
            error = data.get("error")
            if error is not None and not isinstance(error, dict):
                raise ValueError("compaction error must be an object or null")

    def _latest(self, event_type: EventType) -> SessionEvent | None:
        for event in reversed(self._events):
            if event.type == event_type:
                return event
        return None

    def latest_system(self) -> SessionEvent | None:
        return self._latest(EventType.SYSTEM_MESSAGE)

    def latest_summary(self) -> SessionEvent | None:
        return self._latest(EventType.COMPACTION_SUMMARY)

    def summary_text(self) -> str:
        event = self.latest_summary()
        return str(event.data.get("summary", "")) if event else ""

    def shadowed_seqs(self) -> set[int]:
        event = self.latest_summary()
        if not event:
            return set()
        return {int(seq) for seq in event.data.get("shadowed_seqs", [])}

    def next_turn(self) -> int:
        turns = [int(event.data["turn"]) for event in self._events if event.type == EventType.TURN_START]
        return max(turns, default=-1) + 1

    def next_step(self, turn: int) -> int:
        steps = [int(event.data["step"]) for event in self._events
                 if event.type == EventType.STEP_START and int(event.data["turn"]) == turn]
        return max(steps, default=-1) + 1

    def start_turn(self, now: float | None = None) -> int:
        turn = self.next_turn()
        self.append(EventType.TURN_START, {"turn": turn}, now=now)
        return turn

    def end_turn(self, turn: int, reason: str, now: float | None = None) -> None:
        self.append(EventType.TURN_END, {"turn": turn, "reason": reason}, now=now)

    def start_step(self, turn: int, now: float | None = None) -> int:
        step = self.next_step(turn)
        self.append(EventType.STEP_START, {"turn": turn, "step": step}, now=now)
        return step

    def end_step(self, turn: int, step: int, now: float | None = None) -> None:
        self.append(EventType.STEP_END, {"turn": turn, "step": step}, now=now)

    def add_user(self, text: str, now: float | None = None) -> None:
        self.append(EventType.USER_MESSAGE, {"text": text}, now=now)

    def add_system(self, text: str, text_hash: str, now: float | None = None) -> None:
        self.append(EventType.SYSTEM_MESSAGE, {"text": text, "hash": text_hash}, now=now)

    def add_assistant(self, turn: int, step: int, content: str | None,
                      tool_calls: list[dict[str, Any]] | None = None,
                      usage: dict[str, Any] | None = None, now: float | None = None) -> None:
        self.append(EventType.ASSISTANT_MESSAGE, {
            "turn": turn, "step": step, "content": content,
            "tool_calls": tool_calls or [], "usage": usage,
        }, now=now)

    def add_tool_call(self, turn: int, step: int, call_id: str, name: str,
                      arguments: str, now: float | None = None) -> None:
        self.append(EventType.TOOL_CALL, {
            "turn": turn, "step": step, "call_id": call_id, "name": name, "arguments": arguments,
        }, now=now)

    def add_tool_result(self, turn: int, step: int, call_id: str, tool: str,
                        result: dict[str, Any], now: float | None = None) -> None:
        self.append(EventType.TOOL_RESULT, {
            "turn": turn, "step": step, "call_id": call_id, "tool": tool, "result": result,
        }, now=now)

    def derive_messages(self, system_prompt: str, *, ephemeral: list[dict[str, Any]] | None = None,
                        max_tool_result_chars: int = 4000) -> list[dict[str, Any]]:
        """Project the durable log into a valid Chat Completions history.

        Incomplete assistant/tool pairs are omitted rather than sent to the
        provider. A crashed turn therefore loses its partial tail but cannot
        produce an invalid request.
        """
        system_event = self.latest_system()
        system_text = str(system_event.data["text"]) if system_event else system_prompt
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_text}]

        summary = self.latest_summary()
        if summary and str(summary.data.get("summary", "")).strip():
            summary_text = str(summary.data["summary"]).replace(SUMMARY_OPEN, "").replace(SUMMARY_CLOSE, "")
            messages.append({
                "role": "user",
                "content": f"{CHECKPOINT_PREAMBLE}\n{SUMMARY_OPEN}\n{summary_text}\n{SUMMARY_CLOSE}",
            })

        shadowed = self.shadowed_seqs()
        pending: dict[str, Any] | None = None
        pending_calls: set[str] = set()
        pending_results: list[dict[str, Any]] = []

        def flush() -> None:
            nonlocal pending, pending_calls, pending_results
            if pending is None:
                return
            if pending_calls:
                logger.warning("[Memory] 跳过不完整的工具调用块 turn=%s step=%s missing=%d",
                               pending["turn"], pending["step"], len(pending_calls))
            elif pending["tool_calls"] or (
                    isinstance(pending["content"], str) and pending["content"].strip()):
                messages.append({
                    "role": "assistant",
                    "content": pending["content"],
                    **({"tool_calls": pending["tool_calls"]} if pending["tool_calls"] else {}),
                })
                messages.extend(pending_results)
            pending, pending_calls, pending_results = None, set(), []

        for event in self._events:
            if event.seq in shadowed or event.type in _IGNORED_SURFACE_TYPES:
                continue
            if event.type == EventType.USER_MESSAGE:
                flush()
                messages.append({"role": "user", "content": str(event.data["text"])})
            elif event.type == EventType.ASSISTANT_MESSAGE:
                flush()
                calls = event.data.get("tool_calls") or []
                pending = {
                    "turn": int(event.data["turn"]),
                    "step": int(event.data["step"]),
                    "content": event.data.get("content"),
                    "tool_calls": calls,
                }
                pending_calls = {str(call["id"]) for call in calls}
                if not pending_calls:
                    flush()
            elif event.type == EventType.TOOL_RESULT:
                if pending is None:
                    logger.warning("[Memory] 跳过孤立的工具结果 call_id=%s", event.data["call_id"])
                    continue
                call_id = str(event.data["call_id"])
                if call_id not in pending_calls:
                    logger.warning("[Memory] 跳过未配对的工具结果 call_id=%s", call_id)
                    continue
                pending_calls.remove(call_id)
                pending_results.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _tool_result_text(event.data["result"], max_tool_result_chars),
                })
        flush()

        if ephemeral:
            for message in ephemeral:
                if isinstance(message, dict) and message.get("role") in {"user", "system", "assistant"} and "content" in message:
                    messages.append(message)
        return messages

    def repair(self, now: float | None = None) -> list[SessionEvent]:
        """Close a crashed turn/step without inventing a model reply.

        The incomplete assistant/tool block stays in the audit log but the
        projection drops it. This keeps subsequent requests protocol-valid.
        """
        appended: list[SessionEvent] = []
        open_steps: set[tuple[int, int]] = set()
        open_turns: set[int] = set()
        open_compactions = 0
        last_compaction_turn = 0
        for event in self._events:
            if event.type == EventType.STEP_START:
                open_steps.add((int(event.data["turn"]), int(event.data["step"])))
            elif event.type == EventType.STEP_END:
                open_steps.discard((int(event.data["turn"]), int(event.data["step"])))
            elif event.type == EventType.TURN_START:
                open_turns.add(int(event.data["turn"]))
            elif event.type == EventType.TURN_END:
                open_turns.discard(int(event.data["turn"]))
            elif event.type == EventType.COMPACTION_START:
                open_compactions += 1
                last_compaction_turn = int(event.data["turn"])
            elif event.type == EventType.COMPACTION_END:
                open_compactions = max(0, open_compactions - 1)
        for _ in range(open_compactions):
            appended.append(self.append(EventType.COMPACTION_END, {
                "turn": last_compaction_turn,
                "error": {"type": "Interrupted", "message": "压缩过程中断，未写入摘要。"},
            }, now=now))
        for turn, step in sorted(open_steps):
            appended.append(self.append(EventType.STEP_END, {"turn": turn, "step": step}, now=now))
        for turn in sorted(open_turns):
            appended.append(self.append(EventType.TURN_END, {"turn": turn, "reason": "interrupted"}, now=now))
        return appended

    def stats(self) -> dict[str, Any]:
        return {
            "session_id": self.id,
            "player": self.player,
            "events": len(self._events),
            "turns": sum(1 for event in self._events if event.type == EventType.TURN_START),
            "steps": sum(1 for event in self._events if event.type == EventType.STEP_START),
            "summary_chars": len(self.summary_text()),
            "last_seq": self.last_seq,
            "degraded": self._degraded,
        }


def _tool_result_text(result: dict[str, Any], maximum: int) -> str:
    try:
        text = json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        text = str(result)
    if maximum > 0 and len(text) > maximum:
        # Keep the JSON transport valid while retaining a useful head.
        return json.dumps({
            "_truncated": True,
            "original_chars": len(text),
            "preview": text[:maximum],
        }, ensure_ascii=False)
    return text

