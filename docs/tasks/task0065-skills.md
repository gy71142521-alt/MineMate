# Task 006.5：Skill 系统

## 阶段 A 交付范围

阶段 A 建立了 Skill 接口、独立注册表、严格参数校验、执行适配、后台 API 和最终目标核验；当时生产注册表为空。当前已进入阶段 B，生产目录注册了 `explore_and_find`，详见后文。其余三个业务 Skill、LLM Skill 选择、Minecraft 进度回复在后续阶段接入。Task 006 的自然语言 Tool Calling、本地聊天指令和显式后台任务接口继续使用原行为。

没有新增依赖，没有修改 `.env`、密钥或认证缓存。Python 仍只通过 Node 工具执行游戏操作。

## 目录与职责

```text
agent/
  skills/
    __init__.py       # 公共导出
    base.py           # Skill 契约及执行/验收钩子
    models.py         # 参数、请求、检查证据、结果模型
    registry.py       # 独立注册与 Schema 生成
    runtime.py        # SkillManager 与受限 Tool 适配器
    catalog.py        # 生产 Skill 集中注册
    explore_and_find/ # 每个业务 Skill 独立目录
      __init__.py    # 保持旧公开导入路径
      metadata.py    # 运行时元数据和参数模型
      skill.py       # 阶段 B：有界搜索与接近
      SKILL.md       # 技能说明、输入与命令示例
      SOP.md         # 工具顺序、分支与验收
  app/
    execution/
      models.py      # 共用 ExecutionRequest，保留原 TaskRequest
      manager.py     # 共用执行槽及 ExecutionContext
    main.py          # API 装配
  test/
    test_skills.py   # 测试专用 Skill、Mock HTTP 与真实 Node HTTP 集成
    test_explore_and_find.py # 搜索 Skill 的单元及 HTTP 集成测试
scripts/
  test-explore-skill.ps1 # 手动提交与轮询真实 Skill 的测试入口
```

`Skill` 必须声明 `name`、`description`、`parameters`、`preconditions`、`success_criteria`、`required_tools`。参数模型继承 `SkillParameters`，拒绝额外字段、字符串数字和非有限数值。

执行顺序为：验证 Skill/参数 → 获取真实 Node 工具目录 → 核对依赖工具 → 取得原执行器租约 → 读取快照/检查前置条件 → 确定性 execute → 读取新快照 → verify 全部完成条件 → 释放租约。只有独立核验通过，才能标记 `completed`。

Skill 的 `execute(runtime, parameters)` 返回普通 JSON 对象；`verify(parameters, data, before, after)` 返回 `SkillVerification`，每个检查包含 criterion、passed、observed。criterion 必须覆盖该 Skill 声明的所有完成条件，不允许空验收。具体 Skill 的材料、目标、设施检查由各自的 `check_preconditions` 实现，通用层负责 Bot 可用性和依赖工具存在性。

`SkillRuntime.call(tool, arguments, operation_id=..., wait_seconds=...)` 通过原执行器调用工具。参数按当前 Node Schema 再校验；同一 operation_id/参数重复只返回原结果，变更参数则报冲突。相同参数的两次有意操作必须使用不同 ID，调用数量受总预算限制。未知结果查询原回执，不能更换 ID 重发修改工具。

导航 pending 复用 `get_movement_task` 等待原 taskId；熔炼 start 的通用等待已包含 status/take，后续 Skill 不可再次取出。持续跟随和抢占停止不能插入 Skill 内部，停止通过现有外部停止/取消入口处理。

## HTTP 接口

服务仍按原方式启动：在 `agent` 中执行 `.\.venv\Scripts\python.exe -u -m app.main`，Node 使用原启动命令。开发阶段可继续使用项目的一键启动器。

| 方法与路径 | 行为 |
| --- | --- |
| GET `/api/agent/skills` | 返回已注册 Skill 的描述、参数 Schema、前置条件、完成条件和依赖工具 |
| POST `/api/agent/skills/execute` | 严格验证请求后提交后台执行，HTTP 202 返回执行 ID |
| GET `/api/agent/skills/executions/{execution_id}?player=Steve` | 查询该玩家的进度、账本、结果和核验证据 |
| POST `/api/agent/skills/executions/{execution_id}/cancel` | 请求体 `{"username":"Steve"}`，取消后续调用并释放对应租约 |

