# MineMate

Minecraft Java Edition AI 玩伴。Node.js 负责游戏控制，Python Agent 负责模型与工具调用。详细配置、认证、接口和游戏验收见 [Minecraft Bot 文档](minecraft-bot/README.md)。

## 临时测试启动器（Windows）

在项目根目录双击 **`start-minemate.cmd`**，打开窗口后点击 **“一键启动”**。

- 上方日志框显示 Node.js / Minecraft Bot 日志，下方显示 Python / Agent 日志；同时包含标准输出和错误输出。
- **“停止全部”**停止本窗口创建的两个服务；关闭窗口也会先停止服务。停止后可再次启动。
- **“清空日志”**只清空显示内容，不停止服务。可选择日志文字并按 Ctrl+C 复制。
- 使用现有 `minecraft-bot/.env` 和 `agent/.env`，不会修改或在界面展示配置。修改配置后停止并重新启动。
- 窗口只显示进程状态，不把进程运行等同于 Minecraft 连接成功或 API 已就绪；请查看对应日志。Microsoft 首次认证仍按日志提示在浏览器中操作。

启动前需要：

1. 已安装 Node.js，并在 `minecraft-bot` 中执行过 `npm.cmd install`。
2. 已创建 `agent/.venv`，并在其中安装 `agent/requirements.txt`；Python 安装需包含 Tkinter（Windows 官方安装包通常自带）。
3. 在 Minecraft 中开放 LAN，将**本次端口**填入 `minecraft-bot/.env` 的 `MC_PORT`。
4. 先停止原来终端中手动运行的 Node/Python。启动器不接管已有进程，也不会按进程名或端口终止其它程序；同时打开多个启动器仍可能造成端口冲突。

已有环境下，也可在 PowerShell 分别执行：

```powershell
Set-Location 'D:\桌面\MineMate'
```

```powershell
.\agent\.venv\Scripts\python.exe .\launcher.py
```

首次安装及原手动启动方式见 [完整启动说明](minecraft-bot/README.md#配置与启动)。启动器不会自动安装依赖、扫描 LAN 端口或自动重启失败服务。缺少依赖、认证失败或端口冲突会显示在错误提示/对应日志中。

## 实现与测试

- `launcher.py`：Python 标准库 Tkinter 界面、后台进程管理和实时日志读取，没有新增依赖。直接启动 `node src/index.js` 和虚拟环境的 `python -u -m app.main`，分别使用原服务目录。
- `start-minemate.cmd`：双击入口，使用现有虚拟环境的 `pythonw.exe` 打开窗口，避免额外服务控制台。
- `agent/test/test_launcher.py`：启动配置检查、两个真实测试子进程的中文/无换行日志、启动回滚、重复启动、退出码、停止范围与超时清理。

当前临时版在 Windows 使用终止进程的方式停止服务，不等待正在执行的游戏操作完成，也不提供优雅重启；请在测试世界使用。日志只保留在窗口内，单框最多约 20 万字符，输出过快会提示省略。关闭窗口的清理逻辑不等同于任务管理器强杀启动器后的保证。

启动器测试在 `agent` 目录运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s test -p test_launcher.py -v
```

此测试使用临时子进程，不连接真实 Minecraft，不请求付费模型。图形界面的按钮、关闭窗口和真实 Bot/Agent 联动仍需实际操作验收。

本次自动化结果：Node **138/138**、Python **65/65**（其中启动器新增 6 项）通过；启动器和测试文件的 Python 编译检查通过。实际测试环境使用现有 Python 3.14.10 / Tk 9.0；尚未验证 Python 3.12 下的图形界面。
