# MineMate

Minecraft Java Edition AI 玩伴。Node.js 负责游戏控制，Python Agent 负责模型与工具调用。详细配置、认证、接口和游戏验收见 [Minecraft Bot 文档](minecraft-bot/README.md)。

日志的级别、请求 ID、脱敏规则和常见错误码排查见 [日志与诊断说明](docs/logging.md)。

Agent 的事件日志、按玩家隔离的上下文、压缩摘要、SQLite 持久化与恢复语义见 [记忆与上下文说明](docs/memory.md)。

查看 [现有工具使用指南](docs/tools-guide.md)：完整列出 34 个工具的能力、参数示例、前置条件、数量/距离限制、组合流程及常见错误。

物品和方块名称不再由 Python 维护静态别名。涉及资源的自然语言请求会先调用 Node.js 的 `resolve_resource`，从当前 Mineflayer 运行时 registry（包括已加载的模组内容）返回候选；候选不唯一时先澄清，复杂任务仍需后续 Skill 和 Planning。

Planning 前置接口已补齐：移动终态、配方/交互站位查询、后台显式工具序列、熔炼续接、整任务租约和执行回执。新增接口、请求示例和游戏验收见 [Task 007 前置衔接说明](docs/tasks/task007-preparation.md)。当前尚未接入 LLM 自动计划生成或重新规划；原自然语言消息接口保持 Task 006 行为。

Task 006.5 已实现阶段 A 基础设施、阶段 B 的 `explore_and_find` 和阶段 C 的 `gather_resource`：前者搜索与接近，后者查询真实采集来源，组合挖掘、拾取并核验背包目标总数。测试脚本可直接调用 Skill、显示进度和完整工具账本，操作示例见 [Skill 实施与测试说明](docs/tasks/task0065-skills.md)。合成、熔炼 Skill 与自然语言选择 Skill 尚未接入。无需新增依赖或修改现有 `.env`。

业务 Skill 按“一技能一目录”组织。首个技能的 [SKILL.md](agent/skills/explore_and_find/SKILL.md) 提供用途、输入与测试命令，[SOP.md](agent/skills/explore_and_find/SOP.md) 记录工具顺序和验收；同目录 `metadata.py` 是运行时元数据及参数的唯一代码来源，`skill.py` 实现执行。当前暂不添加辅助资源，说明文件尚未接入 LLM。

第二个技能见 [gather_resource 技能说明](agent/skills/gather_resource/SKILL.md) 和 [采集 SOP](agent/skills/gather_resource/SOP.md)。例如在根目录运行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-gather-skill.ps1 -Player Steve -Resource oak_log -Count 4`：背包已有数量会扣除，达到总数 4 才成功；请将 Steve 换成实际昵称，并先重启 Node/Python 服务。

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
