import asyncio
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

from app.config import load_settings
from app.intent import MessageRequest
from app.llm_client import LLMClient
from app.memory import (
    AdaptiveSummarizer,
    DeterministicSummarizer,
    InMemoryEventStore,
    MemoryConfig,
    MemoryManager,
    SQLiteEventStore,
)
from app.memory.compaction import CompactionPolicy, compact_if_needed_async
from app.memory.context import compact_world_snapshot, compose_system_prompt
from app.memory.session import Session
from app.memory.tokens import estimate_messages, estimate_text
from app.memory.types import EventType, SessionRecord
from app.tool_loop import ToolLoop


def record(player="Steve"):
    now = time.time()
    return SessionRecord(id="s1", player=player, created_at=now, updated_at=now, last_seq=-1)


def call(call_id="call1", name="query_status", arguments="{}"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": arguments}}


class SessionTests(unittest.TestCase):
    def test_derives_system_user_assistant_and_tool_pair(self):
        session = Session(record(), [])
        session.add_system("SYS", "hash")
        turn = session.start_turn()
        session.add_user("看看状态")
        step = session.start_step(turn)
        session.add_assistant(turn, step, None, [call()])
        session.add_tool_result(turn, step, "call1", "query_status", {
            "success": True, "tool": "query_status",
            "data": {"status": "completed", "health": 20}, "error": None,
        })
        session.end_step(turn, step)
        session.end_turn(turn, "completed")

        messages = session.derive_messages("fallback")
        self.assertEqual(messages[0], {"role": "system", "content": "SYS"})
        self.assertEqual(messages[1], {"role": "user", "content": "看看状态"})
        self.assertEqual(messages[2]["role"], "assistant")
        self.assertEqual(messages[2]["tool_calls"][0]["id"], "call1")
        self.assertEqual(messages[3]["role"], "tool")
        self.assertEqual(messages[3]["tool_call_id"], "call1")
        self.assertIn("health", messages[3]["content"])

    def test_incomplete_tool_pair_is_not_sent_to_model(self):
        session = Session(record(), [])
        turn = session.start_turn()
        session.add_user("挖矿")
        step = session.start_step(turn)
        session.add_assistant(turn, step, None, [call()])
        session.end_step(turn, step)
        session.end_turn(turn, "error")
        messages = session.derive_messages("SYS")
        self.assertEqual([message["role"] for message in messages], ["system", "user"])

    def test_summary_shadows_old_surface_and_preserves_recent(self):
        session = Session(record(), [])
        turn = session.start_turn()
        session.add_user("旧消息")
        old = session.start_step(turn)
        session.add_assistant(turn, old, "旧回复")
        session.end_step(turn, old)
        session.end_turn(turn, "completed")
        shadowed = [event.seq for event in session.events]
        session.append(EventType.COMPACTION_SUMMARY, {
            "turn": 1, "summary": "玩家之前说过旧消息。",
            "shadowed_seqs": shadowed, "tokens_before": 100, "tokens_after": 20,
        })
        turn2 = session.start_turn()
        session.add_user("新消息")
        session.end_turn(turn2, "cancelled")
        messages = session.derive_messages("SYS")
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("旧消息", messages[1]["content"])
        self.assertEqual(messages[2]["content"], "新消息")
        self.assertNotIn("旧消息", str(messages[2:]))

    def test_repair_closes_open_step_and_turn(self):
        session = Session(record(), [])
        turn = session.start_turn()
        session.add_user("崩溃前")
        session.start_step(turn)
        repaired = session.repair()
        self.assertGreaterEqual(len(repaired), 2)
        self.assertEqual(repaired[-1].type, EventType.TURN_END)
        messages = session.derive_messages("SYS")
        self.assertEqual([message["role"] for message in messages], ["system", "user"])

    def test_latest_system_and_summary_are_used(self):
        session = Session(record(), [])
        session.add_system("old", "1")
        session.add_system("new", "2")
        self.assertEqual(session.derive_messages("fallback")[0]["content"], "new")


class TokenAndContextTests(unittest.TestCase):
    def test_cjk_is_not_underestimated(self):
        self.assertGreaterEqual(estimate_text("你好世界"), 4)
        self.assertLessEqual(estimate_text("hello world"), estimate_text("你好世界"))

    def test_world_snapshot_is_compact_and_bounded(self):
        snapshot = {
            "timestamp": 1, "availability": {"player": "available", "inventory": "available"},
            "player": {"health": 20, "food": 18, "position": {"x": 1, "y": 2, "z": 3}},
            "inventory": [{"name": f"item_{index}", "count": 1} for index in range(300)],
            "nearbyEntities": [{"name": "zombie", "category": "hostile", "distance": 2.0}],
            "nearbyBlocks": [{"name": "stone", "position": {"x": 1, "y": 1, "z": 1}, "distance": 1.0}],
        }
        text = compact_world_snapshot(snapshot, max_chars=500)
        self.assertLessEqual(len(text), 520)
        self.assertIn("health", text)
        self.assertNotIn("item_299", text)

    def test_system_prompt_is_stable(self):
        self.assertEqual(compose_system_prompt("A"), compose_system_prompt("A"))
        self.assertIn("压缩摘要", compose_system_prompt("A"))


class StoreTests(unittest.TestCase):
    def test_sqlite_round_trip_and_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.sqlite3"
            store = SQLiteEventStore(path)
            store.open()
            record = store.create_session("Steve", now=1.0)
            session = Session(record, [])
            session.append(EventType.USER_MESSAGE, {"text": "hello"}, now=2.0)
            store.append(record.id, session.events, now=2.0)
            store.close()

            reopened = SQLiteEventStore(path)
            reopened.open()
            latest = reopened.latest_session("steve")
            self.assertIsNotNone(latest)
            self.assertEqual(latest.id, record.id)
            self.assertEqual([event.data["text"] for event in reopened.load_events(record.id)], ["hello"])
            reopened.delete_session(record.id)
            self.assertIsNone(reopened.latest_session("Steve"))
            reopened.close()

    def test_sequence_mismatch_is_rejected(self):
        store = InMemoryEventStore()
        store.open()
        record = store.create_session("Steve")
        session = Session(record, [])
        session.append(EventType.USER_MESSAGE, {"text": "one"})
        store.append(record.id, session.events)
        session.append(EventType.USER_MESSAGE, {"text": "two"})
        with self.assertRaises(Exception):
            store.append(record.id, [session.events[-1].model_copy(update={"seq": 99})])
        store.close()


class CompactionTests(unittest.IsolatedAsyncioTestCase):
    def long_session(self, turns=8):
        session = Session(record(), [])
        for index in range(turns):
            turn = session.start_turn(now=index)
            session.add_user("这是一段需要压缩的较早历史。" * 20, now=index)
            step = session.start_step(turn, now=index)
            session.add_assistant(turn, step, "收到，继续执行任务。" * 20, now=index)
            session.end_step(turn, step, now=index)
            session.end_turn(turn, "completed", now=index)
        current = session.start_turn(now=turns)
        session.add_user("现在继续", now=turns)
        return session, current

    async def test_compaction_replaces_old_turns_and_keeps_recent(self):
        session, current = self.long_session()
        messages = session.derive_messages("SYS")
        policy = CompactionPolicy(context_window=2000, threshold_ratio=0.5, retain_turns=2,
                                  max_summary_chars=1200)
        result = await compact_if_needed_async(
            session, policy, messages, [], current, DeterministicSummarizer())
        self.assertIsNotNone(result)
        self.assertGreater(len(session.shadowed_seqs()), 0)
        self.assertIn("对话压缩摘要", session.summary_text())
        projected = session.derive_messages("SYS")
        self.assertIn("compacted-summary", projected[1]["content"])
        self.assertLess(len(projected), len(messages))

    async def test_no_compaction_below_threshold(self):
        session = Session(record(), [])
        turn = session.start_turn()
        session.add_user("hi")
        session.end_turn(turn, "completed")
        current = session.start_turn()
        session.add_user("again")
        result = await compact_if_needed_async(
            session, CompactionPolicy(context_window=100_000), session.derive_messages("SYS"),
            [], current, DeterministicSummarizer())
        self.assertIsNone(result)

    async def test_failed_summary_is_closed_without_partial_summary(self):
        class Broken:
            name = "broken"

            async def summarize(self, transcript, *, max_chars):
                raise RuntimeError("boom")

        session, current = self.long_session(7)
        result = await compact_if_needed_async(
            session, CompactionPolicy(context_window=2000, threshold_ratio=0.5, retain_turns=1),
            session.derive_messages("SYS"), [], current, Broken())
        self.assertIsNone(result)
        self.assertEqual(session.summary_text(), "")
        self.assertEqual(session.events[-1].type, EventType.COMPACTION_END)
        self.assertIsNotNone(session.events[-1].data["error"])


class ManagerTests(unittest.IsolatedAsyncioTestCase):
    async def manager(self, **config):
        store = InMemoryEventStore()
        manager = MemoryManager(store, MemoryConfig(**config), DeterministicSummarizer())
        await manager.startup()
        self.addAsyncCleanup(manager.close)
        return manager

    async def test_history_survives_across_turns(self):
        manager = await self.manager()
        first = await manager.begin_turn("Steve", "我叫 Alice")
        await manager.build_context(first, "SYS", {"player": {"health": 20}}, [])
        await manager.start_step(first)
        await manager.record_assistant(first, "你好 Alice")
        await manager.end_turn(first, "completed")

        second = await manager.begin_turn("Steve", "我叫什么？")
        messages = await manager.build_context(second, "SYS", {"player": {"health": 20}}, [])
        await manager.end_turn(second, "cancelled")
        self.assertEqual([message["role"] for message in messages[:3]], ["system", "user", "assistant"])
        self.assertEqual(messages[1]["content"], "我叫 Alice")
        self.assertEqual(messages[2]["content"], "你好 Alice")

    async def test_mid_turn_steering_is_recorded_as_user_message(self):
        manager = await self.manager()
        first = await manager.begin_turn("Steve", "先去挖石头")
        await manager.record_user(first, "先停一下")
        await manager.end_turn(first, "cancelled")
        second = await manager.begin_turn("Steve", "继续")
        messages = await manager.build_context(second, "SYS", {}, [])
        await manager.end_turn(second, "cancelled")
        users = [message["content"] for message in messages if message["role"] == "user"]
        self.assertIn("先停一下", users)

    async def test_players_are_isolated(self):
        manager = await self.manager()
        first = await manager.begin_turn("Steve", "Steve 的秘密")
        await manager.end_turn(first, "cancelled")
        second = await manager.begin_turn("Alex", "Alex 的消息")
        messages = await manager.build_context(second, "SYS", {}, [])
        await manager.end_turn(second, "cancelled")
        self.assertNotIn("Steve 的秘密", str(messages))
        self.assertEqual(messages[1]["content"], "Alex 的消息")

    async def test_reset_removes_session(self):
        manager = await self.manager()
        turn = await manager.begin_turn("Steve", "记住我")
        await manager.end_turn(turn, "cancelled")
        await manager.reset("Steve")
        turn = await manager.begin_turn("Steve", "新会话")
        messages = await manager.build_context(turn, "SYS", {}, [])
        await manager.end_turn(turn, "cancelled")
        self.assertEqual(messages[1]["content"], "新会话")

    async def test_memory_degradation_does_not_break_tool_loop(self):
        class BrokenMemory:
            async def begin_turn(self, *args, **kwargs):
                raise RuntimeError("disk down")

        settings = replace(load_settings({}), llm_api_key="unit-secret", llm_model="mock",
                           tool_calling_enabled=True)
        llm = AsyncMock()
        llm.tool_completion.return_value = {"content": "仍然回复"}
        client = AsyncMock()
        client.catalog.return_value = ({}, 1)
        client.request.return_value = {}
        loop = ToolLoop(settings, llm, client, memory=BrokenMemory())
        result = await loop.run(MessageRequest(player="Steve", message="hi"))
        self.assertEqual(result["reply"], "仍然回复")


class ToolLoopMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_turn_sees_first_turn_history(self):
        settings = replace(load_settings({}), llm_api_key="unit-secret", llm_model="mock",
                           tool_calling_enabled=True)
        manager = MemoryManager(InMemoryEventStore(), MemoryConfig(), DeterministicSummarizer())
        await manager.startup()
        self.addAsyncCleanup(manager.close)
        llm = AsyncMock()
        llm.tool_completion.side_effect = [{"content": "你好 Alice"}, {"content": "你叫 Alice"}]
        client = AsyncMock()
        client.catalog.return_value = ({}, 1)
        client.request.return_value = {"player": {"health": 20}}
        loop = ToolLoop(settings, llm, client, memory=manager)
        from app.intent import MessageRequest
        await loop.run(MessageRequest(player="Steve", message="我叫 Alice"))
        await loop.run(MessageRequest(player="Steve", message="我叫什么"))
        second = llm.tool_completion.await_args.args[0]
        self.assertIn({"role": "user", "content": "我叫 Alice"}, second)
        self.assertIn({"role": "assistant", "content": "你好 Alice"}, second)
        self.assertTrue(any("<speaker>Steve</speaker>" in str(message.get("content", ""))
                            for message in second))


class AutoCompactionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_loop_receives_compacted_checkpoint(self):
        settings = replace(load_settings({}), llm_api_key="unit-secret", llm_model="mock",
                           tool_calling_enabled=True)
        manager = MemoryManager(
            InMemoryEventStore(),
            MemoryConfig(context_window=900, threshold_ratio=0.5, retain_turns=1, max_summary_chars=600),
            DeterministicSummarizer(),
        )
        await manager.startup()
        self.addAsyncCleanup(manager.close)
        for index in range(5):
            turn = await manager.begin_turn("Steve", f"第{index}轮：" + "很长的事情。" * 30)
            await manager.build_context(turn, "SYS", {}, [])
            await manager.start_step(turn)
            await manager.record_assistant(turn, "收到。" * 30)
            await manager.end_turn(turn, "completed")
        self.assertTrue(manager._sessions["steve"].summary_text())

        llm = AsyncMock()
        llm.tool_completion.return_value = {"content": "我记得。"}
        client = AsyncMock()
        client.catalog.return_value = ({}, 1)
        client.request.return_value = {}
        loop = ToolLoop(settings, llm, client, memory=manager)
        result = await loop.run(MessageRequest(player="Steve", message="继续"))
        self.assertEqual(result["reply"], "我记得。")
        messages = llm.tool_completion.await_args.args[0]
        self.assertTrue(any("compacted-summary" in str(message.get("content", "")) for message in messages))


    async def test_tool_result_is_remembered_in_next_turn(self):
        settings = replace(load_settings({}), llm_api_key="unit-secret", llm_model="mock",
                           tool_calling_enabled=True)
        manager = MemoryManager(InMemoryEventStore(), MemoryConfig(), DeterministicSummarizer())
        await manager.startup()
        self.addAsyncCleanup(manager.close)
        llm = AsyncMock()
        llm.tool_completion.side_effect = [
            {"tool_calls": [{"id": "call1", "type": "function",
                             "function": {"name": "query_status", "arguments": "{}"}}]},
            {"content": "你满血。"},
            {"content": "刚才查询显示你满血。"},
        ]
        client = AsyncMock()
        empty = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
        client.catalog.return_value = ({"query_status": {"name": "query_status", "description": "状态",
                                                         "parameters": empty}}, 1)
        client.request.return_value = {"player": {"health": 20}}
        client.execute.return_value = {"success": True, "tool": "query_status",
                                       "data": {"status": "completed", "health": 20}, "error": None}
        loop = ToolLoop(settings, llm, client, memory=manager)
        await loop.run(MessageRequest(player="Steve", message="查看状态"))
        await loop.run(MessageRequest(player="Steve", message="刚才怎么样"))
        history = llm.tool_completion.await_args.args[0]
        self.assertTrue(any(message.get("role") == "tool" and message.get("tool_call_id") == "call1"
                            for message in history))
        self.assertTrue(any(message.get("role") == "assistant" and message.get("tool_calls")
                            for message in history))


class SummarizeClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_summarize_uses_no_tools_and_returns_content(self):
        import httpx
        from dataclasses import replace as dc_replace
        settings = dc_replace(load_settings({}), llm_api_key="unit-secret", llm_model="mock")
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(200, json={"choices": [{"message": {"content": "摘要"},
                                                          "finish_reason": "stop"}]})

        text = await LLMClient(settings, httpx.MockTransport(respond)).summarize("历史", max_tokens=400)
        self.assertEqual(text, "摘要")
        payload = __import__("json").loads(calls[0].content)
        self.assertNotIn("tools", payload)
        self.assertEqual(payload["max_tokens"], 400)
