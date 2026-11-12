import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import AsyncMock, patch

from app import check_tool_calling
from test_llm_client import llm_settings


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_protocol_probe_never_executes_game_and_checks_arguments(self):
        for arguments, expected in [("{}", True), ('{"code":"evil"}', False)]:
            client = AsyncMock()
            client._request.return_value = {"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [{
                "id": "probe1", "type": "function", "function": {"name": "protocol_probe", "arguments": arguments}
            }]}}]}
            with patch.object(check_tool_calling, "load_settings", return_value=llm_settings()), patch.object(check_tool_calling, "LLMClient", return_value=client), redirect_stdout(StringIO()) as output:
                self.assertEqual(await check_tool_calling.check(), expected)
            payload = client._request.await_args.args[0]
            self.assertEqual(payload["tool_choice"]["function"]["name"], "protocol_probe")
            self.assertNotIn("unit-test-secret", output.getvalue())

    async def test_missing_key_does_not_call_provider(self):
        from dataclasses import replace
        with patch.object(check_tool_calling, "load_settings", return_value=replace(llm_settings(), llm_api_key="")), patch.object(check_tool_calling, "LLMClient") as factory, redirect_stdout(StringIO()):
            self.assertFalse(await check_tool_calling.check())
            factory.assert_not_called()

    async def test_probe_accepts_same_gateway_metadata_as_game_loop(self):
        client = AsyncMock()
        client._request.return_value = {"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [{
            "id": "probe:call.0", "index": 0, "type": "function", "function": {"name": "protocol_probe", "arguments": {}}
        }]}}]}
        with patch.object(check_tool_calling, "load_settings", return_value=llm_settings()), patch.object(check_tool_calling, "LLMClient", return_value=client), redirect_stdout(StringIO()):
            self.assertTrue(await check_tool_calling.check())
