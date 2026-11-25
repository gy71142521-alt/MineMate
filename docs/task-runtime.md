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

`AgentRuntime` 不直接操作 Minecraft；所有游戏动作仍经 Node Tool Registry。Runtime 通过 Python 控制工具 `execute_skill` 动态暴露 Skill Registry，Skill 再复用 SkillManager/TaskManager 和 Node Tool Registry。

## 2. 一次任务的生命周期

```text
1. 获取工具目录 + 世界快照
2. 从记忆会话派生上下文
3. Planner 判断 direct / plan
      direct → 不生成计划
      plan   → 生成 1–8 个结果导向步骤
4. Executor 循环：
      LLM 看到当前计划、真实工具结果、execute_skill 和 finish/update_plan 工具
      ├─ 普通工具 → 校验 → Node 执行 → 结果回填
      ├─ execute_skill → Skill 参数校验 → SkillManager 执行/核验 → 结果回填
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

如果目标与已注册 Skill 匹配，模型优先调用：

```json
{"name":"execute_skill","arguments":{
  "skill":"craft_item",
  "arguments":{"item_name":"crafting_table","count":1}
}}
```

Runtime 会等待 Skill 进入终态，把 `verification`、工具账本和真实错误作为 `role=tool` 回填。Skill 失败或不确定时不会自动变成成功；取消、超时和客户端断开会取消在途 Skill。

Runtime 在读取 Skill 的终态后还会等待 `TaskManager` 的清理收尾。Skill 的验证可能先写入 `completed`，Node 租约随后才释放；如果不等待，下一步放置熔炉或启动熔炼会收到 `BUSY`。因此只有租约释放完成后，Runtime 才把结果交给模型继续下一步。

正常释放租约时 Node 保持当前 `generation`，因为同一个 Runtime 任务可能随后继续调用放置或其他 Node 工具。显式取消、租约过期和 Bot 生命周期变化才递增 generation，使迟到的旧请求返回 `STALE_TASK`。

### 4.1 Skill 优先路由

`planned` 模式下，direct 和 plan 共用同一策略。Planner 和 Executor 的工具列表均将动态 `execute_skill` 放在首位；Planner 的摘要额外保留全部 Skill 名称，避免描述截断后丢失后面的能力。

| 目标 | 首选入口 | 底层入口的处理 |
| --- | --- | --- |
| 按名称/类型搜索 | `explore_and_find` | 隐藏 `find_block`；`find_entity` 为战斗索敌保留并注明优先规则 |
| 按物品及数量采集 | `gather_resource` | `mine_block` 保留供明确坐标的单方块挖掘；描述与系统提示要求资源采集优先 Skill |
| 合成物品 | `craft_item` Skill | 隐藏 Node 同名工具，包含不需要工作台的中间物品合成 |
| 开始熔炼/投料 | `smelt_item` Skill | Node 同名工具只暴露 status/take；独立补料/补燃料子工具保留 |

仅在相应 Skill 已注册时应用规则。不修改 Node 注册表、HTTP 接口、固定聊天指令或 Skill 内部可调用的完整工具目录。旧记忆或模型仍输出原生 `craft_item`/`find_block`/`smelt_item(start)` 时，Runtime 回填 `SKILL_REQUIRED`，不执行游戏操作，也不擅自把 Node 的 `position` 参数转换成 Skill 的 `table_position`。不能因 Skill 失败就自动退回底层重做副作用；应读取真实缺口、补充前置条件后重新选择。

例如“制作一个铁胸甲”：材料和工作台已就绪时调用 `execute_skill(craft_item, {item_name: iron_chestplate, count: 1})`。若工作台只在背包中，可先检查放置位置、用 `place_block` 放好，再调用合成 Skill。若缺材料，模型先按真实配方安排采集或中间物品合成；Skill 本身不会递归准备材料。看到 Node 日志中的底层 `craft_item` 是正常现象，因为 Skill 内部仍必须调用它，判断是否走 Skill 应查看 Python 的 `[Skills] 模型选择` 日志。

重启 Python 后，在测试世界检查上述铁胸甲任务，再检查采集木头、找工作台和熔炼粗铁；确认出现对应 Skill 名称/参数。回归具体坐标挖掘、跟随/停止、放置工作台、单独补煤和取出产品。采集、实体搜索和独立熔炉操作仍保留底层能力，语义选择依赖模型遵循提示；自动化路由测试不等于真实模型或 Minecraft 验收。

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
- Harness 兜底只产生“我先停一下，还没弄完”之类的诚实回复，不把未完成说成完成；
- 模型决定 `completed` 后，Harness 不额外做目标验证；真实工具结果仍由模型在 summary 中负责核对；
- 若模型在 `finish_task` 前已经写了自然语言回复，`completed` 时优先用那句，避免再用工具 summary 覆盖成状态播报；`partial/blocked` 仍用显式 summary，防止把未完成说成完成。

## 6. 兜底与质量属性

| 兜底 | 默认 | 作用 |
| --- | ---: | --- |
| `TASK_MAX_STEPS` | 24 | 模型调用轮数上限 |
| `TASK_MAX_TOOL_CALLS` | 32 | 实际 Node 工具调用上限 |
| `TASK_TIMEOUT` | 120s | 整个任务硬超时 |
| `TASK_MAX_CONSECUTIVE_ERRORS` | 3 | 连续失败提示/兜底 |
| `TASK_MAX_SAME_ERROR` | 2 | 同一失败动作不无限重试 |
| `TASK_MAX_REPLANS` | 3 | 计划修改次数上限 |
| `TASK_MAX_TEXT_ONLY` | 2 | 只输出文字不结束的容忍次数 |

任一上限触发都会：取消正在执行的 Node 动作（若有）、关闭记忆 turn、返回明确的部分结果，不会无限循环。

导航额外有任务级保护：`goto_position` 的 `NAVIGATION_FAILED` / `TARGET_OCCUPIED` 累计 3 次会提示模型停止重复，累计 5 次直接兜底结束；`arrival_range=0` 的目标脚部/头部被占用时 Node 直接拒绝，不再交给寻路器反复失败。

Node 侧 `AGENT_TIMEOUT_MS` 默认已调整为 150000；如果它小于 `TASK_TIMEOUT`，Node 会先断开 HTTP，Agent 任务会被取消。修改超时后需要同时重启 Node/Python。

## 7. 运行中插话（Steering）

玩家在任务执行中发来的自然语言消息，不再一律返回 `PLAYER_BUSY`：

```text
Node 发现该玩家有活动 Agent 请求
  → POST /api/agent/steer（短请求，不中止原请求）
  → Runtime 把消息放入该玩家 next-step 队列
  → 当前工具批次结算后、下一次模型调用前：
      作为一条 user 消息插入当前 turn
      写入记忆 user/message
  → 模型看到“玩家插话”，自行决定停止、换目标或继续
