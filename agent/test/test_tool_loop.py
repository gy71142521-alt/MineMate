import asyncio
import json
import unittest
import shutil
import subprocess
from pathlib import Path
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx

from app import diagnostics
from app.config import load_settings
from app.intent import MessageRequest
from app.llm_client import LLMClient, LLMError
from app.message_handler import MessageHandler
from app.tool_client import ToolClient, validate, validate_schema
from app.tool_loop import ToolLoop


SCHEMA = {"type": "object", "properties": {"x": {"type": "integer", "minimum": 0, "maximum": 4}}, "required": ["x"], "additionalProperties": False}
DEFINITION = {"name": "demo", "description": "from Node", "parameters": SCHEMA, "mutates": True}
REQUEST = MessageRequest(player="Steve", message="请帮我执行")


def call(name="demo", arguments=None, identity="call1"):
    return {"role": "assistant", "tool_calls": [{"id": identity, "type": "function", "function": {
        "name": name, "arguments": json.dumps({"x": 1} if arguments is None else arguments)
    }}], "content": None}


class ToolLoopTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, responses, **options):
        settings = replace(load_settings({}), llm_api_key="unit-secret", llm_model="mock", tool_calling_enabled=True, **options)
        llm = AsyncMock()
        llm.tool_completion.side_effect = responses
        client = AsyncMock()
        client.catalog.return_value = ({"demo": DEFINITION}, 7)
        client.request.return_value = {"player": {"health": 20}}
        client.execute.return_value = {"success": True, "tool": "demo", "data": {"status": "completed"}, "error": None}
        return ToolLoop(settings, llm, client), llm, client

    async def test_catalog_schema_is_used_and_real_result_returned_to_model(self):
        loop, llm, client = self.fixture([call(), {"content": "完成了。"}])
        result = await loop.run(REQUEST)
        self.assertEqual(result["reply"], "完成了。")
        args = client.execute.await_args.args
        self.assertEqual(args[:3], ("demo", {"x": 1}, "Steve"))
        self.assertEqual(args[-1], 7)
        self.assertEqual(llm.tool_completion.await_args.args[1][0]["function"], {key: DEFINITION[key] for key in ("name", "description", "parameters")})
        history = llm.tool_completion.await_args.args[0]
        self.assertEqual(history[-1]["role"], "tool")
        self.assertEqual(history[-1]["tool_call_id"], "call1")
        self.assertEqual(json.loads(history[-1]["content"]), client.execute.return_value)

    async def test_runtime_resource_resolution_is_returned_before_resource_action(self):
        resolver = {"type": "object", "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 64},
            "kind": {"type": "string", "enum": ["item", "block", "any"]}},
            "required": ["query", "kind"], "additionalProperties": False}
        craft = {"type": "object", "properties": {
            "item_name": {"type": "string", "minLength": 1, "maxLength": 64}},
            "required": ["item_name"], "additionalProperties": False}
        loop, llm, client = self.fixture([
            call("resolve_resource", {"query": "iron helmet", "kind": "item"}),
            call("craft_item", {"item_name": "iron_helmet"}, identity="call2"),
            {"content": "已确认目标。"},
        ])
        client.catalog.return_value = ({
            "resolve_resource": {"name": "resolve_resource", "description": "resolve", "parameters": resolver},
            "craft_item": {"name": "craft_item", "description": "craft", "parameters": craft}}, 7)
        client.execute.side_effect = [
            {"success": True, "tool": "resolve_resource", "data": {"status": "completed", "candidates": [{"name": "iron_helmet"}], "ambiguous": False}, "error": None},
            {"success": True, "tool": "craft_item", "data": {"status": "completed"}, "error": None},
        ]
        await loop.run(MessageRequest(player="Steve", message="合成铁头盔"))
        self.assertEqual([item.args[0] for item in client.execute.await_args_list], ["resolve_resource", "craft_item"])
        self.assertEqual(client.execute.await_args_list[1].args[1]["item_name"], "iron_helmet")

    async def test_movement_modes_then_follow_use_boolean_arguments_and_real_results(self):
        loop, llm, client = self.fixture([
            call("set_sprint", {"enabled": True}),
            call("set_sneak", {"enabled": False}, identity="call2"),
            call("follow_player", {}, identity="call3"),
            {"content": "跟随已启动。"},
        ])
        mode = {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"], "additionalProperties": False}
        empty = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
        client.catalog.return_value = ({name: {"name": name, "description": "移动", "parameters": schema} for name, schema in [
            ("set_sprint", mode), ("set_sneak", mode), ("follow_player", empty)]}, 7)
        client.execute.side_effect = [
            {"success": True, "tool": name, "data": {"status": status}, "error": None}
            for name, status in [("set_sprint", "completed"), ("set_sneak", "completed"), ("follow_player", "pending")]
        ]
        result = await loop.run(REQUEST)
        self.assertIn("没结束", result["reply"])
        self.assertEqual([item.args[:2] for item in client.execute.await_args_list], [
            ("set_sprint", {"enabled": True}), ("set_sneak", {"enabled": False}), ("follow_player", {})])
        client.cancel.assert_not_awaited()
        self.assertEqual(llm.tool_completion.await_count, 4)

    async def test_boolean_tool_parameters_reject_strings_numbers_and_extra_controls(self):
        schema = {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"], "additionalProperties": False}
        self.assertTrue(validate_schema(schema))
        for value in [True, False]:
            self.assertTrue(validate(schema, {"enabled": value}))
        for arguments in [{"enabled": 1}, {"enabled": 0}, {"enabled": "true"}, {"enabled": None}, {"enabled": True, "forward": True}, {}]:
            self.assertFalse(validate(schema, arguments))
            loop, _, client = self.fixture([call("set_sprint", arguments)])
            client.catalog.return_value = ({"set_sprint": {"name": "set_sprint", "description": "移动", "parameters": schema}}, 7)
            with self.assertRaises(LLMError) as error:
                await loop.run(REQUEST)
            self.assertEqual(error.exception.code, "INVALID_TOOL_CALL")
            client.execute.assert_not_awaited()

    async def test_arrived_navigation_can_continue_to_attack_in_same_request(self):
        loop, llm, client = self.fixture([
            call("goto_position", {"x": 14, "y": 72, "z": 7}),
            call("attack_entity", {"entity_id": 2}, identity="call2"),
            {"content": "已观察到僵尸受伤。"},
        ])
        coordinates = {axis: {"type": "number", "minimum": -100, "maximum": 100} for axis in "xyz"}
        client.catalog.return_value = ({
            "goto_position": {"name": "goto_position", "description": "导航", "parameters": {"type": "object", "properties": coordinates, "required": list("xyz"), "additionalProperties": False}},
            "attack_entity": {"name": "attack_entity", "description": "攻击", "parameters": {"type": "object", "properties": {"entity_id": {"type": "integer", "minimum": 0, "maximum": 100}}, "required": ["entity_id"], "additionalProperties": False}},
        }, 7)
        client.execute.side_effect = [
            {"success": True, "tool": "goto_position", "data": {"status": "completed", "movement": {"status": "arrived", "taskId": 42}}, "error": None},
            {"success": True, "tool": "attack_entity", "data": {"status": "completed", "damageObserved": True, "deathObserved": False}, "error": None},
        ]
        result = await loop.run(REQUEST)
        self.assertIn("受伤", result["reply"])
        self.assertEqual([args.args[0] for args in client.execute.await_args_list], ["goto_position", "attack_entity"])
        self.assertEqual(llm.tool_completion.await_count, 3)

    async def test_mining_uses_real_block_query_then_selected_tool(self):
        loop, llm, client = self.fixture([
            call("inspect_block", {"position": {"x": 14, "y": 73, "z": 7}}),
            call("mine_block", {"position": {"x": 14, "y": 73, "z": 7}, "block_name": "oak_log", "tool_name": "diamond_axe"}, identity="call2"),
            {"content": "目标原木已挖除。"},
        ])
        position = {"type": "object", "properties": {axis: {"type": "integer", "minimum": -100, "maximum": 100} for axis in "xyz"}, "required": list("xyz"), "additionalProperties": False}
        text = {"type": "string", "minLength": 1, "maxLength": 64}
        client.catalog.return_value = ({
            "inspect_block": {"name": "inspect_block", "description": "查询", "parameters": {"type": "object", "properties": {"position": position}, "required": ["position"], "additionalProperties": False}},
            "mine_block": {"name": "mine_block", "description": "挖掘", "parameters": {"type": "object", "properties": {"position": position, "block_name": text, "tool_name": text}, "required": ["position", "block_name"], "additionalProperties": False}},
        }, 7)
        client.execute.side_effect = [
            {"success": True, "tool": "inspect_block", "data": {"status": "completed", "name": "oak_log"}, "error": None},
            {"success": True, "tool": "mine_block", "data": {"status": "completed", "blockRemoved": True}, "error": None},
        ]
        await loop.run(REQUEST)
        self.assertEqual(client.execute.await_args_list[1].args[1]["tool_name"], "diamond_axe")
        self.assertEqual([args.args[0] for args in client.execute.await_args_list], ["inspect_block", "mine_block"])
        prompt = llm.tool_completion.await_args.args[0][0]["content"]
        self.assertIn("inspect_placement 仅用于放置", prompt)

    async def test_failed_tool_reply_preserves_actionable_reason_without_raw_error(self):
        for code, expected in [("CANNOT_CONSUME", "不饿"),
                               ("NO_LINE_OF_SIGHT", "挡住"), ("OCCUPIED", "已经有东西"), ("NAVIGATION_FAILED", "没走过去")]:
            loop, _, client = self.fixture([call(), {"content": "已经完成了。"}])
            client.execute.return_value = {"success": False, "tool": "demo", "data": None, "error": {"code": code, "message": "unit-secret"}}
            result = await loop.run(REQUEST)
            self.assertIn(expected, result["reply"])
            self.assertNotIn("unit-secret", result["reply"])
            self.assertNotIn("已经完成", result["reply"])

    async def test_gateway_metadata_and_decoded_arguments_preserve_call_identity(self):
        response = call(identity="call/functions.follow_player:0")
        response["tool_calls"][0]["index"] = 0
        response["tool_calls"][0]["function"]["arguments"] = {"x": 1}
        loop, llm, client = self.fixture([response, {"content": "操作已启动。"}])
        client.execute.return_value = {"success": True, "tool": "demo", "data": {"status": "pending"}, "error": None}
        result = await loop.run(REQUEST)
        self.assertIn("没结束", result["reply"])
        self.assertEqual(client.execute.await_args.args[:3], ("demo", {"x": 1}, "Steve"))
        history = llm.tool_completion.await_args.args[0]
        wire_call = history[-2]["tool_calls"][0]
        self.assertNotIn("index", wire_call)
        self.assertEqual(json.loads(wire_call["function"]["arguments"]), {"x": 1})
        self.assertEqual(history[-1]["tool_call_id"], "call/functions.follow_player:0")
        client.cancel.assert_not_awaited()

    async def test_gateway_format_compatibility_does_not_relax_tools_or_schema(self):
        for name, arguments in [("shell", {}), ("demo", {"x": True}), ("demo", {"x": 9}),
                                ("demo", {"x": 1, "username": "Other"}), ("demo", {"x": float("nan")})]:
            response = call(name)
            response["tool_calls"][0]["index"] = 0
            response["tool_calls"][0]["function"]["arguments"] = arguments
            loop, _, client = self.fixture([response])
            with self.assertRaises(LLMError):
                await loop.run(REQUEST)
            client.execute.assert_not_awaited()

    async def test_invalid_call_metadata_is_rejected_with_safe_field_diagnostics(self):
        cases = [({"id": ""}, "ID:EMPTY"), ({"id": "unit-secret\n"}, "ID:INVALID"),
                 ({"id": "x" * 257}, "ID:TOO_LONG"), ({"index": 8}, "INDEX:RANGE"),
                 ({"index": True}, "INDEX:TYPE"), ({"unit-secret": "sensitive"}, "OTHER:EXTRA_FIELD"),
                 ({"function": {"name": "demo", "arguments": None}}, "FUNCTION_ARGUMENTS:TYPE"),
                 ({"function": {"name": "functions.demo", "arguments": "{}"}}, "FUNCTION_NAME:PATTERN")]
        for patch, diagnostic in cases:
            response = call()
            response["tool_calls"][0].update(patch)
            loop, _, client = self.fixture([response])
            with self.assertLogs("minemate.tools", level="WARNING") as captured:
                with self.assertRaises(LLMError):
                    await loop.run(REQUEST)
            logs = " ".join(captured.output)
            self.assertIn("reason=TOOL_CALL_FORMAT", logs)
            self.assertIn(diagnostic, logs)
            self.assertNotIn("unit-secret", logs)
            self.assertNotIn("sensitive", logs)
            client.execute.assert_not_awaited()

    async def test_unregistered_wrong_parameters_duplicates_and_parallel_rejected(self):
        for response in [call("shell"), call(arguments={"x": True}), call(arguments={"x": 9}), call(arguments={"x": 1, "code": "evil"}),
                         {"tool_calls": call()["tool_calls"] * 2}]:
            loop, _, client = self.fixture([response])
            with self.assertRaises(LLMError) as error:
                await loop.run(REQUEST)
            self.assertEqual(error.exception.code, "INVALID_TOOL_CALL")
            client.execute.assert_not_awaited()
        response = call()
        response["tool_calls"][0]["function"]["arguments"] = '{"x":1,"x":2}'
        loop, _, client = self.fixture([response])
        with self.assertRaises(LLMError):
            await loop.run(REQUEST)
        client.execute.assert_not_awaited()

    async def test_failures_and_pending_cannot_be_reported_as_success_or_retried(self):
        for status in ("pending", "uncertain", "failed"):
            loop, llm, client = self.fixture([call(), {"content": "已全部完成。"}])
            client.execute.return_value = {"success": status != "failed", "tool": "demo", "data": {"status": status} if status != "failed" else None,
                                           "error": None if status != "failed" else {"code": "MISSING", "message": "missing"}}
            result = await loop.run(REQUEST)
            self.assertNotIn("已全部完成", result["reply"])
            self.assertTrue(llm.tool_completion.await_args.kwargs["final"])
            self.assertEqual(client.execute.await_count, 1)

    async def test_wrong_table_reply_explains_craft_position(self):
        loop, _, client = self.fixture([call("craft_item", {}), {"content": "已经完成。"}])
        empty = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
        client.catalog.return_value = ({"craft_item": {"name": "craft_item", "description": "合成",
                                                        "parameters": empty}}, 7)
        client.execute.return_value = {"success": False, "tool": "craft_item", "data": None,
                                       "error": {"code": "WRONG_TABLE", "message": "position 必须是已有工作台。"}}
        result = await loop.run(REQUEST)
        self.assertIn("工作台", result["reply"])
        self.assertIn("另外找地方", result["reply"])
        self.assertNotIn("已经完成", result["reply"])

    async def test_item_entity_missing_reply_explains_auto_pickup(self):
        loop, _, client = self.fixture([call("collect_item", {}), {"content": "已经完成。"}])
        empty = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
        client.catalog.return_value = ({"collect_item": {"name": "collect_item", "description": "拾取",
                                                          "parameters": empty}}, 7)
        client.execute.return_value = {"success": False, "tool": "collect_item", "data": None,
                                       "error": {"code": "ITEM_ENTITY_MISSING", "message": "范围内没有掉落物。"}}
        result = await loop.run(REQUEST)
        self.assertIn("捡起来", result["reply"])
        self.assertNotIn("已经完成", result["reply"])

    async def test_call_count_limit_and_duplicate_call_id(self):
        loop, llm, client = self.fixture([call(), call(identity="call2")], tool_max_calls=1)
        with self.assertRaises(LLMError) as error:
            await loop.run(REQUEST)
        self.assertEqual(error.exception.code, "TOOL_CALL_LIMIT")
        self.assertEqual(client.execute.await_count, 1)
        # 相同 tool_call_id 是协议级重复；相同参数但不同 ID 允许成功后再执行（如连续跳跃）。
        loop, _, client = self.fixture([call(), call(identity="call1")])
        with self.assertRaises(LLMError) as error:
            await loop.run(REQUEST)
        self.assertEqual(error.exception.code, "INVALID_TOOL_CALL")
        self.assertEqual(client.execute.await_count, 1)

    async def test_repeated_successful_tool_calls_are_allowed_across_turns(self):
        jump = call("jump", {}, identity="call1")
        jump_again = call("jump", {}, identity="call2")
        loop, llm, client = self.fixture([
            jump,
            jump_again,
            {"content": "跳了两下。"},
        ])
        empty = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
        client.catalog.return_value = ({"jump": {"name": "jump", "description": "跳", "parameters": empty}}, 7)
        client.execute.side_effect = [
            {"success": True, "tool": "jump", "data": {"status": "completed"}, "error": None},
            {"success": True, "tool": "jump", "data": {"status": "completed"}, "error": None},
        ]
        result = await loop.run(REQUEST)
        self.assertEqual(result["reply"], "跳了两下。")
        self.assertEqual(client.execute.await_count, 2)
        self.assertEqual([item.args[0] for item in client.execute.await_args_list], ["jump", "jump"])

    async def test_repeated_successful_tool_calls_are_allowed_in_one_batch(self):
        first = call("jump", {}, identity="call1")
        second = call("jump", {}, identity="call2")
        second["tool_calls"][0]["index"] = 1
        loop, llm, client = self.fixture([
            {"tool_calls": first["tool_calls"] + second["tool_calls"]},
            {"content": "跳了两下。"},
        ])
        empty = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
        client.catalog.return_value = ({"jump": {"name": "jump", "description": "跳", "parameters": empty}}, 7)
        client.execute.side_effect = [
            {"success": True, "tool": "jump", "data": {"status": "completed"}, "error": None},
            {"success": True, "tool": "jump", "data": {"status": "completed"}, "error": None},
        ]
        result = await loop.run(REQUEST)
        self.assertEqual(result["reply"], "跳了两下。")
        self.assertEqual(client.execute.await_count, 2)

    async def test_total_timeout_and_task_cancellation_cancel_node(self):
        for cancel in (False, True):
            loop, _, client = self.fixture([call()], tool_calling_timeout=0.02)
            entered = asyncio.Event()
            async def waiting(*_):
                entered.set()
                await asyncio.Event().wait()
            client.execute.side_effect = waiting
            task = asyncio.create_task(loop.run(REQUEST))
            await entered.wait()
            if cancel:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            else:
                with self.assertRaises(LLMError) as error:
                    await task
                self.assertEqual(error.exception.code, "TOOL_LOOP_TIMEOUT")
            client.cancel.assert_awaited_once_with("Steve", client.execute.await_args.args[3])

    async def test_invalid_final_reply_cancels_pending_action(self):
        loop, _, client = self.fixture([call(), {"content": None}])
        client.execute.return_value = {"success": True, "tool": "demo", "data": {"status": "pending"}, "error": None}
        with self.assertRaises(LLMError) as error:
            await loop.run(REQUEST)
        self.assertEqual(error.exception.code, "INVALID_TOOL_CALL")
        client.cancel.assert_awaited_once_with("Steve", client.execute.await_args.args[3])

    async def test_model_coordinates_are_not_leaked_to_player(self):
        loop, _, client = self.fixture([{"content": "我到了 189, 64, 190，挖到木头了。"}])
        result = await loop.run(REQUEST)
        self.assertNotIn("189", result["reply"])
        self.assertNotIn("64", result["reply"])
        self.assertNotIn("190", result["reply"])
        self.assertIn("挖到木头", result["reply"])

    async def test_multiline_or_long_display_reply_does_not_cancel_started_action(self):
        for text in ("操作已启动。\r\n尚未完成。", "这是说明文字" * 110):
            loop, _, client = self.fixture([call(), {"content": text}])
            client.execute.return_value = {"success": True, "tool": "demo", "data": {"status": "pending"}, "error": None}
            result = await loop.run(REQUEST)
            self.assertIn("没结束", result["reply"])
            client.execute.assert_awaited_once()
            client.cancel.assert_not_awaited()
        loop, _, _ = self.fixture([{"content": "第一行\n第二行\t继续"}])
        self.assertEqual((await loop.run(REQUEST))["reply"], "第一行 第二行 继续")
        loop, _, _ = self.fixture([{"content": "a" * 501}])
        self.assertEqual(len((await loop.run(REQUEST))["reply"]), 500)

    async def test_validation_diagnostics_do_not_log_raw_model_content(self):
        bad_json = call()
        bad_json["tool_calls"][0]["function"]["arguments"] = "unit-secret"
        for response, reason in [
            ({"tool_calls": "unit-secret"}, "TOOL_CALL_LIST"),
            (dict(call(), tool_calls=call()["tool_calls"] * 9), "PARALLEL_TOOL_CALLS"),
            (call("unit_secret"), "UNKNOWN_TOOL"),
            (call(arguments={"x": 1, "secret": "unit-secret"}), "ARGUMENTS_SCHEMA"),
            (bad_json, "ARGUMENTS_JSON"),
            ({"content": "```unit-secret```"}, "FINAL_REPLY"),
        ]:
            loop, _, client = self.fixture([response])
            with self.assertLogs("minemate.tools", level="WARNING") as captured:
                with self.assertRaises(LLMError) as error:
                    await loop.run(REQUEST)
            self.assertEqual(error.exception.code, "INVALID_TOOL_CALL")
            self.assertIn("reason=" + reason, " ".join(captured.output))
            self.assertNotIn("unit-secret", " ".join(captured.output))
            self.assertNotIn("preview=", " ".join(captured.output))
            client.execute.assert_not_awaited()

    async def test_oversized_batch_is_rejected_with_count_and_names(self):
        response = {"tool_calls": call()["tool_calls"] * 9}
        loop, _, client = self.fixture([response])
        with self.assertLogs("minemate.tools", level="WARNING") as captured:
            with self.assertRaises(LLMError):
                await loop.run(REQUEST)
        logs = " ".join(captured.output)
        self.assertIn("code=PARALLEL_TOOL_CALLS", logs)
        self.assertIn("count=9", logs)
        self.assertIn("limit=8", logs)
        self.assertIn("names=demo,demo,demo,demo,demo,demo", logs)
        client.execute.assert_not_awaited()

    async def test_multiple_valid_calls_execute_sequentially(self):
        first = call(arguments={"x": 1})
        second = call(arguments={"x": 2}, identity="call2")
        second["tool_calls"][0]["index"] = 1
        loop, llm, client = self.fixture([
            {"tool_calls": first["tool_calls"] + second["tool_calls"]},
            {"content": "两个查询都完成了。"},
        ])
        client.execute.side_effect = [
            {"success": True, "tool": "demo", "data": {"status": "completed"}, "error": None},
            {"success": True, "tool": "demo", "data": {"status": "completed"}, "error": None},
        ]
        result = await loop.run(REQUEST)
        self.assertEqual(result["reply"], "两个查询都完成了。")
        self.assertEqual([item.args[:2] for item in client.execute.await_args_list],
                         [("demo", {"x": 1}), ("demo", {"x": 2})])
        history = llm.tool_completion.await_args.args[0]
        assistant = [item for item in history if item["role"] == "assistant"][-1]
        self.assertEqual([item["id"] for item in assistant["tool_calls"]], ["call1", "call2"])
        tool_messages = [item for item in history if item["role"] == "tool"]
        self.assertEqual([item["tool_call_id"] for item in tool_messages], ["call1", "call2"])

    async def test_batch_stops_after_pending_and_marks_remaining_not_executed(self):
        first = call(arguments={"x": 1})
        second = call(arguments={"x": 2}, identity="call2")
        loop, llm, client = self.fixture([
            {"tool_calls": first["tool_calls"] + second["tool_calls"]},
            {"content": "已启动。"},
        ])
        client.execute.side_effect = [
            {"success": True, "tool": "demo", "data": {"status": "pending"}, "error": None},
            {"success": True, "tool": "demo", "data": {"status": "completed"}, "error": None},
        ]
        result = await loop.run(REQUEST)
        self.assertIn("前面那步没弄完", result["reply"])
        self.assertIn("没动", result["reply"])
        self.assertEqual(client.execute.await_count, 1)
        history = llm.tool_completion.await_args.args[0]
        tool_messages = {item["tool_call_id"]: json.loads(item["content"]) for item in history if item["role"] == "tool"}
        self.assertEqual(tool_messages["call1"]["data"]["status"], "pending")
        self.assertEqual(tool_messages["call2"]["error"]["code"], "NOT_EXECUTED")

    async def test_traceback_all_includes_call_shape_preview_and_stack(self):
        diagnostics.set_traceback_mode("all")
        try:
            loop, _, client = self.fixture([{"tool_calls": call()["tool_calls"] * 9}])
            with self.assertLogs("minemate.tools", level="WARNING") as captured:
                with self.assertRaises(LLMError):
                    await loop.run(REQUEST)
        finally:
            diagnostics.set_traceback_mode("errors")
        logs = " ".join(captured.output)
        self.assertIn("code=PARALLEL_TOOL_CALLS", logs)
        self.assertIn("calls=0:id=", logs)
        self.assertIn("preview=", logs)
        self.assertIn('"name": "demo"', logs)
        self.assertIn("Traceback", logs)
        client.execute.assert_not_awaited()

    async def test_schema_diagnostic_reports_missing_and_unexpected_without_values(self):
        loop, _, client = self.fixture([call(arguments={"x": 1, "secret": "unit-secret"})])
        with self.assertLogs("minemate.tools", level="WARNING") as captured:
            with self.assertRaises(LLMError):
                await loop.run(REQUEST)
        logs = " ".join(captured.output)
        self.assertIn("code=ARGUMENTS_SCHEMA", logs)
        self.assertIn("tool=demo", logs)
        self.assertIn("missing=- unexpected=1", logs)
        self.assertNotIn("unit-secret", logs)
        client.execute.assert_not_awaited()

    async def test_invalid_reply_after_pending_still_cancels_with_safe_diagnostic(self):
        loop, _, client = self.fixture([call(), {"content": "```unit-secret```"}])
        client.execute.return_value = {"success": True, "tool": "demo", "data": {"status": "pending"}, "error": None}
        with self.assertLogs("minemate.tools", level="WARNING") as captured:
            with self.assertRaises(LLMError):
                await loop.run(REQUEST)
        client.cancel.assert_awaited_once_with("Steve", client.execute.await_args.args[3])
        self.assertNotIn("unit-secret", " ".join(captured.output))

    async def test_message_handler_switch_preserves_legacy_when_disabled(self):
        loop, llm, _ = self.fixture([{"content": "你好"}])
        handler = MessageHandler(loop.settings, llm, tool_loop=loop)
        self.assertEqual((await handler.handle(REQUEST))["reply"], "你好")
        llm.complete.assert_not_awaited()

    async def test_message_queued_before_stop_cannot_use_new_catalog_epoch(self):
        loop, llm, client = self.fixture([])
        with self.assertRaises(LLMError) as error:
            await loop.run(MessageRequest(player="Steve", message="挖掘指定方块", generation=6))
        self.assertEqual(error.exception.code, "STALE_TASK")
        llm.tool_completion.assert_not_awaited()
        client.execute.assert_not_awaited()

    async def test_llm_structured_function_request_and_unsupported_model(self):
        settings = replace(load_settings({}), llm_api_key="unit-secret", llm_model="mock")
        def respond(request):
            payload = json.loads(request.content)
            self.assertEqual(payload["tools"][0]["function"]["name"], "demo")
            self.assertFalse(payload["parallel_tool_calls"])
            self.assertNotIn("response_format", payload)
            return httpx.Response(200, json={"choices": [{"message": call(), "finish_reason": "tool_calls"}]})
        result = await LLMClient(settings, httpx.MockTransport(respond)).tool_completion([], [{"type": "function", "function": DEFINITION}])
        self.assertEqual(result["tool_calls"][0]["function"]["name"], "demo")
        llm = LLMClient(settings, httpx.MockTransport(lambda _: httpx.Response(400, text="unit-secret")))
        with self.assertRaises(LLMError) as error:
            await llm.tool_completion([], [])
        self.assertEqual(error.exception.code, "LLM_TOOL_CALLING_REJECTED")
        self.assertNotIn("unit-secret", str(error.exception))

    async def test_tool_output_budget_and_truncated_response(self):
        settings = replace(load_settings({}), llm_api_key="unit-secret", llm_model="mock", tool_llm_max_tokens=4096)
        calls = []
        def respond(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": call(), "finish_reason": "length"}]})
        client = LLMClient(settings, httpx.MockTransport(respond))
        with self.assertLogs("minemate.llm", level="WARNING") as captured:
            with self.assertRaises(LLMError) as error:
                await client.tool_completion([], [])
        self.assertEqual(error.exception.code, "LLM_OUTPUT_TRUNCATED")
        self.assertIn("reason=OUTPUT_LIMIT", " ".join(captured.output))
        self.assertNotIn("unit-secret", " ".join(captured.output))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["max_tokens"], 4096)
        # Task 005 的请求不采用工具模式的 token 配置。
        def legacy_respond(request):
            self.assertEqual(json.loads(request.content)["max_tokens"], 512)
            return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]})
        await LLMClient(settings, httpx.MockTransport(legacy_respond)).complete("Steve", "你好")

    async def test_response_diagnostics_are_bounded_and_do_not_include_provider_text(self):
        settings = replace(load_settings({}), llm_api_key="unit-secret", llm_model="mock")
        for body, reason in [
            ({"error": "unit-secret"}, "CHOICES_FORMAT"),
            ({"choices": [False]}, "CHOICES_FORMAT"),
            ({"choices": [{"message": {}}]}, "MISSING_FINISH_REASON"),
            ({"choices": [{"finish_reason": "unit-secret", "message": {}}]}, "UNSUPPORTED_FINISH_REASON"),
            ({"choices": [{"finish_reason": "stop", "message": "unit-secret"}]}, "MESSAGE_FORMAT"),
        ]:
            client = LLMClient(settings, httpx.MockTransport(lambda _: httpx.Response(200, json=body)))
            with self.assertLogs("minemate.llm", level="WARNING") as captured:
                with self.assertRaises(LLMError) as error:
                    await client.tool_completion([], [])
            self.assertEqual(error.exception.code, "LLM_INVALID_RESPONSE")
            self.assertIn("reason=" + reason, " ".join(captured.output))
            self.assertNotIn("unit-secret", " ".join(captured.output))

    async def test_node_catalog_and_invalid_result(self):
        settings = replace(load_settings({}), tool_api_timeout=0.02)
        for body in [{"tools": [DEFINITION, DEFINITION], "generation": 1}, {"tools": [dict(DEFINITION, parameters={})], "generation": 1}]:
            client = ToolClient(settings, httpx.MockTransport(lambda _: httpx.Response(200, json={"success": True, "data": body, "error": None})))
            with self.assertRaises(LLMError):
                await client.catalog()
        for status, body in [(503, {}), (200, {"success": True, "data": {"tool": "wrong"}, "error": None})]:
            client = ToolClient(settings, httpx.MockTransport(lambda _: httpx.Response(status, json=body)))
            with self.assertRaises(LLMError):
                await client.execute("demo", {}, "Steve", "id1", 1)

    @unittest.skipUnless(shutil.which("node"), "需要 Node.js 运行实际工具目录契约测试")
    async def test_python_uses_actual_node_registered_schemas(self):
        program = "const {EventEmitter}=require('node:events'); const {createToolSystem}=require('./src/tools'); const r=createToolSystem(new EventEmitter()); process.stdout.write(JSON.stringify({tools:r.list(),generation:0})); r.dispose();"
        output = subprocess.run(["node", "-e", program], cwd=Path(__file__).resolve().parents[2] / "minecraft-bot", capture_output=True, check=True, timeout=10)
        actual = json.loads(output.stdout)
        client = ToolClient(load_settings({}), httpx.MockTransport(lambda _: httpx.Response(200, json={"success": True, "data": actual, "error": None})))
        catalog, _ = await client.catalog()
        self.assertEqual(list(catalog.values()), actual["tools"])
        self.assertIn("mine_block", catalog)
        self.assertIn("query_status", catalog)
        self.assertIn("smelt_item", catalog)
        for name in ("furnace_add_input", "furnace_add_fuel", "furnace_take_output"):
            self.assertIn(name, catalog)
            schema = catalog[name]["parameters"]
            self.assertEqual(schema["properties"]["count"], {"type": "integer", "minimum": 1, "maximum": 64})
            args = {"position": {"x": 1, "y": 64, "z": 0}, "count": 64}
            if name != "furnace_add_fuel":
                args["item_name"] = "raw_iron" if name == "furnace_add_input" else "iron_ingot"
            self.assertTrue(validate(schema, args))
            self.assertFalse(validate(schema, {**args, "count": 65}))
            self.assertFalse(validate(schema, {**args, "count": True}))
        self.assertIn("jump", catalog)
        for name in ["set_sprint", "set_sneak"]:
            schema = catalog[name]["parameters"]
            self.assertEqual(schema["properties"]["enabled"], {"type": "boolean"})
            self.assertTrue(validate(schema, {"enabled": True}))
            self.assertTrue(validate(schema, {"enabled": False}))
            self.assertFalse(validate(schema, {"enabled": 1}))
            self.assertFalse(validate(schema, {"enabled": "false"}))
        self.assertEqual(len(catalog), len({item["name"] for item in actual["tools"]}))

    async def test_tool_http_timeout_and_connection_errors(self):
        for exception, code in [(httpx.ReadTimeout("unit-secret"), "TOOL_API_TIMEOUT"), (httpx.ConnectError("unit-secret"), "NODE_UNAVAILABLE")]:
            def respond(_):
                raise exception
            client = ToolClient(load_settings({}), httpx.MockTransport(respond))
            with self.assertRaises(LLMError) as error:
                await client.catalog()
            self.assertEqual(error.exception.code, code)
            self.assertNotIn("unit-secret", str(error.exception))

    @unittest.skipUnless(shutil.which("node"), "需要真实 Node 工具目录")
    async def test_furnace_subtools_use_node_schemas_and_return_real_result_to_model(self):
        program = "const {EventEmitter}=require('node:events');const {createToolSystem}=require('./src/tools');const r=createToolSystem(new EventEmitter());process.stdout.write(JSON.stringify(r.list()));r.dispose();"
        output = subprocess.run(["node", "-e", program], cwd=Path(__file__).resolve().parents[2] / "minecraft-bot", capture_output=True, check=True, timeout=10)
        catalog = {d["name"]: d for d in json.loads(output.stdout) if d["name"].startswith("furnace_")}
        at = {"x": 1, "y": 64, "z": 0}
        actions = [("furnace_add_input", {"position": at, "item_name": "raw_iron", "count": 3}),
                   ("furnace_add_fuel", {"position": at}),
                   ("furnace_take_output", {"position": at, "item_name": "iron_ingot"})]
        loop, llm, client = self.fixture([*[call(n, args, identity=f"furnace{i}") for i, (n, args) in enumerate(actions)],
                                          {"content": "已完成投料、添加燃料和取出现有产品。"}])
        client.catalog.return_value = (catalog, 7)
        client.execute.side_effect = [{"success": True, "tool": n, "data": {"status": "completed", "verified": True}, "error": None}
                                      for n, _ in actions]
        result = await loop.run(REQUEST)
        self.assertEqual([a.args[:2] for a in client.execute.await_args_list], actions)
        self.assertIn("现有产品", result["reply"])
        history = llm.tool_completion.await_args.args[0]
        self.assertEqual([json.loads(m["content"])["tool"] for m in history if m["role"] == "tool"], [n for n, _ in actions])
        prompt = history[0]["content"]
        self.assertIn("不要为补充操作重复调用", prompt)

    async def test_furnace_conflict_is_reported_without_retry_or_success_claim(self):
        loop, _, client = self.fixture([call(), {"content": "已经完成了。"}])
        client.execute.return_value = {"success": False, "tool": "demo", "data": None,
                                       "error": {"code": "FURNACE_OUTPUT_CONFLICT", "message": "unit-secret"}}
        result = await loop.run(REQUEST)
        self.assertIn("其它工艺", result["reply"])
        self.assertNotIn("已经完成", result["reply"])
        self.assertNotIn("unit-secret", result["reply"])
        client.execute.assert_awaited_once()

    async def test_http_disconnect_cancels_running_handler(self):
        from unittest.mock import patch
        from app import main
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def handle(_):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        handler = AsyncMock()
        handler.handle.side_effect = handle
        request = AsyncMock()
        request.is_disconnected.return_value = True
        with patch.object(main, "message_handler", handler):
            response = await main.agent_message(REQUEST, request)
        self.assertEqual(response.status_code, 499)
        self.assertTrue(entered.is_set())
        self.assertTrue(cancelled.is_set())
