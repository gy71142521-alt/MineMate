# Task 007 前置衔接：执行接口与验收

本轮补齐 Task 006 与后续 Planning 之间的执行能力，并完成第一阶段动态资源解析。没有实现 LLM Planner、依赖图生成、Replanner、长期记忆，也没有新增依赖。Task 007 的三个完整自主任务尚未宣布完成。

第一阶段动态解析入口是 Node 工具 `resolve_resource`，实现位于 `minecraft-bot/src/tools/perception/resources.js`。它只读取当前运行时 `itemsByName` 和 `blocksByName`，支持模组注册内容，返回候选并标记歧义。Python 不再维护物品别名或改写工具参数；模型必须先解析，再使用返回的真实 ID。`agent/app/semantic.py` 仅保留不猜测资源的兼容辅助函数，不执行 Skill、不替代真实快照和工具验证。

## 调用关系

```text
未来 Planner / 可信测试调用方
    → Python /api/agent/tasks（校验显式步骤，立即返回任务 ID）
    → 后台串行执行、前序结果引用、执行账本、背包终态验证
    → Node 工具注册表（动态 Schema + 整任务租约 + 请求回执）
    → 原 movement / crafting / mining 等模块 → 同一个 Mineflayer Bot
```

原 `/api/agent/message` 和所有 Task 001～006 接口保持原行为。新的后台入口独立于聊天 HTTP 请求，提交方断开不取消后台任务。**自然语言消息目前仍走原 Tool Calling 循环；自动把复杂自然语言目标转换为后台步骤留给下一阶段。** 后台执行不调用付费模型。

## 文件与职责

| 文件 | 职责 |
| --- | --- |
| `minecraft-bot/src/tools/movement/index.js` | 原控制器增加最多 128 条移动终态历史；原 `getState()` 结构不变 |
| `tools/movement/queries.js` | 注册 `get_movement_task`，查询原任务而不重启导航 |
| `tools/movement/standing.js` | 注册 `find_interaction_positions`，有限地形与射线查询 |
| `tools/crafting/recipes.js` | 注册合成和熔炼配方查询 |
| `tools/crafting/index.js` | 仅额外导出现有熔炼/燃料定义供查询复用 |
| `tools/session.js` | Node 实例 ID、30 秒控制租约及心跳失效 |
| `tools/registry.js` | 整任务互斥、回执和原请求去重；保留原单操作锁 |
| `tools/index.js` | 组装新工具，目录共 34 项 |
| `src/httpServer.js` | 租约、回执与可选执行上下文；保留旧 HTTP 路由 |
| `agent/app/tool_client.py` | 读取动态目录和实例、租约管理、严格回执校验 |
| `agent/app/execution/models.py` | 显式序列及背包验证条件的 Pydantic 模型 |
| `execution/references.py` | 仅允许前序 JSON 结果引用，替换后重新校验参数 |
| `execution/manager.py` | 后台执行、串行等待、预算、取消、账本和终态验证 |
| `agent/app/main.py` | 提交/查询/取消接口及服务关闭清理 |
| `minecraft-bot/test/execution.test.js`、`test/movement.test.js` | Node 新能力、历史与互斥验证 |
| `agent/test/test_execution.py` | Python 后台与本机真实 HTTP 跨语言集成测试 |

同时更新根 README、Bot README、AGENTS.md、`agent/.env.example` 和本文。没有查看或输出真实配置值，也未修改 `.env`、账号密钥或认证缓存；测试进程按原项目方式加载环境，模型请求使用 Mock。

## 新游戏查询工具

这些工具均 `mutates: false`，通过同一注册目录暴露给 Python 和原 Tool Calling。

| 工具 | 参数 | 数据 |
| --- | --- | --- |
| `get_movement_task` | `task_id` | `data.movement` 为历史副本；running→pending，arrived→completed，其余终态→failed |
| `inspect_recipe` | `item_name`, 可选 `count`（1～64）、`max_results`（1～16） | 当前版本配方、材料、元数据、整批产量、工作台需求和背包缺口，缺口较小的优先 |
| `inspect_smelting_recipe` | `item_name`, 可选 `count`（1～8） | 现有普通熔炉适配器的原料、产物、燃料与用量，不猜测未知配方 |
| `find_interaction_positions` | 目标 `position`，可选 `radius`（1～4）、`max_results`（1～16） | `candidates[].position` 为脚部中心坐标，附距离、维度、时间、截断标记，`pathVerified: false` |

站位查询只检查距 Bot 16 格内目标；默认半径 3，垂直 ±2，最多 405 个候选网格。只接受已加载的完整安全支撑、两格空气、没有其他实体占用、交互距离 ≤4.5 且射线通畅的候选。不改实体坐标、不转头、不设寻路目标，不每帧扫描，也不缓存站位。

候选并不保证寻路可达。原 `goto_position` 使用 GoalNear 到“附近”，到达后仍需重新 `inspect_block` 或对应交互检查；不能直接跳过现有距离、视线、耐久及脚下支撑检查。`canSeeBlock` 检查射线遮挡，不依赖当前朝向；Mineflayer 的 `dig` 默认会转向目标，因此新增查询解决的是站位和障碍问题，不通过绕过视线检查解决问题。半砖、楼梯等复杂站位保守地不作为候选。