外层保持 `{success,data,error}`；HTTP 202 外层 success 仅表示提交成功。内层 SkillResult.success 只有最终状态 completed 时才为 true；pending/running 时 success=false、error=null，不表示请求失败。

请求合同示例（`craft_item` 要到阶段 D 才注册，当前提交将得到 UNKNOWN_SKILL）：

```json
{
  "player": "Steve",
  "request_id": "craft-001",
  "skill": "craft_item",
  "arguments": {},
  "timeout_seconds": 300,
  "max_tool_calls": 96
}
```

参数由后续各 Skill 的实际 Schema 定义，不复用原子工具参数合同。Skill 名称和 Node 工具同名时仍属于不同目录；阶段 F 会使用明确前缀向 LLM 导出，避免碰撞。

内层结果稳定包含 execution_id、player、request_id、skill、success、status、data、error、verification、steps、calls、created_at、updated_at。status 为 pending/running/completed/failed/uncertain/cancelled。错误保留真实错误码；工具失败详情在 steps 中，未知异常只给固定提示，不输出内部堆栈。

请求体最多 16 KiB，执行总时限默认 300 秒、最大 600 秒，工具调用默认 96 次、最大 192 次；单次等待最多 180 秒，轮询也消耗调用预算。与显式后台任务共用单执行槽和最多 64 条内存记录；不排队、不自动抢占、不跨重启恢复。相同玩家/request_id 重复提交返回原记录，内容或请求种类不同则 REQUEST_CONFLICT。

## 已知风险及后续解决方案

以下记录阶段 B～F 的风险方案。阶段 B 的有限搜索、站位接近、实体解析及进度观察已实现，后续采集/合成/熔炼与 LLM 接入仍按表中阶段推进；不依赖 Memory。

