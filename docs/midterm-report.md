# MineMate 中期汇报说明文档

> **Lab B（智能体软件）**  
> 版本：2026-11-23  
> 性质：阶段性说明文档，不是提交稿，也不是中期答辩演讲稿。  
> 说明：侧重已经实现、可以验证的功能；未完成的能力和真实游戏验收状态会单独标注。

## 0. 摘要

MineMate 是一个 Minecraft Java Edition AI 玩伴。玩家在游戏聊天框里用自然语言提出目标，Agent 理解、选择工具、调用游戏控制层执行，再把真实结果反馈给模型，继续推理或回复。

当前已经完成：

- **双进程架构**：Node.js + Mineflayer 唯一负责游戏连接和操作；Python + FastAPI 负责 LLM、Agent Loop、记忆和校验。
- **任务执行运行时已跑通**：Planner 判断直接/规划 → Executor 调用工具 → 真实结果回填 → 失败可调整计划 → 模型用 `finish_task` 结束；Harness 只做步数/调用/超时/重复错误兜底。
- **39 个游戏工具**：感知、移动、挖掘、建造、背包、战斗、方块交互、合成/熔炼。
- **4 个确定性 Skill**：`explore_and_find`、`gather_resource`、`craft_item`、`smelt_item`。
- **事件溯源式记忆**：按玩家持久化会话，消息历史从事件日志派生，上下文超限时压缩旧轮次，SQLite 重启可继续。
- **Planning 与执行底座**：Planner 生成结果导向步骤；显式任务仍支持 Schema/引用校验、租约、串行执行、账本、导航/熔炼续接和背包验证。
- **安全与可观测性**：修改互斥、停止优先、`pending/uncertain` 不报成功、请求 ID 和阶段日志。

本轮复跑：

- Python：**299/299 通过**
- Node：**180/180 通过**

主要缺口：

- Planner/Replanner 已接入单轮任务，但计划不持久化、没有 DAG/子任务依赖；跨轮复杂任务仍依赖对话历史。
- Skill 还没有接入自然语言自动选择，目前通过 HTTP/测试脚本调用。
- 记忆只在 Tool Calling 路径生效；长期事实主要靠压缩摘要。
- 真实 Minecraft 全流程验收需要按仓库清单现场执行；自动化结果不等同于真实游戏验收。

---

## 1. 项目总览

### 1.1 项目目标

做一个**能在游戏里理解目标、执行动作、反馈结果、记住上下文的 AI 玩伴**，而不是只会聊天的机器人。

核心链路：

1. 玩家用自然语言表达目标；
2. Agent 理解并选择工具；
3. Node 执行真实游戏操作；
4. Agent 根据真实结果继续或停止；
5. 连续对话中保留刚才发生的事。

### 1.2 目标用户与场景

| 用户/场景 | 说明 |
| --- | --- |
| Minecraft Java 玩家 | 本机 Open to LAN 或可信局域网，单人/小范围联机 |
| 生存/创造测试世界 | 采集、合成、探索、简单战斗、建造辅助 |
| 想要“玩伴”而不是“外挂”的玩家 | 自然语言、状态反馈、上下文记忆 |
| 课程/实验环境 | 启动器同时运行 Node/Python，便于演示 |

典型场景：

- “看看你的状态和背包，然后跳一下。”
- “帮我采集 4 个橡木原木。”
- “做一个工作台并放下来。”
- “刚才我们说要做什么？”
- “停止”“跟随我”“前往 …”等固定指令。

### 1.3 要解决的问题

1. **自然语言到游戏动作**：LLM 不能直接操作 Minecraft，需要受控工具。
2. **LLM 不确定性**：非法参数、重复/并行调用、虚假完成，需要程序校验。
3. **异步游戏状态**：导航、挖掘、窗口、背包需要真实结果，而不是“调用成功即完成”。
4. **连续记忆**：早期 Agent 每条消息独立，无法理解“刚才/再挖一个”。
5. **安全边界**：不破坏建筑、不无限循环、不把未确认说成成功。
6. **可维护性**：Node 和 Python 不能各自维护一套工具 Schema。

### 1.4 需求与实现

