import os
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

import httpx


class StartupTests(unittest.TestCase):
    def test_actual_agent_startup_without_key_preserves_health(self):
        # 只访问本机新进程；显式清空密钥，绝不触发真实模型请求。
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        env = {
            **os.environ, "AGENT_HOST": "127.0.0.1", "AGENT_PORT": str(port),
            "NODE_API_URL": "http://127.0.0.1:1", "NODE_API_TIMEOUT_SECONDS": "1",
            "LLM_API_KEY": "", "LLM_MODEL": "", "LLM_BASE_URL": "https://example.invalid/v1",
            "LLM_TIMEOUT": "1", "LLM_MAX_CONCURRENT": "2", "LLM_REQUESTS_PER_MINUTE": "20",
            "LLM_PLAYER_COOLDOWN": "3", "PYTHONIOENCODING": "utf-8"
        }
        process = subprocess.Popen([sys.executable, "-m", "app.main"], cwd=Path(__file__).resolve().parents[1],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=0.5, trust_env=False) as client:
                deadline = time.monotonic() + 10
                while True:
                    try:
                        health = client.get("/health")
                        break
                    except httpx.RequestError:
                        if process.poll() is not None or time.monotonic() >= deadline:
                            self.fail("Agent 未能启动本地 HTTP 服务。")
                        time.sleep(0.05)
                self.assertEqual(health.status_code, 200)
                response = client.post("/api/agent/message", json={"player": "Steve", "message": "hi"})
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["error"]["code"], "LLM_NOT_CONFIGURED")
                self.assertEqual(client.get("/health").status_code, 200)
        finally:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