```

边界和语义：

- 插话只在**工具批次完成后**插入，不会打断正在执行的 Node 工具；
- 不自动取消当前 turn，也不由 Harness 解析“停止/换目标”；由模型根据插话语义决定 `stop_movement` / `update_plan` / `finish_task`；
- 每个 turn 最多排队 8 条；`POST /api/agent/steer` 返回 `{accepted:true|false}`；
- 没有活动 turn 时 Node 回退到普通 `/api/agent/message`；
- 插话写入当前 turn 的 `user/message`，因此下一轮记忆能看到；
- 这是内存 inbox，不是跨重启持久队列；任务刚好结束时的竞态可能丢弃已排队插话；
- 固定 `停止` 仍是最高优先级的旧指令路径：它会 abort 当前请求并停止移动，不等模型处理。

## 8. 与记忆的关系

- 任务使用原有 `MemoryManager`：begin turn、system prompt、历史上下文、tool call/result、finish 后的最终 assistant 都写入事件日志；
- `update_plan` 和 `finish_task` 是 Python 控制工具，也在记忆里留下 tool call/result；
- 计划本身目前是**单轮内**上下文，不单独持久化成 plan 事件；跨轮继续复杂任务仍依赖对话历史/摘要；
- 记忆上下文不可用时回退到无记忆上下文，任务运行不被阻断。

## 9. 路径选择与兼容

- `planned`：默认，使用 Planner + Executor + finish/update_plan + 兜底；
- `legacy`：保留原 ToolLoop（失败即结束、单轮调用预算），用于回归或临时降级；
- Skill `/api/agent/skills/execute` 和后台 `/api/agent/tasks` 完全不动；
- Skill 的自然语言自动路由仍不在本轮范围。

## 10. 测试

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