| 需求 | 当前实现 | 状态 |
| --- | --- | --- |
| 连接/聊天/固定指令 | Node + Mineflayer，LAN 连接 | 已实现 |
| 世界感知 | 状态、背包、附近实体/方块、资源解析 | 已实现 |
| 移动 | 跟随、导航、跑步/潜行、跳跃、停止 | 已实现 |
| 自然语言 | Tool Calling；关闭时回退 JSON 意图 | 已实现 |
| 多步任务 | 单请求内有界循环，多工具串行 | 已实现（有界） |
| 复杂确定性任务 | 4 个 Skill | 已实现 |
| 记忆与上下文 | 事件日志、压缩、SQLite、按玩家隔离 | 已实现（Tool Calling） |
| Planning | Planner 生成步骤、Executor 执行、失败 update_plan | 已实现（单轮） |
| 显式后台任务 | 步骤执行、租约、账本、验证 | 执行底座已实现 |
| 安全/可观测性 | 权限、互斥、停止优先、状态语义、日志 | 已实现 |

### 1.5 系统架构

```text
玩家 Minecraft 聊天
        │
        ▼
┌──────────────────────────────────────┐
│ Node.js / Mineflayer                 │
│  - 唯一 Minecraft 连接与游戏状态      │
│  - 39 个工具 + Schema                 │
│  - 权限、互斥、超时、取消、租约、回执 │
└──────────────────────────────────────┘
        ▲                    │
        │ HTTP JSON          │ 工具调用 / 世界快照
        ▼                    ▼
┌──────────────────────────────────────┐
│ Python / FastAPI Agent               │
│  - MessageHandler：限流、每玩家串行   │
│  - AgentRuntime：Planner + 工具执行   │
│  - MemoryManager：事件日志、压缩      │
│  - SkillManager：确定性多步任务       │
│  - TaskManager：显式步骤后台执行      │
│  - 日志诊断 / 回复风格                │
└──────────────────────────────────────┘
        │
        ▼
   OpenAI 兼容模型
```

分工理由：

- Mineflayer 是有状态长连接，只放在一个 Node 进程；
- Python 负责 LLM 编排、Pydantic 校验、异步 HTTP 和记忆；
- Node 是工具名称、描述和 Schema 的唯一来源；Python 动态获取；
- 两边只交换 JSON，便于分别测试和替换模型/存储。

---

## 2. 当前进展

### 2.1 已完成阶段

| 阶段 | 成果 |
| --- | --- |
| 连接与感知 | LAN 连接、聊天、状态/背包/实体/方块 |
| 移动 | 跟随、导航、跑步/潜行、跳跃、停止 |
| 意图与对话 | LLM 意图、限流、脱敏、固定指令回退 |
| 统一工具层 | 注册表、Schema、统一信封、互斥、超时/取消 |
| Tool Calling | function tools 循环、参数/重复/批量校验、结果回填 |
| Skill | 契约、注册、严格参数、执行/验收、4 个业务 Skill |
| Planning 前置 | 显式步骤、Node 租约/回执、`$ref`、续接、验证 |
| 记忆与上下文 | 事件溯源 Session、压缩、SQLite、查询/重置 API |
| 日志与体验 | `rid`、错误链、脱敏堆栈、玩家口吻回复、坐标过滤 |

### 2.2 核心 Agent Loop

```text
Node 聊天 → Python /api/agent/message
  → MessageHandler：限流、并发、每玩家一个 turn
  → AgentRuntime：
      1. 获取工具目录 + 世界快照，派生记忆上下文
      2. Planner 判断：direct / plan
           simple → 直接执行
           complex → 2–8 个结果导向步骤
      3. Executor 循环：
           LLM(function tools + finish_task + update_plan)
             ├─ finish_task → 模型宣布结束
             ├─ update_plan → 替换剩余步骤
             └─ 普通工具（1–8 个）→ 校验 → Node 执行
                  错误/pending/uncertain → role=tool 回填，继续
      4. Harness：步数/调用/超时/连续错误/重复失败兜底
```

关键规则：

- 模型只选择工具，不直接操作游戏；Python 校验/转发，Node 执行。
- 一次响应 1–8 个调用，串行执行；超过 8 个整批拒绝。
- 默认最多 12 个 LLM 步、24 次 Node 工具调用、120 秒总时限。
- 工具失败、`pending`、`uncertain` 不终止任务，模型根据真实结果决定重试/换工具/改计划。
- 任务结束必须调用 `finish_task`；普通文本不会自动结束。
- 重复失败/连续错误/超限由 Harness 兜底，返回诚实的不完整结果，不无限循环。
- 客户端断开、超时、停止会请求 Node 取消。
- 同一玩家同时只允许一个 turn，避免记忆出现两个 open turn。

### 2.3 工具层：39 个工具

