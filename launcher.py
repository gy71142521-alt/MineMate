"""临时本机测试启动器：只管理自己启动的两个直接子进程，不执行游戏工具。"""
from __future__ import annotations

import codecs
import os
import queue
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parent
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


@dataclass(frozen=True)
class Service:
    name: str
    command: tuple[str, ...]
    directory: Path


def service_commands(root: Path = ROOT) -> list[Service]:
    """固定启动入口，避免经过 npm/cmd 产生难以管理的中间服务进程。"""
    python = root / "agent" / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    node = shutil.which("node")
    if not python.is_file():
        raise RuntimeError("缺少 agent/.venv，请先按 README 创建 Python 环境并安装依赖。")
    if not node:
        raise RuntimeError("未找到 Node.js，请确认已安装并加入 PATH。")
    if not (root / "minecraft-bot/node_modules/mineflayer").is_dir():
        raise RuntimeError("缺少 Node 依赖，请先在 minecraft-bot 目录执行 npm.cmd install。")
    return [
        Service("Python", (str(python), "-u", "-m", "app.main"), root / "agent"),
        Service("Node", (node, "src/index.js"), root / "minecraft-bot"),
    ]


class Processes:
    def __init__(self, log: Callable[[str, str], None]):
        self.log = log
        self._lock = threading.RLock()
        self._operations = threading.RLock()
        self._children: dict[str, subprocess.Popen] = {}

    def snapshot(self) -> dict[str, int | None]:
        with self._lock:
            return {name: process.poll() for name, process in self._children.items()}

    def start(self, services: list[Service]) -> None:
        with self._operations:
            if any(code is None for code in self.snapshot().values()):
                raise RuntimeError("已有服务运行，请先停止全部再重新启动。")
            with self._lock:
                self._children.clear()
            if len({service.name for service in services}) != len(services):
                raise RuntimeError("启动配置存在重复服务名称。")
            env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
                   "PYTHONUTF8": "1", "NO_COLOR": "1", "FORCE_COLOR": "0"}
            try:
                for service in services:
                    process = subprocess.Popen(
                        service.command, cwd=service.directory, env=env,
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        creationflags=NO_WINDOW,
                    )
                    with self._lock:
                        self._children[service.name] = process
                    self.log(service.name, f"[启动器] 已启动进程 PID={process.pid}，请查看下方服务日志。\n")
                    threading.Thread(target=self._read, args=(service.name, process), daemon=True).start()
            except OSError:
                self.stop()
                raise RuntimeError("无法启动服务，已停止本次创建的其它进程。请检查解释器、目录和权限。") from None

    def _read(self, name: str, process: subprocess.Popen) -> None:
        # 按字节增量读取：未换行的输出也实时展示，跨读取的中文不会被拆坏。
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            assert process.stdout is not None
            with process.stdout:
                while chunk := os.read(process.stdout.fileno(), 4096):
                    self.log(name, decoder.decode(chunk))
                remaining = decoder.decode(b"", final=True)
                if remaining:
                    self.log(name, remaining)
        except OSError:
            self.log(name, "\n[启动器] 日志读取中断。\n")
        finally:
            code = process.wait()
            self.log(name, f"\n[启动器] PID={process.pid} 已退出，退出码={code}。\n")

    def stop(self) -> None:
        with self._operations:
            # 只终止本实例持有的 Popen，不按端口或进程名杀其它终端的服务。
            with self._lock:
                children = list(reversed(self._children.items()))
            for name, process in children:
                if process.poll() is None:
                    self.log(name, "[启动器] 正在停止服务进程…\n")
                    try:
                        process.terminate()
                    except OSError:
                        if process.poll() is None:
                            self.log(name, "[启动器] 请求停止失败，正在检查退出状态。\n")
            for name, process in children:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                        process.wait(timeout=3)
                    except (OSError, subprocess.TimeoutExpired):
                        self.log(name, "[启动器] 进程未能停止，请再次点击停止。\n")
            if any(process.poll() is None for _, process in children):
                raise RuntimeError("部分进程未能停止，请再次点击停止；窗口将保持打开。")