## Node HTTP 上下文与回执

原统一 JSON 信封不变。`GET /api/tools` 额外返回 `instanceId`、`generation`、`active`、`lease`；Python 的旧 `catalog()` 仍返回 `(catalog, generation)`。

- `POST /api/tools/lease`：`{username, action, instanceId, generation, leaseId?}`；action 为 acquire/heartbeat/release。首次 acquire 返回 leaseId。Bot 必须已出生、存活，没有其他操作或移动任务。
- `POST /api/tools/execute`：保留原参数，额外允许 `instanceId`、`leaseId`。后台执行必须携带上下文。整任务持有租约期间，其他修改工具返回 BUSY；只读查询仍可用。
- `GET /api/tools/requests/{requestId}?username=Steve&instanceId=...`：查询 `{requestId, running, result, instanceId}`。`running: true` 即底层仍在结算，不得把超时返回理解为操作没有发生。不存在记录也不能证明没执行。
- `POST /api/tools/cancel`：保留原全局/按请求 ID 取消；后台清理需携带原实例、代次、租约，迟到清理返回冲突，不能停止后来任务。

Node 使用 UUID 标识本次进程，停止、死亡、重生/维度切换和断线会撤销控制权。Python 每 5 秒续租，30 秒失联自动清理移动和控制状态。旧单工具调用即使超时，仍持有原互斥锁直到底层 API 结算。取消不会回滚已经发生的挖掘、丢弃、投入或合成。

回执最多 512 项，在后续执行时清理已结算超过 10 分钟的记录；不会因容量满而驱逐在途记录。记录和移动历史都是内存数据；Node 重启后旧实例任务必须停止，不得获取新实例继续旧操作。

租约是本机可信服务间的控制协调机制，并非用户身份认证。继续使用项目现有 OWNER_USERNAME 与本机监听配置；不要使用多 worker 运行 Python 后台管理器。

## Python 后台接口

| 方法及地址 | 行为 |
| --- | --- |
| `POST /api/agent/tasks` | 16 KiB 请求体上限；校验实际 Node Schema、前序引用并申请租约；202 返回 task_id |
| `GET /api/agent/tasks/{task_id}?player=Steve` | 查询步骤状态、账本、验证证据和终态 |
| `POST /api/agent/tasks/{task_id}/cancel` | 请求 `{ "username": "Steve" }`，停止后续调用并释放原租约 |

同一时刻只有一个后台任务。不自动抢占新任务：请先取消旧任务，再提交新的 request_id。相同玩家及 request_id 的相同内容返回原记录，不重复操作；不同内容返回冲突。

每个序列最多 24 步；总时限默认 300 秒、最大 600 秒；每步等待默认 90 秒、最大 180 秒；工具调用默认最多 96 次、最大 192 次（包括导航/熔炉轮询）。心跳和快照读取不算游戏工具调用。最多保存 64 个任务记录，达到容量后拒绝新提交，需保存需要的记录后重启 Agent；不提供持久化和重启自动恢复。

步骤串行执行，其顺序就是依赖关系；本阶段不生成或调度 DAG。跟随是持续任务、停止是抢占动作，因此后台序列不接受 follow_player/stop_movement，二者继续使用原聊天和工具接口。

`goto_position` 返回 pending 后只查询原 taskId，收到 arrived 才继续后续步骤。`smelt_item` 的 start 返回 pending 后只轮询 status，outputReady 为真时 take 一次；不重复 start。后台 smelt start **包含取出整批产物的续接**，调用方不要再安排一次 take。熔炉被他人改动、产物数量不一致、取消或等待超时都会停止后续调用，炉中已投入的物品仍留在游戏中。

失败结果、uncertain、连接中断或格式异常不会被当作 completed。丢失 HTTP 回复时仅查询原 requestId 回执：回执仍在运行、缺失或不可信，则记录不确定状态和能取得的最新自身/背包证据，停止序列，不重发副作用。

`verify_inventory` 支持 `increase`（相对任务开始的增量，默认）与 `at_least`（终态总量）条件。只有步骤成功且所有显式背包条件由快照验证后才标记 completed；没有条件返回 finished_unverified。当前不提供通用建造/战斗/位置的最终目标验证，这部分留给 Task 007 Task Verifier。

## 启动

无新增依赖、无新增必填环境变量。LAN 本次端口仍填在真实 `minecraft-bot/.env`；原认证与模型配置不变。可继续使用根目录一键启动器，或分别开两个 PowerShell 终端：

```powershell
Set-Location 'D:\桌面\MineMate\minecraft-bot'
```

```powershell
npm.cmd start
```

第二个终端：

```powershell
Set-Location 'D:\桌面\MineMate\agent'
```

```powershell
.\.venv\Scripts\python.exe -m app.main
```

## 请求示例