| 类别 | 数量 | 工具 |
| --- | ---: | --- |
| 感知/资源 | 7 | `get_world_snapshot`、`query_status`、`inspect_inventory`、`nearby_entities`、`nearby_blocks`、`resolve_resource`、`inspect_mining_sources` |
| 移动 | 8 | `follow_player`、`goto_position`、`set_sprint`、`set_sneak`、`jump`、`get_movement_task`、`find_interaction_positions`、`stop_movement` |
| 挖掘 | 3 | `inspect_block`、`find_block`、`mine_block` |
| 建造 | 2 | `inspect_placement`、`place_block` |
| 背包 | 5 | `collect_item`、`equip_item`、`consume_item`、`drop_item`、`use_item` |
| 战斗 | 2 | `find_entity`、`attack_entity` |
| 交互 | 5 | `open_workbench`、`open_furnace`、`open_container`、`interact_block`、`transfer_item` |
| 合成/熔炼 | 7 | `inspect_recipe`、`inspect_smelting_recipe`、`craft_item`、`smelt_item`、`furnace_add_input`、`furnace_add_fuel`、`furnace_take_output` |

实现特点：

- 统一返回 `{success, tool, data, error}`；`status` 区分 `completed/pending/uncertain`。
- 修改工具互斥，停止优先；超时等待底层结算再释放锁。
- 方块/物品名来自 Node 运行时 registry，不维护 Python 静态清单。
- 资源查询走 `resolve_resource`，歧义先澄清。
- `find_block` 返回 `visible/diggable`；最近的候选不一定可见或可达。

### 2.4 Skill：确定性多步流程

工具是原子动作；Skill 有前置条件、固定顺序和独立验收。

| Skill | 能力 | 成功条件 |
| --- | --- | --- |
| `explore_and_find` | 搜索方块/实体，有限探索、接近 | 找到真实目标；接近后复核距离/ID |
| `gather_resource` | 查询真实掉落，搜索/接近/挖掘/拾取 | 背包目标总数达标，维度一致 |
| `craft_item` | 查真实配方，必要时找/接近工作台，合成 | 背包实际新增 ≥ 请求值，维度一致 |
| `smelt_item` | 找兼容熔炉，按容量投入原料/燃料 | 炉内原料/燃料/产品守恒，维度一致；只表示投料完成 |

共同点：

- 内部不逐个调用 LLM，是确定性程序；
- 共用执行槽、租约、心跳、账本、回执和取消；
- 每次工具操作唯一 `operation_id`，重复同 ID 复用结果；
- `execute()` 不是成功凭证，`verify()` 用执行前后快照独立核验；
- 失败/不确定不标 `completed`。

当前限制：Skill 尚未接入自然语言自动选择；`gather_resource`、`craft_item` 等通过 HTTP/脚本调用。自然语言任务由 `AgentRuntime` 的 Planner + Executor 组合工具完成。

### 2.5 记忆与上下文

实现效果：Agent 能记住同一玩家此前说过什么、做过什么，下一条消息可以引用“刚才那个/再挖一个”。

简要实现：

- 每个玩家一个 Session，事件日志仅追加；
- 事件包括 `turn/*`、`step/*`、`user/message`、`assistant/message`、`tool/call`、`tool/result`、`compaction/*`；
- 模型消息历史由 `Session.derive_messages()` 从事件日志派生，不维护第二份历史；
- 当前世界快照和本地时间作为临时上下文追加，不写入持久日志；
- 上下文超过预算时，把最旧完整 turn 压缩成摘要，保留近期轮次；原始事件不删除；
- SQLite 默认 `data/minemate-memory.sqlite3`，WAL、事务、seq 连续校验；重启后加载最近会话；
- 崩溃残留的 step/turn/compaction 标记会在加载时修复；不完整工具调用块不发给模型；
- 记忆失败只降级并记录日志，不阻断聊天。

查询/重置：

```text
GET  /api/agent/memory/{player}
POST /api/agent/memory/{player}/reset
```

### 2.6 Planning：Planner + 显式后台执行

- `AgentRuntime` 内的 Planner 先判断 direct/plan；复杂目标生成结果导向步骤，执行中可用 `update_plan` 修改；
- 计划和工具结果都在同一 turn 内，由 Executor 逐步推进；
- 显式后台 `/api/agent/tasks` 仍负责调用方提供的步骤：最多 24 步、顺序依赖、`$ref` 引用、租约/心跳、账本和背包验证；
- 两条路径共享 Node 工具与验证语义，但 Planner 不会自动把计划提交到 TaskManager；
- 计划当前不持久化为 memory 事件，也没有 DAG/子任务依赖。

