import json
import unittest
from unittest.mock import patch

import httpx

from app import main
from app.llm_client import LLMClient
from app.message_handler import MessageHandler
from test_llm_client import llm_settings, completion


class AgentApiTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, handler, payload):
        with patch.object(main, "message_handler", handler):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                return await client.post("/api/agent/message", json=payload)

    async def test_route_through_real_handler_to_mock_llm(self):
        settings = llm_settings()
        transport = httpx.MockTransport(lambda _: httpx.Response(200, json=completion(json.dumps({
            "intent": "goto", "arguments": {"x": 100, "y": 64, "z": 200}, "reply": "准备前往"
        }))))
        handler = MessageHandler(settings, LLMClient(settings, transport))
        response = await self.request(handler, {"player": "Steve", "message": "去坐标 100 64 200"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["data"]["intent"], "goto")
        self.assertIn("尚未执行", body["data"]["reply"])
        self.assertIsNone(body["error"])

    async def test_invalid_input_and_invalid_model_json(self):
        settings = llm_settings()
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(200, json=completion("invalid JSON"))

        handler = MessageHandler(settings, LLMClient(settings, httpx.MockTransport(respond)))
        for payload in [{"player": "Steve", "message": "x" * 501}, {"player": "Steve", "message": "hi", "execute": True}]:
            response = await self.request(handler, payload)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(set(response.json()), {"success", "data", "error"})
        self.assertEqual(len(calls), 0)
        response = await self.request(handler, {"player": "Steve", "message": "你好"})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "LLM_INVALID_INTENT")

    async def test_missing_key_rate_limit_and_health_still_works(self):
        from dataclasses import replace
        settings = replace(llm_settings(), llm_api_key="")
        handler = MessageHandler(settings, LLMClient(settings))
        response = await self.request(handler, {"player": "Steve", "message": "hi"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "LLM_NOT_CONFIGURED")
        response = await self.request(handler, {"player": "Steve", "message": "hi"})
        self.assertEqual(response.status_code, 429)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            self.assertEqual((await client.get("/health")).status_code, 200)