def main() -> None:
    # 测试进程管理时不需要创建 GUI；Tkinter 属于 Python 标准库。
    import tkinter as tk
    from tkinter import messagebox, scrolledtext, ttk

    root = tk.Tk()
    root.title("MineMate · 测试启动器")
    root.geometry("1050x760")
    root.minsize(760, 520)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(2, weight=1)
    logs: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=2000)
    overflow = threading.Event()

    def log(name: str, text: str) -> None:
        try:
            logs.put_nowait((name, text))
        except queue.Full:
            overflow.set()

    processes = Processes(log)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="launcher")
    operations = []
    stopping = False
    closing = False

    toolbar = ttk.Frame(root, padding=10)
    toolbar.grid(row=0, column=0, sticky="ew")
    start_button = ttk.Button(toolbar, text="一键启动")
    start_button.pack(side="left", padx=(0, 8))
    stop_button = ttk.Button(toolbar, text="停止全部", state="disabled")
    stop_button.pack(side="left", padx=(0, 8))
    status = tk.StringVar(value="未启动")
    ttk.Label(toolbar, textvariable=status).pack(side="right")
    ttk.Label(root, padding=(10, 0, 10, 8), wraplength=1000,
              text="先开放 Minecraft LAN，并更新 minecraft-bot/.env 中的 MC_PORT。运行中仅表示进程存在，连接结果请查看日志。关闭窗口会停止本窗口启动的服务。"
              ).grid(row=1, column=0, sticky="ew")

    panes = ttk.Panedwindow(root, orient="vertical")
    panes.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
    views = {}
    frames = {}
    for name, label in [("Node", "Node.js / Minecraft Bot"), ("Python", "Python / Agent")]:
        frame = ttk.Labelframe(panes, text=f"{label} — 未启动", padding=5)
        view = scrolledtext.ScrolledText(frame, wrap="word", height=15, state="disabled",
                                        font=("Consolas", 10), background="#171b22", foreground="#e5e9f0",
                                        insertbackground="white")
        view.pack(fill="both", expand=True)
        panes.add(frame, weight=1)
        views[name] = view
        frames[name] = (frame, label)

    def clear_logs() -> None:
        for view in views.values():
            view.configure(state="normal")
            view.delete("1.0", "end")
            view.configure(state="disabled")

    ttk.Button(toolbar, text="清空日志", command=clear_logs).pack(side="left")

    def start() -> None:
        if operations or closing:
            return

        def launch() -> None:
            services = service_commands()
            processes.start(services)

        for name in views:
            log(name, "\n========== 本次启动 ==========\n")
        operations.append(executor.submit(launch))
        status.set("正在启动…")
        start_button.configure(state="disabled")

    def stop() -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        # 单一工作线程按顺序收尾，不遗留稍后创建的进程，也不丢弃启动/停止异常。
        operations.append(executor.submit(processes.stop))
        status.set("正在停止…")
        start_button.configure(state="disabled")
        stop_button.configure(state="disabled")

    def close() -> None:
        nonlocal closing
        if closing:
            return
        closing = True
        stop()

    def append(name: str, text: str) -> None:
        view = views[name]
        # 用户上滚查看时不强制滚回末尾；单框最多保留约 20 万字符。
        follow = view.yview()[1] >= 0.99
        view.configure(state="normal")
        view.insert("end", text)
        size = view.count("1.0", "end-1c", "chars")[0]
        if size > 200000:
            view.delete("1.0", f"1.0+{size - 200000}c")
        if follow:
            view.see("end")
        view.configure(state="disabled")

    def tick() -> None:
        nonlocal closing, stopping
        batches: dict[str, list[str]] = {name: [] for name in views}
        for _ in range(200):
            try:
                name, text = logs.get_nowait()
            except queue.Empty:
                break
            batches[name].append(text)
        if overflow.is_set():
            overflow.clear()
            for batch in batches.values():
                batch.append("\n[启动器] 日志输出过快，已省略部分内容。\n")
        for name, batch in batches.items():
            if batch:
                append(name, "".join(batch))
        for operation in operations[:]:
            if operation.done():
                operations.remove(operation)
                error = operation.exception()
                if error is not None:
                    for name in views:
                        log(name, f"[启动器] {error}\n")
                    if not closing:
                        messagebox.showerror("MineMate 启动器", str(error), parent=root)
        states = processes.snapshot()
        active = any(code is None for code in states.values())
        for name, (frame, label) in frames.items():
            detail = "未启动" if name not in states else "运行中（进程）" if states[name] is None else f"已退出：{states[name]}"
            frame.configure(text=f"{label} — {detail}")
        if not operations:
            stopping = False
            if closing:
                if not active:
                    executor.shutdown(wait=False)
                    root.destroy()
                    return
                closing = False
                messagebox.showerror("MineMate 启动器", "仍有服务未停止，请再次点击停止。", parent=root)
            status.set("服务进程运行中" if active else "已停止" if states else "未启动")
            start_button.configure(state="disabled" if active else "normal")
            stop_button.configure(state="normal" if active else "disabled")
        else:
            stop_button.configure(state="disabled" if closing or stopping else "normal")
        root.after(100, tick)

    start_button.configure(command=start)
    stop_button.configure(command=stop)
    root.protocol("WM_DELETE_WINDOW", close)
    root.after(100, tick)
    try:
        root.mainloop()
    finally:
        executor.shutdown(wait=True)
        processes.stop()


if __name__ == "__main__":
    main()