### 2.7 安全、可观测性与回复体验

- `OWNER_USERNAME` 限制修改型工具调用者；
- 修改工具互斥；停止优先；取消不回滚已发生操作；
- `pending/uncertain` 不报告完成；程序覆盖虚假成功；
- 挖掘保护方块/脚下支撑；攻击白名单；放置不覆盖/不碰撞；
- 日志带 `rid`、阶段、错误码、耗时；`LOG_TRACEBACK` 可控制堆栈级别；
- Agent 回复采用玩家口吻，不主动报坐标、工具名、状态码；即使模型输出坐标，也会在回复层过滤。

### 2.8 测试与验证

本轮复跑：

```text
Python: 299 tests, OK
Node:   180 tests, 180 pass
```

覆盖：

- Node：工具注册/Schema、移动、感知、挖掘、建造、背包、战斗、交互、合成/熔炼子工具、权限、互斥、超时/取消、HTTP、租约/回执。
- Python：Agent HTTP、LLM 客户端、Tool Loop、工具目录、执行器、4 个 Skill、记忆事件/压缩/SQLite/按玩家隔离、回复风格、日志脱敏、启动器、配置。
- 跨语言：Python 通过真实 Node HTTP/目录做契约测试，不只测 Python mock。

真实环境：本轮没有重新连接真实 Minecraft 或付费模型做全流程验收；仓库有游戏验收清单，演示前需现场跑一遍。

### 2.9 10 分钟演示建议

| 时间 | 内容 | 展示点 |
| --- | --- | --- |
| 0:00–1:00 | 目标、双进程架构、Node/Python 分工 | 架构合理性 |
| 1:00–3:30 | 聊天：“看看生命值和背包，然后跳一下，再告诉我状态” | Agent Loop、工具选择、真实结果 |
| 3:30–6:30 | 显式 `gather_resource` 采集 2 个橡木 | 多步执行、租约、账本、核验 |
| 6:30–8:30 | 记忆：第一条“先检查 4 块木板，别合成”；第二条“继续刚才那件事，够就做” | 任务连续性、约束记忆、持久记忆 |
| 8:30–10:00 | 安全边界（如挖脚下方块）与后续 Planner 计划 | 不确认不报成功、反思 |

演示前：

- Minecraft Open to LAN，端口写入 `minecraft-bot/.env` 的 `MC_PORT`；
- `agent/.env` 配好 Key/Model，`TOOL_CALLING_ENABLED=true`；
- Bot 已出生、存活；重置演示玩家记忆：

```powershell
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/agent/memory/Steve/reset'
```