先在 Bot 背包放至少 4 个橡木木板，保持 Bot 静止且已出生。提交显式工作台合成步骤（不需要真实 LLM API）：

```powershell
$payload = @'
{
  "player": "Steve",
  "request_id": "table-test-001",
  "steps": [
    {"step_id": "recipe", "tool": "inspect_recipe", "arguments": {"item_name": "crafting_table", "count": 1}},
    {"step_id": "make", "tool": "craft_item", "arguments": {"item_name": "crafting_table", "count": 1}}
  ],
  "verify_inventory": [{"item": "crafting_table", "count": 1, "mode": "increase"}],
  "timeout_seconds": 120,
  "max_tool_calls": 20
}
'@
```

```powershell
$submitted = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/agent/tasks' -ContentType 'application/json' -Body $payload
```

```powershell
$submitted.data.task_id
```

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/agent/tasks/$($submitted.data.task_id)?player=Steve" | ConvertTo-Json -Depth 30
```

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/agent/tasks/$($submitted.data.task_id)/cancel" -ContentType 'application/json' -Body '{"username":"Steve"}'
```

把 Steve 改为实际玩家昵称，OWNER_USERNAME 已配置时须匹配。重复相同提交不会再合成；测试新操作需换 request_id，并先核对已有结果。

熔炼步骤示例：将 position 替换为可触及的真实空熔炉坐标，背包备好原料和燃料，不要直接复制示例坐标执行。

```json
{
  "player": "Steve",
  "request_id": "iron-test-001",
  "steps": [{
    "step_id": "smelt",
    "tool": "smelt_item",
    "arguments": {
      "position": {"x": 10, "y": 64, "z": 8},
      "action": "start", "item_name": "raw_iron", "fuel_name": "coal", "count": 2
    },
    "wait_seconds": 90
  }],
  "verify_inventory": [{"item": "iron_ingot", "count": 2, "mode": "increase"}]
}
```

前序查询结果引用示例：若先前 step_id=stands 的 `find_interaction_positions` 返回候选，后续导航可写：

```json
{
  "step_id": "walk",
  "tool": "goto_position",
  "arguments": {
    "x": {"$ref": {"step": "stands", "path": ["data", "candidates", 0, "position", "x"]}},
    "y": {"$ref": {"step": "stands", "path": ["data", "candidates", 0, "position", "y"]}},
    "z": {"$ref": {"step": "stands", "path": ["data", "candidates", 0, "position", "z"]}}
  }
}
```

没有候选、路径不存在、引用未来步骤、非法字段/索引或替换后类型不匹配均停止，不编造坐标。不支持表达式、Python/JS 代码或自动计算材料数量。

## 自动化与游戏验收

运行全部测试：

```powershell
Set-Location 'D:\桌面\MineMate\minecraft-bot'
```

```powershell
npm.cmd test
```

```powershell
Set-Location 'D:\桌面\MineMate\agent'
```

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s test -v
```

新增测试包含真实安装的 Mineflayer 配方 API、合成区块/射线、原移动控制器终态、Node 回环 HTTP 租约/回执，以及 Python 到真实 Node HTTP 服务的导航→合成执行链。游戏对象使用 Mock 或合成数据；不会连接 Minecraft 或请求付费模型。

必须在测试世界手动确认：

1. 原“你好/状态/背包/附近/环境/跟随我/前往/停止”、跑步/潜行/跳跃和原工具仍正常。
2. 提交导航→交互序列，看到只启动一次导航、真正到达后才交互；无路时不执行后续步骤。
3. 目标被墙挡住或是脚下支撑时，站位查询给出其他候选或空列表；既不隔墙挖掘，也不自动破坏脚下支撑。到附近后仍需现场检查。
4. 检查工作台和石镐配方，与当前版本、材料、工作台要求相符；运行工作台示例确认背包增量。
5. 空熔炉与真实原料/燃料测试 start→status→take；等待时 API 可查询，游戏不阻塞，背包铁锭实际增加且没有重复投料。
6. 导航/熔炼等待中输入“停止”，确认不再执行后续工具，原料不会因取消自动退回；然后可执行新的手动移动。
7. 连续提交同一 request_id 只操作一次；不同任务同时提交被拒绝。旧任务取消不能停止新的任务。
8. 执行中死亡、换维度、断线、重启 Node 或停止 Python，确认旧任务不恢复；Python 失联后最多约 30 秒租约过期清理控制。
9. 观察进度接口的步骤、ledger 和 verification，对照游戏实际结果。工具返回但没有背包增量时不得报告完成。

本轮实际结果：Node **147/147**、Python **85/85** 全部通过，包含原有 138 项 Node 与 65 项 Python 测试；新增分别为 9 项和 20 项。Python `compileall` 和 `git diff --check` 通过。测试使用现有 Python 3.14.10，未单独验证项目约定的 Python 3.12。

尚未进行本轮 Minecraft 游戏验收或真实云端 API 测试；自动化通过不代表 Task 007 完成。记录为内存数据、候选站位不保证路径可达、最终验证目前只覆盖显式背包条件；这些限制在后续规划和验收时必须保留。
