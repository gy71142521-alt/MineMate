"""启动器只使用临时测试子进程，不启动 Minecraft 或调用模型。"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location("minemate_test_launcher", Path(__file__).resolve().parents[2] / "launcher.py")
launcher = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = launcher
spec.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.logs = []
        self.condition = threading.Condition()

        def log(name, text):
            with self.condition:
                self.logs.append((name, text))
                self.condition.notify_all()

        self.processes = launcher.Processes(log)
        self.addCleanup(self.processes.stop)

    def service(self, name, code):
        return launcher.Service(name, (sys.executable, "-u", "-c", code), Path(__file__).resolve().parent)

    def wait_log(self, predicate):
        with self.condition:
            self.assertTrue(self.condition.wait_for(lambda: predicate(self.logs), timeout=5), self.logs)

    def test_fixed_commands_and_preflight_with_unicode_paths(self):
        with tempfile.TemporaryDirectory(prefix="MineMate 测试 ") as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "agent/.venv"):
                launcher.service_commands(root)
            python = root / "agent/.venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            python.parent.mkdir(parents=True)
            python.touch()
            with patch.object(launcher.shutil, "which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "Node.js"):
                    launcher.service_commands(root)
            with patch.object(launcher.shutil, "which", return_value="node.exe"):
                with self.assertRaisesRegex(RuntimeError, "Node 依赖"):
                    launcher.service_commands(root)
                (root / "minecraft-bot/node_modules/mineflayer").mkdir(parents=True)
                services = launcher.service_commands(root)
            self.assertEqual(services[0].command, (str(python), "-u", "-m", "app.main"))
            self.assertEqual(services[1].command, ("node.exe", "src/index.js"))
            self.assertEqual([service.directory for service in services], [root / "agent", root / "minecraft-bot"])

    def test_real_two_processes_stream_stdout_stderr_and_stop(self):
        # 没有换行的输出也应立即收到；一个中文字符故意分两次写入。
        first = "import os,time; b='中文实时'.encode('utf-8'); os.write(1,b[:2]); time.sleep(.05); os.write(1,b[2:]); os.write(2,b' stderr'); time.sleep(60)"
        self.processes.start([self.service("Python", first), self.service("Node", "import time; print('node-ready',flush=True); time.sleep(60)")])

        def received(logs):
            python = "".join(text for name, text in logs if name == "Python")
            node = "".join(text for name, text in logs if name == "Node")
            return "中文实时 stderr" in python and "node-ready" in node

        self.wait_log(received)
        self.assertEqual(self.processes.snapshot(), {"Python": None, "Node": None})
        with self.assertRaisesRegex(RuntimeError, "已有服务"):
            self.processes.start([self.service("Other", "pass")])
        self.processes.stop()
        self.assertTrue(all(code is not None for code in self.processes.snapshot().values()))
        self.processes.stop()  # 重复停止安全，结束后允许重新启动。
        self.processes.start([self.service("Node", "print('restarted',flush=True)")])
        self.wait_log(lambda logs: any("restarted" in text for _, text in logs))

    def test_partial_start_failure_rolls_back_started_process(self):
        invalid = launcher.Service("Node", (str(Path(tempfile.gettempdir()) / "minemate-nonexistent-executable.exe"),), Path.cwd())
        with self.assertRaisesRegex(RuntimeError, "已停止"):
            self.processes.start([self.service("Python", "import time; time.sleep(60)"), invalid])
        self.assertIsNotNone(self.processes.snapshot()["Python"])

    def test_unexpected_exit_is_logged_with_exit_code(self):
        self.processes.start([self.service("Node", "import sys; print('startup-error',file=sys.stderr); sys.exit(7)")])
        self.wait_log(lambda logs: any("退出码=7" in text for _, text in logs))
        self.assertEqual(self.processes.snapshot()["Node"], 7)
        self.assertTrue(any("startup-error" in text for _, text in self.logs))

    def test_stop_only_affects_owned_children(self):
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], creationflags=launcher.NO_WINDOW)
        try:
            self.processes.start([self.service("Node", "import time; time.sleep(60)")])
            self.processes.stop()
            self.assertIsNone(unrelated.poll())
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=5)

    def test_stop_wait_does_not_block_status_queries_and_escalates_to_kill(self):
        entered = threading.Event()
        release = threading.Event()
        process = Mock()
        process.poll.return_value = None

        def wait(timeout):
            entered.set()
            release.wait(5)
            if not process.kill.called:
                raise subprocess.TimeoutExpired("test", timeout)
            process.poll.return_value = 1
            return 1

        process.wait.side_effect = wait
        self.processes._children["Node"] = process
        with ThreadPoolExecutor(max_workers=2) as executor:
            stopping = executor.submit(self.processes.stop)
            self.assertTrue(entered.wait(3))
            try:
                status = executor.submit(self.processes.snapshot)
                self.assertEqual(status.result(timeout=1), {"Node": None})
            finally:
                release.set()
            stopping.result(timeout=3)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
