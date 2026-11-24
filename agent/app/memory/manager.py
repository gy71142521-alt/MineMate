"""Per-player memory orchestration.

The manager owns session loading, append/persist ordering, per-player
serialization, context assembly and best-effort compaction. Memory failures
degrade the conversation; they do not take down the game bot.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .compaction import (
    AdaptiveSummarizer,
    CompactionPolicy,
    Summarizer,
    compact_if_needed_async,
)
from .context import compose_system_prompt, ephemeral_context
from .session import Session
from .store import EventStore
from .tokens import estimate_messages, estimate_tools
from .types import EventType, MemoryStoreError, SessionEvent

logger = logging.getLogger("minemate.memory")


@dataclass(frozen=True)
class MemoryConfig:
    context_window: int = 32768
    threshold_ratio: float = 0.75
    retain_turns: int = 6
    max_summary_chars: int = 4000
    max_tool_result_chars: int = 4000
    max_world_chars: int = 6000
    session_idle_days: float = 30.0
    auto_compact: bool = True


@dataclass
class MemoryTurn:
    session: Session
    turn: int
    step: int = 0
    closed: bool = False
    step_open: bool = False
    degraded: bool = field(default=False)


class MemoryManager:
    def __init__(self, store: EventStore, config: MemoryConfig,
                 summarizer: Summarizer | None = None, *, clock=time.time):
        self.store = store
        self.config = config
        self.summarizer = summarizer or AdaptiveSummarizer(None)
        self.policy = CompactionPolicy(
            context_window=config.context_window,
            threshold_ratio=config.threshold_ratio,
            retain_turns=config.retain_turns,
            max_summary_chars=config.max_summary_chars,
        )
        self.clock = clock
        self._sessions: dict[str, Session] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_activity: dict[str, float] = {}
        self._started = False

    async def startup(self) -> None:
        if self._started:
            return
        await asyncio.to_thread(self.store.open)
        self._started = True

    async def close(self) -> None:
        try:
            await asyncio.to_thread(self.store.close)
        finally:
            self._started = False
            self._sessions.clear()
            self._last_activity.clear()

    def _lock(self, player: str) -> asyncio.Lock:
        key = player.lower()
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def _load(self, player: str) -> Session:
        key = player.lower()
        now = self.clock()
        cached = self._sessions.get(key)
        if cached is not None:
            last = self._last_activity.get(key, cached.record.updated_at)
            if not self._expired(last, now):
                return cached
            self._sessions.pop(key, None)
            self._last_activity.pop(key, None)
        record = await asyncio.to_thread(self.store.latest_session, player)
        if record is None or self._expired(record.updated_at, now):
            record = await asyncio.to_thread(self.store.create_session, player, now=now)
            session = Session(record, [])
            first = session.append(EventType.SESSION_CREATED, {"player": player, "created_at": record.created_at}, now=now)
            await self._persist(session, [first])
        else:
            events = await asyncio.to_thread(self.store.load_events, record.id)
            expected_seq = events[-1].seq if events else -1
            if record.last_seq != expected_seq:
                raise MemoryStoreError("记忆会话序号与记录不一致。")
            session = Session(record, events)
            repaired = session.repair()
            if repaired:
                logger.warning("[Memory] 修复中断会话 session=%s events=%d", session.id, len(repaired))
                await self._persist(session, repaired)
        self._sessions[key] = session
        self._last_activity[key] = now
        return session

    def _expired(self, updated_at: float, now: float) -> bool:
        days = self.config.session_idle_days
        return days > 0 and now - updated_at > days * 86400

    async def _persist(self, session: Session, events: list[SessionEvent]) -> None:
        if not events or session.degraded:
            return
        self._last_activity[session.player.lower()] = self.clock()
        try:
            await asyncio.to_thread(self.store.append, session.id, events)
        except Exception as error:
            session.mark_degraded()
            logger.error("[Memory] 持久化失败 session=%s events=%d error=%s",
                         session.id, len(events), type(error).__name__)

    async def begin_turn(self, player: str, message: str, *, system_prompt: str | None = None,
                         now: float | None = None) -> MemoryTurn:
        async with self._lock(player):
            session = await self._load(player)
            repaired = session.repair(now=now)
            if repaired:
                logger.warning("[Memory] 修复未闭合轮次 session=%s events=%d", session.id, len(repaired))
                await self._persist(session, repaired)
            if system_prompt:
                await self._ensure_system(session, system_prompt, now=now)
            before = session.last_seq
            turn = session.start_turn(now=now)
            session.add_user(message, now=now)
            await self._persist(session, [event for event in session.events if event.seq > before])
            logger.info("[Memory] 开始轮次 session=%s turn=%d events=%d",
                        session.id, turn, len(session.events))
            return MemoryTurn(session=session, turn=turn)

    async def _ensure_system(self, session: Session, base_prompt: str, *, now: float | None = None) -> None:
        prompt = compose_system_prompt(base_prompt)
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        latest = session.latest_system()
        if latest is not None and latest.data.get("hash") == digest:
            return
        event = session.append(EventType.SYSTEM_MESSAGE, {"text": prompt, "hash": digest}, now=now)
        await self._persist(session, [event])

    async def build_context(self, turn: MemoryTurn, system_prompt: str, snapshot: Any,
                            tools: Any, *, now: datetime | None = None) -> list[dict[str, Any]]:
        async with self._lock(turn.session.player):
            await self._ensure_system(turn.session, system_prompt)
            ephemeral = ephemeral_context(
                snapshot, player=turn.session.player, now=now,
                max_world_chars=self.config.max_world_chars,
            )
            messages = turn.session.derive_messages(
                system_prompt,
                ephemeral=ephemeral,
                max_tool_result_chars=self.config.max_tool_result_chars,
            )
            if self.config.auto_compact:
                before = turn.session.last_seq
                result = await compact_if_needed_async(
                    turn.session, self.policy, messages, tools, turn.turn,
                    self.summarizer, source="auto",
                )
                # Persist marker events even when summarization failed; otherwise
                # the next append would create a seq gap and degrade the session.
                new_events = [event for event in turn.session.events if event.seq > before]
                await self._persist(turn.session, new_events)
                if result is not None:
                    messages = turn.session.derive_messages(
                        system_prompt,
                        ephemeral=ephemeral,
                        max_tool_result_chars=self.config.max_tool_result_chars,
                    )
                    logger.info("[Memory] 上下文已压缩 turn=%d tokens=%d->%d",
                                turn.turn, result.tokens_before, result.tokens_after)
            logger.debug("[Memory] 上下文完成 turn=%d messages=%d tokens=%d",
                         turn.turn, len(messages), estimate_messages(messages) + estimate_tools(tools))
            return messages

    async def start_step(self, turn: MemoryTurn) -> int:
        async with self._lock(turn.session.player):
            if turn.closed:
                raise RuntimeError("turn already closed")
            step = turn.session.start_step(turn.turn)
            turn.step = step
            turn.step_open = True
            await self._persist(turn.session, [turn.session.events[-1]])
            return step

    async def record_user(self, turn: MemoryTurn, message: str) -> None:
        """Append a mid-turn user steering message to the event log."""
        async with self._lock(turn.session.player):
            if turn.closed:
                return
            turn.session.add_user(message)
            await self._persist(turn.session, [turn.session.events[-1]])

    async def record_assistant(self, turn: MemoryTurn, content: str | None,
                               tool_calls: list[dict[str, Any]] | None = None,
                               usage: dict[str, Any] | None = None) -> None:
        async with self._lock(turn.session.player):
            if turn.closed:
                return
            turn.session.add_assistant(turn.turn, turn.step, content, tool_calls, usage)
            await self._persist(turn.session, [turn.session.events[-1]])

    async def record_tool_call(self, turn: MemoryTurn, call_id: str, name: str,
                               arguments: str) -> None:
        async with self._lock(turn.session.player):
            if turn.closed:
                return
            turn.session.add_tool_call(turn.turn, turn.step, call_id, name, arguments)
            await self._persist(turn.session, [turn.session.events[-1]])

    async def record_tool_result(self, turn: MemoryTurn, call_id: str, tool: str,
                                 result: dict[str, Any]) -> None:
        async with self._lock(turn.session.player):
            if turn.closed:
                return
            turn.session.add_tool_result(turn.turn, turn.step, call_id, tool, result)
            await self._persist(turn.session, [turn.session.events[-1]])

    async def end_step(self, turn: MemoryTurn) -> None:
        async with self._lock(turn.session.player):
            if turn.closed or not turn.step_open:
                return
            turn.session.end_step(turn.turn, turn.step)
            turn.step_open = False
            await self._persist(turn.session, [turn.session.events[-1]])

    async def end_turn(self, turn: MemoryTurn, reason: str) -> None:
        async with self._lock(turn.session.player):
            if turn.closed:
                return
            if turn.step_open:
                turn.session.end_step(turn.turn, turn.step)
                turn.step_open = False
                await self._persist(turn.session, [turn.session.events[-1]])
            turn.session.end_turn(turn.turn, reason)
            turn.closed = True
            await self._persist(turn.session, [turn.session.events[-1]])

    async def reset(self, player: str) -> None:
        async with self._lock(player):
            session = self._sessions.pop(player.lower(), None)
            self._last_activity.pop(player.lower(), None)
            if session is None:
                record = await asyncio.to_thread(self.store.latest_session, player)
                if record is not None:
                    await asyncio.to_thread(self.store.delete_session, record.id)
                return
            await asyncio.to_thread(self.store.delete_session, session.id)

    async def stats(self, player: str) -> dict[str, Any]:
        async with self._lock(player):
            session = self._sessions.get(player.lower())
            if session is None:
                record = await asyncio.to_thread(self.store.latest_session, player)
                if record is None:
                    return {"player": player, "session_id": None, "events": 0, "turns": 0,
                            "steps": 0, "summary_chars": 0, "last_seq": -1,
                            "summary": "", "degraded": False, "db_path": str(getattr(self.store, "path", "")) or None}
                events = await asyncio.to_thread(self.store.load_events, record.id)
                session = Session(record, events)
            data = session.stats()
            data["summary"] = session.summary_text()[:2000]
            db_path = getattr(self.store, "path", None)
            data["db_path"] = str(db_path) if db_path is not None else None
            return data
