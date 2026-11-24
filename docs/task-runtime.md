# 任务执行架构（Planner + Executor + Finish）

本轮把 Agent 从“单个有界工具循环”升级为“先判断、必要时规划、执行、按错误调整、显式结束”的任务运行时。目标：

- 简单任务直接执行，不为形式强行规划；
- 复杂任务先产生结果导向的计划，再逐步行动；
- 工具失败/pending/uncertain 作为 observation 回填，不直接结束；
- 任务是否结束由模型调用 `finish_task` 决定；
- Harness 只做兜底，保证死循环、连续错误、调用超限、超时一定会结束。

## 1. 入口与模式

```text
Node 聊天
  → Python /api/agent/message
  → MessageHandler
      ├─ AGENT_EXECUTION_MODE=planned → AgentRuntime（本轮新增，默认）
      └─ AGENT_EXECUTION_MODE=legacy  → ToolLoop（旧行为，兼容/回归）
```

`AgentRuntime` 不直接操作 Minecraft；所有游戏动作仍经 Node Tool Registry。
Skill 目录和后台 `TaskManager` 未改动。

## 2. 一次任务的生命周期

```text
1. 获取工具目录 + 世界快照
2. 从记忆会话派生上下文
3. Planner 判断 direct / plan
      direct → 不生成计划
      plan   → 生成 1–8 个结果导向步骤
4. Executor 循环：
      LLM 看到当前计划、真实工具结果和 finish/update_plan 工具
      ├─ 普通工具 → 校验 → Node 执行 → 结果回填
      ├─ update_plan → 替换剩余计划
      └─ finish_task → 模型宣布结束
5. Harness 在步数/调用/时间/连续错误/重复失败超限时兜底结束
```

计划不是固定工具序列，而是**检查点**。例如“做木斧头”可以规划为“准备木板/木棍 → 准备工作台 → 合成木斧”，具体工具仍由 Executor 根据真实结果选择。

## 3. Planner

`Planner` 只做分类与计划，不执行工具：

```json
{"mode":"direct","reason":"一步就能完成"}
```

或：

```json
{"mode":"plan","goal":"做一把木斧头","steps":[
  {"title":"准备木板和木棍","success_criteria":"背包有足够材料"},
  {"title":"准备好工作台","success_criteria":"工作台已放置且可交互"},
  {"title":"合成木斧","success_criteria":"背包新增木斧"}
]}
```

- 只看到工具名称/描述摘要，不直接调用工具；
- 输出经过严格 Pydantic 校验；
- 最多 8 步；步骤必须是结果目标，不写坐标/内部参数；
- 规划失败最多重试 2 次，再回退 direct（并记录 warning）。

## 4. Executor 与错误回填

每轮模型可以返回 1–8 个工具调用，Executor 顺序执行：

- 参数、注册名、Node Schema、重复 call id 逐项校验；
- 工具失败不会结束任务，而是把真实 `{success:false,error}` 写入 `role=tool` 历史；
- `pending` 也不会结束，模型可以调用 `get_movement_task` 等查询；
- `uncertain` 作为不确定结果回填，模型可以查询或调整计划；
- 相同工具+参数+错误重复超过 `TASK_MAX_SAME_ERROR` 时，不再执行该动作，回填 `REPEATED_FAILURE`；
- 连续错误达到阈值会追加编排器提示，要求换方案/改计划/结束。

模型可调用 `update_plan` 替换剩余步骤，Harness 校验后追加新计划上下文。

## 5. 谁决定任务结束

模型必须调用控制工具：

```json
{"name":"finish_task","arguments":{
  "status":"completed|partial|blocked",
  "summary":"给玩家的简短结果",
  "remaining":"可选的未完成事项"
}}
```

- 只有 `finish_task` 是正常结束路径；普通文字不会自动结束；
- 模型只输出文字时，Harness 会提示“如果结束请调用 finish_task”；
- 连续两次只输出文字仍未结束时，Harness 兜底结束；
- Harness 兜底只产生“先到这儿/任务还没完全结束”之类的诚实回复，不把未完成说成完成；
- 模型决定 `completed` 后，Harness 不额外做目标验证；真实工具结果仍由模型在 summary 中负责核对。

## 6. 兜底与质量属性

| 兜底 | 默认 | 作用 |
| --- | ---: | --- |
| `TASK_MAX_STEPS` | 12 | 模型调用轮数上限 |
| `TASK_MAX_TOOL_CALLS` | 24 | 实际 Node 工具调用上限 |
| `TASK_TIMEOUT` | 120s | 整个任务硬超时 |
| `TASK_MAX_CONSECUTIVE_ERRORS` | 3 | 连续失败提示/兜底 |
| `TASK_MAX_SAME_ERROR` | 2 | 同一失败动作不无限重试 |
| `TASK_MAX_REPLANS` | 3 | 计划修改次数上限 |
| `TASK_MAX_TEXT_ONLY` | 2 | 只输出文字不结束的容忍次数 |

任一上限触发都会：取消正在执行的 Node 动作（若有）、关闭记忆 turn、返回明确的部分结果，不会无限循环。

Node 侧 `AGENT_TIMEOUT_MS` 默认已调整为 150000；如果它小于 `TASK_TIMEOUT`，Node 会先断开 HTTP，Agent 任务会被取消。修改超时后需要同时重启 Node/Python。

## 7. 与记忆的关系

- 任务使用原有 `MemoryManager`：begin turn、system prompt、历史上下文、tool call/result、finish 后的最终 assistant 都写入事件日志；
- `update_plan` 和 `finish_task` 是 Python 控制工具，也在记忆里留下 tool call/result；
- 计划本身目前是**单轮内**上下文，不单独持久化成 plan 事件；跨轮继续复杂任务仍依赖对话历史/摘要；
- 记忆上下文不可用时回退到无记忆上下文，任务运行不被阻断。

## 8. 路径选择与兼容

- `planned`：默认，使用 Planner + Executor + finish/update_plan + 兜底；
- `legacy`：保留原 ToolLoop（失败即结束、单轮调用预算），用于回归或临时降级；
- Skill `/api/agent/skills/execute` 和后台 `/api/agent/tasks` 完全不动；
- Skill 的自然语言自动路由仍不在本轮范围。

## 9. 测试

```powershell
cd agent
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s test -p "test_*.py" -v
```

新增覆盖：

- Planner direct/plan/非法输出回退；
- Runtime direct、plan 上下文、工具错误回填、update_plan、finish、非法 finish 纠正；
- 步数/调用/重复错误/只输出文字兜底；
- 记忆 turn 正常关闭；
- 配置边界和 LLM 规划协议。

Mock 测试不等于真实 Minecraft 验收。
