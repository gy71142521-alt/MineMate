import unittest
from unittest.mock import AsyncMock

import httpx

from app.config import load_settings
from app.node_client import NodeApiError, NodeClient


class FakeResponse:
    def __init__(self, body, status_code=200):
        self.body = body
        self.status_code = status_code

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class FakeClient:
    def __init__(self, response=None, error=None, **_):
        self.response = response
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def request(self, *_args, **_kwargs):
        if self.error:
            raise self.error
        return self.response


class NodeClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_and_invalid_response(self):
        body = {"success": True, "data": {"ok": 1}, "error": None}
        client = NodeClient(load_settings({}), client_factory=lambda **_: FakeClient(FakeResponse(body)))
        self.assertEqual((await client.request("GET", "/api/world"))["data"], {"ok": 1})
        invalid = NodeClient(load_settings({}), client_factory=lambda **_: FakeClient(FakeResponse({"bad": True})))
        with self.assertRaisesRegex(NodeApiError, "格式无效"):
            await invalid.request("GET", "/api/world")

    async def test_connection_and_timeout_errors_are_sanitized(self):
        unavailable = NodeClient(load_settings({}), client_factory=lambda **_: FakeClient(error=httpx.ConnectError("secret host")))
        with self.assertRaisesRegex(NodeApiError, "不可用"):
            await unavailable.request("GET", "/health")
        timeout = NodeClient(load_settings({}), client_factory=lambda **_: FakeClient(error=httpx.ReadTimeout("secret")))
        with self.assertRaisesRegex(NodeApiError, "超时"):
            await timeout.request("GET", "/health")