| 风险 | 处理方案 | 实施阶段 |
| --- | --- | --- |
| 搜索不到就被误认为世界没有资源 | find_block/find_entity 当前上限 16 格，只说明本次可见范围内未发现；仅搜索模式不移动。探索模式增加有界路点、已访问坐标集合、移动距离与调用预算，逐点移动后再查询。未知区块不能当空气，无路候选跳过，预算用尽报告实际覆盖范围 | B |
| 模组/中文/“铁”有歧义 | LLM 仅提取目标短语，Node resolve_resource 验证真实名称。当前匹配是名称/显示名的文本排序，置信度不是概率，且通常显示名是英文；不能声称已提供完整中文语义解析。严格区分目标产物、输入原料、矿石方块；根据真实背包和 inspect_smelting_recipe 的结果选择，多个有效解释时澄清。需要实体解析时先核对 entitiesByName，再最小扩展 Node 的只读解析接口 | B、D、E |
| 搜索到但走不过去，或站到目标脚下卡住 | 共用有限候选站位选择：粗靠近后 find_interaction_positions，最多尝试有限候选，每次导航结束后重新检查距离、视线和目标是否改变。排除目标方块上方及当前脚下支撑；UNSAFE_TARGET/NO_LINE_OF_SIGHT 触发有限换站位或换资源目标，不反复挖同一位置。不能改成允许破坏脚下支撑 | B、C、D、E |
| 挖掉方块却没有收到资源 | mine_block 只代表方块移除。先记录期望掉落物数量，再挖、检查是否已经自动拾取、必要时 collect_item，最后重新查背包。不能把方块名当掉落物名（如 stone→cobblestone、iron_ore→raw_iron）；以实际查询到的掉落候选或明确用户参数为准。遇到精准采集、时运和模组未知掉落规则时澄清，不硬编码整套掉落表 | C |
| 资源候选被玩家挖掉，掉落物消失 | 每个交互前重新检查目标。记录已失败/已尝试候选并在本 Skill 内跳过，最多有限轮数；实体消失没有背包增量时只能 uncertain，不能确认拾取成功。这里的临时集合是执行状态，不是长期 Memory | B、C |
| 采集数量理解不一致 | gather_resource.count 表示背包目标总数，扣除已有数量；craft_item/smelt_item.count 表示本次期望新增产物数。每个 Skill 在参数描述和结果中明确 total/increase，核验使用同一口径。多产物配方按整批取整，报告实际数量，不虚报精确数量 | C、D、E |
| 工作台存在但材料不足/设施不可达 | 先 inspect_recipe，区分背包配方和 requiresTable，返回材料缺口；需要工作台才搜索、接近和重新检查。阶段 D 不自动采集材料、建工作台或递归调用其它 Skill，避免提前引入 Planning | D |
| 熔炉被占用，输出满 64，或别人改动炉子 | start 前读取 open_furnace，严格复用当前空炉约束；占用时报告炉况，找其它空炉或请求用户处理，不擅自取走他人物品。当前单批最多 8，take 要求整批输出相等，无法处理 64 输出栈。需要共享炉管理时，另行扩展有明确槽位和数量验证的 Node 工具后再接 Skill | E |
| 熔炼完成被误报为已取出 | start 只一次；复用后台 status/take 续接，只在确认真实产物和背包新增数量后 completed。超时、取消后已投入物品留在炉内，报告原坐标与已发生步骤，不自动再次 start；恢复需先查询现场 | E |
| 单批上限与长耗时冲突 | 第一版遵守 Tool 的真实限制，超出单批上限返回明确反馈；有界分批作为后续显式扩展，不能只是调大模型调用次数。Skill 后台执行使用独立总时限；阶段 F 的聊天只提交并返回执行 ID，后续进度/结果另行展示，不让整个熔炼卡住聊天 HTTP | E、F |
| 多任务抢占、断线后迟到回调重复执行 | 阶段 A 已共用执行槽、Node instance/generation/lease、回执和幂等 ID；有变化即停止。后续补充游戏停止/死亡/重生/换维度验收。旧任务清理必须仍带旧租约，不能影响新任务 | A 已实现基础，F 游戏验收 |
| 熔炼等待中被怪物接近 | 第一版不承诺自动撤退或同时做其它主动任务。可在后续安全层添加中断、撤离及恢复前重新核对炉况；等待期间释放资源并做其它任务需要明确资源锁/恢复协议，不能在本版串行执行器中直接并行调用移动或挖掘 | 后续安全层/Planning |
| 模型忽略 Schema 或重复调用 | Skill 目录与 Tool 目录分别管理。阶段 F 给 LLM 的 function 名加 skill_ 前缀，所有 Skill 名称/参数都走程序校验，资源 ID 仍由 Node 校验。优先复合 Skill，保留单工具入口；基础设施的 request_id/operation_id 防重不能替代自然语言重复消息的判断 | A 已实现校验基础，F 接入 |

## 测试与验收

