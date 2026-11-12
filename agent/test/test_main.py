import unittest

import httpx

from app import main
from app.node_client import NodeApiError


class FakeNodeClient:
    def __init__(self):
        self.calls = []
        self.error = None

    async def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if self.error:
            raise self.error
        return {"success": True, "data": {"path": path, "payload": payload}, "error": None}


class MainApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original = main.client
        self.client = FakeNodeClient()
        main.client = self.client

    async def asyncTearDown(self):
        main.client = self.original

    async def request(self, method, path, **kwargs):
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    async def test_health_and_forwarded_routes(self):
        response = await self.request("GET", "/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["service"], "ok")
        response = await self.request("GET", "/api/game/world")
        self.assertEqual(response.status_code, 200)
        response = await self.request("POST", "/api/game/follow", json={"username": "Steve"})
        self.assertEqual(response.status_code, 200)
        response = await self.request("POST", "/api/game/goto", json={"username": "Steve", "x": 1, "y": 64, "z": 2})
        self.assertEqual(response.status_code, 200)
        response = await self.request("POST", "/api/game/stop", json={"username": "Steve"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([call[1] for call in self.client.calls], ["/api/world", "/api/movement/follow", "/api/movement/goto", "/api/movement/stop"])

    async def test_validation_and_node_errors(self):
        response = await self.request("POST", "/api/game/follow", json={"username": "bad name"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["success"], False)
        self.assertEqual(response.json()["error"]["code"], "INVALID_ARGUMENT")
        self.client.error = NodeApiError(503, "NODE_UNAVAILABLE", "Node.js 服务不可用。")
        response = await self.request("GET", "/api/game/world")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "NODE_UNAVAILABLE")
        self.client.error = NodeApiError(504, "NODE_TIMEOUT", "Node.js 服务请求超时。")
        response = await self.request("POST", "/api/game/stop", json={"username": "Steve"})
        self.assertEqual(response.status_code, 504)

    async def test_invalid_node_response_is_not_exposed(self):
        self.client.error = NodeApiError(502, "INVALID_NODE_RESPONSE", "Node.js 返回格式无效。")
        response = await self.request("GET", "/api/game/world")
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("Traceback", response.text)
