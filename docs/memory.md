# Agent 记忆与上下文

本模块把 MineMate 从“无状态请求处理器”升级为**按玩家持久化的会话 Agent**。设计按 DSH 的核心契约实现：

- **Session 事件日志是唯一真源**，LLM 消息历史从日志派生，不单独维护；
- **Turn / Step 生命周期**显式记录，崩溃后可修复；
- **Context 贡献**（系统提示、当前世界、时间、压缩摘要、最近对话）在每次请求时组装；
- **Compaction** 在接近上下文窗口时把最旧完整轮次替换为摘要，保留近期尾部；
- **SQLite 持久化**，按玩家隔离，重启后继续；
- **Token 估算**用于触发压缩，而不是等供应商报错。

这不是 Cordis/TypeScript 包的逐行移植；MineMate 侧用 Python/Pydantic/stdlib sqlite3 实现了同一组语义，省略了 DSH 中与代码编辑、文件引用、子会话引用相关的部分。

## 1. 代码结构

```text
agent/app/memory/
  types.py       事件类型、SessionEvent/SessionRecord、错误分类
  session.py     仅追加日志、消息派生、中断修复
  store.py       EventStore 接口、SQLite 与内存实现
  tokens.py      保守 token 估算、文本截断
  context.py     系统提示补充、世界快照压缩、时间/世界上下文
  compaction.py  压缩选择、摘要器、compaction 事件
  manager.py     按玩家加载/锁/持久化/上下文/压缩编排
```

集成点：

- [ToolLoop](../agent/app/tool_loop.py) 在每轮聊天中开始/记录/结束 turn 和 step；
- [main.py](../agent/app/main.py) 在 lifespan 中初始化/关闭 `MemoryManager`，并提供查询/重置接口；
- [config.py](../agent/app/config.py) 提供 `MEMORY_*` 配置与边界校验。

## 2. 事件模型

每个玩家一个会话，每个会话是一份 `seq` 连续的仅追加日志。`time` 为 epoch 秒。

| 事件 | 作用 |
| --- | --- |
| `session/created` | 会话创建 |
| `system/message` | 渲染后的系统提示；提示变化时追加新节点，派生时取最新 |
| `turn/start` / `turn/end` | 一条玩家消息到最终回复的生命周期 |
| `user/message` | 玩家原文（或未来注入的用户角色上下文） |
| `step/start` / `step/end` | 一次模型调用及其工具执行 |
| `assistant/message` | 模型回复（content 或 tool_calls） |
| `tool/call` | 模型请求的工具调用（仅日志审计；surface 由 assistant 承载） |
| `tool/result` | 与 assistant tool_call 配对的真实工具结果 |
| `compaction/start` | 压缩开始（持久锁标记） |
| `compaction/summary` | 摘要 + 被遮蔽事件 seq + token 记账 |
| `compaction/end` | 压缩结束；失败时带 `error` |

消息历史由 `Session.derive_messages()` 生成：

1. 最新 `system/message` 作为 system；
2. 若存在 `compaction/summary`，插入一条带 `<compacted-summary>` 的用户角色检查点；
3. 按日志顺序投影未被遮蔽的 `user/message`、`assistant/message`、`tool/result`；
4. 工具调用必须按 `call_id` 全部配对，缺结果的块整块丢弃并告警；
5. 超过 `MEMORY_MAX_TOOL_RESULT_CHARS` 的历史工具结果截断为带 `_truncated` 的 JSON；
6. 最后追加**不持久化**的当前世界快照和本地时间，作为本轮上下文。

## 3. Turn 生命周期

```text
Node 消息
  → MemoryManager.begin_turn(player, message)
      加载/创建会话，修复未闭合 turn，追加 turn/start + user/message
  → MemoryManager.build_context(...)
      确保系统提示最新；必要时压缩；派生消息；追加世界/时间
  → 每轮模型调用
      start_step → record_assistant(tool_calls) → record_tool_result(...) → end_step
  → 最终回复
      record_assistant(final reply) → end_turn(completed)
  → 异常/取消/超时
      end_turn(error/cancelled/timeout)
```

同一玩家在 `MessageHandler` 中只允许一个进行中的 turn（`PLAYER_BUSY`），避免两条消息同时写同一个会话。不同玩家拥有独立会话和独立锁。

## 4. 压缩

触发条件：

```text
估算 tokens(messages) + tokens(tools) > context_window × threshold_ratio
```

默认 `threshold_ratio=0.75`，`retain_turns=6`。压缩只选择**当前 turn 之前、已经结束、未被上一次摘要遮蔽的完整 turn**，不会拆开一次 tool_call 与它的结果。

压缩流程：

1. 追加 `compaction/start`；
2. 提取选定轮次为有界 transcript；
3. 优先调用模型生成结构化中文摘要；模型不可用或失败时使用提取式摘要；
4. 摘要必须实际缩短上下文，否则视为失败；
5. 追加 `compaction/summary`（`shadowed_seqs`、`tokens_before/after`、`summarizer`、`source`）；
6. 追加 `compaction/end`。

失败时追加带 `error` 的 `compaction/end`，保留原历史，当前聊天继续。压缩事件即使在失败时也会持久化，避免 seq 空隙导致后续写入失败。

原始事件不会被删除；摘要只是通过 `shadowed_seqs` 把它们移出模型可见 surface。后续压缩会把上一份摘要并入新摘要，并遮蔽上一份摘要事件。

## 5. 持久化与恢复

SQLite 默认位于 `data/minemate-memory.sqlite3`，文件权限尝试设为 `0600`，`.gitignore` 排除 `data/`。

```sql
memory_meta(key, value)                 -- 格式版本
memory_sessions(id, player, created_at, updated_at, last_seq)
memory_events(session_id, seq, time, type, data)  -- data 为 JSON
```