在 `agent` 运行 Skill 专项测试：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s test -p test_skills.py -v
```

测试涵盖注册/目录隔离、参数校验、成功与假成功、前置条件失败、工具失败、未知工具和错误参数、重复提交/操作、回执恢复、调用预算、导航等待、超时、取消、断线/重启、并发拒绝、原任务互斥、HTTP 生命周期及真实 Node HTTP 注册表链路。测试专用 Skill 不在生产注册。

Node HTTP 集成使用真实 Node 进程、原工具注册和 HTTP 服务，但 Bot 是测试对象，没有连接 Minecraft；不会调用付费 LLM API。

阶段 F 必须在测试世界验证搜索/接近、挖掘后拾取、工作台合成、熔炼三块粗铁并取出铁锭，以及停止、断线、熔炉占用和原聊天/移动功能。阶段 A 的空目录检查现在改为确认 `GET /api/agent/skills` 包含 `explore_and_find`，未知 Skill 请求仍返回 UNKNOWN_SKILL；目录接口正常不代表业务技能已通过游戏验收。

### 阶段 A 实际自动化结果

- Python 全量：`python -X utf8 -m unittest discover -s test -p 'test_*.py'`，111 项通过，其中 Skill 专项 21 项。
- Node.js 全量：在 `minecraft-bot` 运行 `npm.cmd test`，152 项通过。
- Python `compileall -q app skills test` 和 `git diff --check` 通过。
- 本次运行环境为 Python 3.14.10、Node.js v24.21.0；项目规范指定的 Python 3.12 尚未在本次环境复测。
- 未执行真实 Minecraft 验收，也未调用付费 LLM API；阶段 A 通过不代表四个业务 Skill 或整个 Task 006.5 已完成。

## 阶段 B：explore_and_find

本阶段只实现这一个 Skill。`catalog.py` 集中注册；通过原有 Skill HTTP API 调用，不调用 LLM。游戏聊天仍走原来的 Tool Calling，本阶段直接说“找一棵树”不会保证进入这个 Skill。

技能已整理为独立目录 `agent/skills/explore_and_find/`。用途与命令见 [SKILL.md](../../agent/skills/explore_and_find/SKILL.md)，准确执行分支见 [SOP.md](../../agent/skills/explore_and_find/SOP.md)。元数据和参数由 `metadata.py` 统一维护并经注册表生成，执行逻辑位于 `skill.py`；原 `from skills.explore_and_find import ...` 保持兼容。辅助资源暂不添加，文档目前不会自动加载给 LLM。

目录整理后重新运行 Python 全量 130 项、Node.js 全量 153 项，均通过；迁移前后完整 Skill 目录定义的规范化 JSON SHA-256 相同，确认对外元数据及参数 Schema 一致。未进行真实 Minecraft 测试。

执行流程：读取初始快照 → resolve_resource 确认运行时名称 → find_block/find_entity 搜索 → 可选有限路点探索 → 可选接近 → 再次确认同一方块坐标/实体 ID → 独立检查搜索范围、实际距离及维度。不会调用挖掘、攻击、放置、背包修改工具。

| 参数 | 默认值及限制 |
| --- | --- |
| target_kind | 必填，block 或 entity；entity 表示实体类型，不是玩家用户名 |
| target | 必填，最多 64 字符；推荐真实名称，如 oak_log、cow、zombie，或注册表显示名 |
| search_radius | 默认 16，整数 1～64；以初始坐标为球心的三维目标范围 |
| explore | 默认 false；true 时仅在当前搜索/接近失败后尝试有限路点 |
| approach | 默认 false；true 时接近并验证最终距离，方块额外检查可见性 |
| approach_distance | 默认 3.5，3～4.5 格；使用 Bot 脚部到方块坐标/实体脚部的三维距离 |
| max_waypoints | 默认 4，1～8；未开启 explore 时无效 |
| max_results | 默认 4，1～8；限制展示结果，不表示整个范围只有这些目标 |

性能及行为边界：

- 每次 Node 搜索半径不超过 16 格，最多返回 16 个候选；Python 再按初始范围过滤和截断。仅搜索模式（explore=false 且 approach=false）不会移动。
- 探索采用最多 8 个水平路点、两圈四方向，不做整球穷举或洞穴挖掘。目标和导航终点必须在初始搜索范围内；实际寻路路线可能因绕障短暂超出该范围。累计请求导航的直线段预算为 256 格，不是实际行走路径长度的保证。
- 最多尝试 6 个不同目标；每个方块最多尝试 3 个候选站位；实体最多主动接近两次，不持续追踪。未知区块不能视为空气；阻挡、未加载或飞行目标可能无法接近。
- 导航使用原 goto_position 和 get_movement_task；不反复启动同一导航，单次等待最多 75 秒。仅明确失败可切换候选，超时、取消、断线或结果不确定立即停止。
- 输入名称仅由当前 Node registry 校验，没有中文别名表。中文可能因显示名为英文而无法匹配；TARGET_UNKNOWN/TARGET_AMBIGUOUS 会保留候选供澄清。这里没有提前引入翻译模型。
- Node resolve_resource 增加 kind=entity，读取 entitiesByName；旧 any 仍只查询物品/方块。没有增加游戏控制 API，也不承诺解决模组协议兼容问题。

### 如何观察真实执行

先在 Minecraft 测试世界开放 LAN，更新当前 MC_PORT，用项目原一键启动器启动 Node 和 Python。已运行的服务需要重启加载新代码，服务端口不用改变。

在 PowerShell 进入根目录（下面每个代码块是一条独立命令）：

```powershell
Set-Location 'D:\桌面\MineMate'
```

先只搜索附近橡木原木，不移动。将 Steve 换成实际游戏昵称；有 OWNER_USERNAME 时使用该主人昵称：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-explore-skill.ps1 -Player Steve -Target oak_log
```