`gather_resource` 示例：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-gather-skill.ps1 -Player Steve -Resource oak_log -Count 2
```

如果没有现场 Minecraft，可录制视频或展示真实日志、工具账本和测试输出；不要把 Mock 测试说成真实游戏验收。

---

## 3. 开发过程与后续计划

### 3.1 人机协同开发

1. 先读 `AGENTS.md` 和相关代码，确认边界；
2. 每次只实现当前明确要求的功能；
3. 先写/改 Pydantic 模型和接口契约，再实现逻辑；
4. Node 工具改动跑全部旧测试 + 新测试；
5. Mock HTTP 测协议，真实 Node HTTP/目录做跨语言契约；
6. 完成后列修改文件、实现内容、测试结果、未解决问题；
7. 更新 `docs/`、`.env.example`、`AGENTS.md` 必要规范。

### 3.2 遇到的问题与解决

| 问题 | 解决 |
| --- | --- |
| 工具 Schema 两边漂移 | Node 注册表唯一来源，Python 动态拉取 |
| 模型返回非法/未知工具 | 严格 ToolCall/JSON/Schema/注册名校验，整批通过才执行 |
| 模型一次返回多个工具 | 1–8 个串行执行；超过 8 个整批拒绝 |
| “多跳几下”被误判重复 | 只拒绝相同 `tool_call_id`，相同参数不同 ID 允许 |
| 每条消息无上下文 | 事件溯源 Session + 派生历史 + SQLite |
| 长对话上下文膨胀 | 完整 turn 压缩成摘要，保留近期尾部 |
| 日志只有错误码 | `rid`、阶段、错误链、脱敏堆栈、模型输出预览 |
| 工具“调用成功”不等于完成 | `status`、快照/背包验证、`pending/uncertain` 不报成功 |
| 测试无法依赖真实游戏/模型 | Mock HTTP + 真实 Node 契约测试 + 独立游戏验收清单 |

### 3.3 后续计划

**P0：主链路**

1. **Planner 深化**：计划持久化到会话、DAG/子任务依赖、跨轮继续；把规划结果接入 `/api/agent/tasks` 的统一执行入口。
2. **Skill 自然语言路由**：把 4 个 Skill 作为受控能力暴露给模型；歧义先澄清。
3. **真实 Minecraft 验收**：移动、挖掘/拾取、合成、熔炼、容器、攻击、停止/取消、死亡/断线。

**P1：能力扩展**

- 更多 Skill：建造、战斗/安全、复杂合成；
- 记忆增强：会话引用、长期事实/偏好、归档/保留策略、供应商 token usage；
- 演示与性能数据：首 token 延迟、工具耗时、压缩频率。

### 3.4 Lab B 自评

| 检查点 | 结论 | 证据/风险 |
| --- | --- | --- |
| Agent 架构 | 基本合理 | Node 唯一游戏控制、Python 唯一 LLM 编排、HTTP 边界、Schema 单一来源 |
| 技术选型 | 有依据 | Mineflayer、FastAPI/Pydantic、OpenAI 兼容 function tools、SQLite |
| 核心 Runtime | 跑通 | 299 Python / 180 Node；direct/plan/错误回填/finish 均有测试 |
| 工具调用 | 有效、有界 | 39 工具；校验、互斥、超时、取消、真实结果回填 |
| 记忆 | 有效（Tool Calling） | 事件日志 + 压缩 + SQLite；两轮对话可引用上一轮 |
| 规划 | 已接入（单轮） | Planner direct/plan、update_plan、finish；计划持久化/DAG 待做 |
| Skill 自然语言选择 | 未完成 | 只能显式 HTTP/脚本调用 |
| 真实游戏验收 | 待完成 | 自动化/Mock 不等同于 Minecraft；演示前需现场跑 |

总体判断：MineMate 已经具备**Planner + Executor 任务运行时、统一工具层、4 个确定性 Skill、事件溯源记忆和显式后台执行底座**，不是单次工具调用 Demo。下一阶段重点是 **计划持久化/DAG、自然语言 Skill 路由和真实游戏验收**。

---

## 附录 A：关键文件

| 文件 | 作用 |
| --- | --- |
| `minecraft-bot/src/tools/` | 39 个工具、注册表、Schema、会话/租约 |
| `minecraft-bot/src/agentclient.js` | Node → Python 消息调用 |
| `agent/app/main.py` | FastAPI 入口、记忆/任务/Skill 路由 |
| `agent/app/message_handler.py` | 限流、并发、每玩家 turn |
| `agent/app/tool_loop.py` | 核心 Agent Loop |
| `agent/app/llm_client.py` | Chat Completions / function tools / 摘要 |
| `agent/app/tool_client.py` | Node 工具目录、执行、取消、回执 |
| `agent/app/execution/` | 显式步骤后台执行、租约、账本、引用 |
| `agent/app/memory/` | 事件日志、上下文、压缩、SQLite |
| `agent/skills/` | Skill 契约、注册、4 个业务 Skill |
| `docs/tools-guide.md` | 39 个工具的参数、限制、组合流程 |
| `docs/memory.md` | 记忆与上下文设计 |
| `docs/logging.md` | 日志级别、请求 ID、排查 |

## 附录 B：常用接口

```http
POST /api/agent/message
{"player":"Steve","message":"看看附近有没有橡木"}

POST /api/agent/tasks
{"player":"Steve","request_id":"task-1","steps":[{"step_id":"s1","tool":"query_status","arguments":{}}],"verify_inventory":[]}

POST /api/agent/skills/execute
{"player":"Steve","request_id":"gather-1","skill":"gather_resource",
 "arguments":{"resource":"oak_log","count":2}}

GET  /api/agent/memory/Steve
POST /api/agent/memory/Steve/reset
```

## 附录 C：测试命令

```powershell
# Python
cd agent
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s test -p "test_*.py" -v

# Node
cd minecraft-bot
npm test
```

> 本文基于 2026-11-23 当前代码与测试结果整理。自动化测试使用 Mock/回环 HTTP 和本地 Node 契约测试；真实 Minecraft 与付费模型验收需按仓库清单另行执行。