- 每个事件写入前校验 JSON 可序列化、seq 连续；
- 追加在 `BEGIN IMMEDIATE` 事务中完成；
- WAL、`foreign_keys=ON`、`synchronous=NORMAL`；
- 进程内用 `threading.RLock` 串行化 SQLite 访问，异步层用 `asyncio.to_thread`；
- 每个玩家一个 `asyncio.Lock`，避免并发 turn；
- 加载时 `Session.repair()` 关闭残留的 step/turn/compaction 标记；
- 未完成的 assistant/tool 配对不从派生 surface 发送，避免协议错误；
- 数据库损坏或磁盘不可写时，记忆操作降级并记录日志，聊天继续，不把 Bot 卡死。

`MEMORY_SESSION_IDLE_DAYS` 控制会话切分；默认 30 天，0 表示不按时间切分。旧会话仍保留在数据库中，但新消息会创建新会话。

## 6. API

```text
GET  /api/agent/memory/{player}
POST /api/agent/memory/{player}/reset
```

查询返回事件数、turn/step 数、摘要长度、最后 seq、是否降级和摘要预览；重置删除该玩家当前会话。接口面向本机可信调用方，不提供跨玩家读取。

## 7. 配置

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MEMORY_ENABLED` | `true` | 关闭后不创建 MemoryManager |
| `MEMORY_DB_PATH` | `data/minemate-memory.sqlite3` | 空值使用默认路径 |
| `MEMORY_CONTEXT_WINDOW` | `32768` | 估算上下文窗口，1024–1,000,000 |
| `MEMORY_COMPACTION_THRESHOLD` | `0.75` | 触发比例，0.1–0.95 |
| `MEMORY_RETAIN_TURNS` | `6` | 逐字保留的最近完整轮次，1–64 |
| `MEMORY_MAX_SUMMARY_CHARS` | `4000` | 摘要上限，256–16000 |
| `MEMORY_MAX_TOOL_RESULT_CHARS` | `4000` | 派生历史中单个工具结果上限 |
| `MEMORY_SESSION_IDLE_DAYS` | `30` | 会话空闲切分天数，0 关闭 |
| `MEMORY_AUTO_COMPACT` | `true` | 关闭后不自动压缩，仍可手动清理数据库 |

记忆只在 `TOOL_CALLING_ENABLED=true` 的 Tool Calling 路径生效。`TOOL_CALLING_ENABLED=false` 的旧意图模式保持原行为，不读取/写入会话。

## 8. 已知边界

- 没有向量检索、用户画像、跨玩家共享记忆；长期事实靠压缩摘要保留。
- 没有对供应商 `usage` 的回放锚定；token 用保守启发式估算，可能偏大。
- 没有 DSH 的 request header、surface replace 操作；`tool/call` 已有独立审计事件，模型可见的工具调用仍由 assistant 事件承载，语义等价。
- 单次压缩只做一轮选择；如果保留的近期轮次本身超过窗口，仍可能溢出。当前通过工具结果截断和 6 轮保留降低风险。
- 事件日志只追加不清理；长期运行需要后续增加归档/保留策略。
- 压缩摘要由模型生成时会产生一次额外模型调用；失败会退化为提取式摘要。

## 9. 验证

```powershell
cd agent
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s test -p "test_*.py" -v
```

关键测试：

- `test_memory.py`：事件派生、工具配对、摘要遮蔽、SQLite 重开、压缩、按玩家隔离、自动压缩接入 ToolLoop；
- `test_message_handler.py`：同一玩家并发 `PLAYER_BUSY`；
- `test_tool_loop.py`：记忆关闭时原行为不变。

Mock/回环测试不等于真实 Minecraft 验收；实际游玩前建议先发送两条有指代关系的消息（例如“我叫 Alice”/“我叫什么”）并检查 `GET /api/agent/memory/{player}`。

## 10. 与 DSH 的对应关系

| DSH 契约 | MineMate 实现 |
| --- | --- |
| `packages/core/session`：仅追加事件日志、surface 投影 | `memory/session.py`、`memory/types.py` |
| `core/agent-loop`：turn/step 边界、tool call/result | `tool_loop.py` 调用 `MemoryManager` 记录 |
| `compaction/compaction` 与 `compaction-basic` | `memory/compaction.py` |
| `llm/token-meter`：压力测量 | `memory/tokens.py` 保守估算 |
| session persistence / storage | `memory/store.py` 的 SQLite 事件表 |
| `context/time-context` | `memory/context.py` 的本地时间消息 |
| 世界/运行环境上下文 | `memory/context.py` 的游戏快照投影 |
| `context/agent-instructions` | 未实现；继续使用静态 `TOOL_PROMPT` |
| `context/session-reference` | 未实现；当前只有单会话历史 |
| `storage-domain` 的通用 KV/领域层 | 未实现；MineMate 只需要事件日志，直接建表 |

### 有意保留的差异

- DSH 的 surface 支持 `replace`/shadow 操作和任意历史内 system 节点；MineMate 用 `shadowed_seqs` + 最新 summary 表达同样的可见性，system 始终取最新一条。
- DSH 的 token meter 会复用 provider usage 作为锚点；MineMate 没有保存 usage，使用保守启发式估算。
- DSH 的 compaction 支持区域压缩、图片省略、模型切换策略；MineMate 第一版只做按完整 turn 的自动压缩。
- DSH 的会话可以 fork/引用其他会话；MineMate 每个玩家一个连续会话，暂不支持跨会话引用。

这些差异是刻意收敛的范围，不改变“事件日志是唯一真源、历史可派生、压缩可回放”的核心合同。后续迭代可以在不改上层 ToolLoop 的情况下替换 `EventStore`、`Summarizer` 或增加 context provider。