搜索并接近原木：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-explore-skill.ps1 -Player Steve -Target oak_log -Approach
```

搜索并接近牛：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-explore-skill.ps1 -Player Steve -TargetKind entity -Target cow -Approach
```

允许在半径 40 格内尝试最多 4 个探索路点，再接近：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-explore-skill.ps1 -Player Steve -Target oak_log -SearchRadius 40 -Explore -Approach
```

脚本不修改系统执行策略，Bypass 只对该测试进程生效。自定义 Python 端口时增加 `-AgentUrl http://127.0.0.1:实际端口`。脚本每 0.5 秒轮询进度，仅在进度变化时打印，结束时输出完整 JSON；快速任务可能直接显示终态。

输出中的 `execution_id` 用于查询和取消。`data.stage` 表示 resolving/searching/exploring/moving/verifying 等阶段，`data.scans` 记录实际搜索位置与截断标记，`data.waypoints` 记录路点结果，`data.selected` 为最终目标，`steps` 是实际工具账本。失败也保留已有进度。`coverage_complete` 始终为 false，明确没有证明整个范围已搜索完毕。`results_truncated` 标记展示或底层候选数量被截断。

按 Ctrl+C 时脚本会尽力请求取消本次执行；取消确认失败可在游戏中发送「停止」。取消不会传送 Bot 回原位。也可在另一个终端按实际执行 ID 手动取消：

```powershell
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/agent/skills/executions/替换执行ID/cancel' -ContentType 'application/json' -Body '{"username":"Steve"}'
```

直接 HTTP 请求体示例（每次新操作使用新 request_id，重发相同 ID 只返回原记录）：

```json
{
  "player": "Steve",
  "request_id": "find-oak-001",
  "skill": "explore_and_find",
  "arguments": {"target_kind": "block", "target": "oak_log", "search_radius": 16, "approach": true},
  "timeout_seconds": 300,
  "max_tool_calls": 192
}
```

### 游戏内验收清单

1. 在 Bot 附近 8～12 格放原木，执行仅搜索：找到坐标，Bot 不移动，背包和世界不变。
2. 执行 -Approach：Bot 接近，结果 completed、verification.verified=true；确认只接近，没有砍树。
3. 附近放一头牛，测试 entity/cow，确认实体 ID 和三维距离；隔墙或垂直距离过大时不得误报到达。
4. 在初始 16 格外、40 格内准备原木，清除近处同类目标，开启探索观察路点。资源不在路点可见范围时允许明确失败，不能声称已搜遍全部区域。
5. 用墙阻挡站位或在途中移除目标，检查跳过候选/失败反馈，不无限重试、不挖墙。
6. 导航途中发「停止」、取消请求或断开 Minecraft，确认停止后续工具调用，重新连接后不会恢复旧任务。
7. 重复同一 request_id，确认不再次移动；新请求使用不同 ID。
8. 回归「你好」「状态」「跟随我」「停止」「前往 X Y Z」和原 Tool Calling。

本阶段没有可用的真实 Minecraft 会话，以上验收尚需用户执行。通过后再推进阶段 C gather_resource。

### 阶段 B 实际自动化结果

- Python 全量 130 项通过，含新增搜索 Skill 专项 19 项；阶段 A 的 21 项基础设施测试继续通过。
- Node.js 全量 153 项通过，新增实体类型解析测试，旧功能回归通过。
- 新增真实 Node HTTP 搜索→站位→导航→复查链路测试；世界是受控测试对象，没有连接 Minecraft。
- Python compileall 与 PowerShell 解析器语法检查通过。未请求付费 LLM API。
