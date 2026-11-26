# MineMate 项目开发规范

## 1. 项目介绍

MineMate 是一个基于大语言模型的 Minecraft Java Edition AI 玩伴。

玩家通过 Minecraft 游戏聊天框使用自然语言向 AI Bot 发送指令，Bot 理解指令后，可以自主规划并执行游戏任务。

项目目标不是开发普通聊天机器人，而是开发具备自然语言理解、工具调用、任务规划、记忆和反馈能力的智能体软件。

## 2. 技术架构

项目采用 Monorepo 结构。

- minecraft-bot：Node.js + Mineflayer，负责连接 Minecraft Java Edition、读取游戏状态和执行游戏操作。
- agent：Python + FastAPI，负责 LLM 调用、任务规划、Agent Loop 和记忆管理。
- data：SQLite 数据存储。
- prompts：管理系统提示词和任务提示词。

Python Agent 和 Node.js 游戏控制层通过 HTTP 通信。

第一版使用 Minecraft Java Edition 的 Open to LAN 功能，不要求搭建独立 Minecraft Dedicated Server。

## 3. 开发原则

1. 每次只实现当前明确要求的功能，不擅自扩展需求。
2. 优先采用简单、稳定、容易维护的实现方式。
3. 不要为了使用新技术而增加不必要的依赖。
4. 不要一次性生成整个项目的所有模块。
5. 新增功能前先检查已有代码，尽量复用现有模块。
6. 不允许在未经确认的情况下大规模重构。
7. 每个功能都必须具有可验证的完成标准。
8. 优先使用成熟的 Mineflayer API，不重复实现已有的游戏控制能力。

## 4. 编码规范

### JavaScript

- 使用 Node.js。
- 使用 CommonJS 模块规范。
- 文件名采用小写命名。
- 每个模块只负责一类功能。
- 对异步操作进行适当的异常处理。
- 不要吞掉错误，应输出有意义的日志。

### Python

- 使用 Python 3.12。
- 使用类型注解。
- 使用 Pydantic 定义数据结构。
- 使用 FastAPI 提供 HTTP 接口。
- Agent、LLM、工具和记忆模块保持职责分离。

## 5. Minecraft 控制规范

1. Mineflayer 是唯一的 Minecraft 游戏控制入口。
2. 所有游戏操作都封装成独立工具。
3. 工具必须定义输入参数、执行结果和异常返回。
4. 不允许让 LLM 直接控制游戏底层按键。
5. 移动、采集、放置等操作必须有执行状态反馈。
6. 不允许无限循环执行游戏动作。
7. 支持停止当前动作和取消任务。
8. 优先保证 Bot 不会无故破坏玩家建筑或丢弃物品。

## 6. Agent 设计规范

1. LLM 负责理解意图、规划任务和选择工具。
2. 传统程序负责参数校验、工具执行和状态管理。
3. Agent 必须根据工具执行结果决定后续行动。
4. 复杂任务必须拆分为可追踪的子任务。
5. 任务失败时应分析原因并决定是否重试。
6. 限制任务执行步骤和工具调用次数。
7. 玩家新指令应具有明确的任务优先级。

## 7. 配置与安全

- API Key 必须存储在环境变量中。
- 禁止将 .env 提交到 Git。
- 提供 .env.example。
- 不在日志中打印 API Key。
- 游戏工具只允许执行明确授权的操作。
- 第一版只面向本机及可信局域网环境。

## 8. AI 辅助开发规范

每次开发任务开始前：

1. 阅读 AGENTS.md。
2. 检查相关代码。
3. 说明准备修改哪些文件。
4. 说明实现方案。
5. 再开始编码。

完成任务后：

1. 列出修改文件。
2. 说明实现内容。
3. 执行相关测试。
4. 汇报测试结果。
5. 列出尚未解决的问题。
6. 更新必要的开发文档。

禁止声称执行了实际上没有执行的测试。

## 9. 统一游戏工具开发规范（Task 006）

- 游戏实现统一放在 `minecraft-bot/src/tools/`，按 movement、perception、mining、building、combat、inventory、interaction、crafting 分类。
- `tools/index.js` 组装工具，`tools/registry.js` 统一注册、校验、调度、去重、超时和取消。新工具必须注册后才能由 Agent 调用。
- 旧 `src/movement.js` 与 `src/perception/` 仅保留转发导出，保持旧接口兼容，禁止放置第二份游戏逻辑。
- Node 的注册定义是工具名称、描述和参数 Schema 的唯一来源；Python 通过 HTTP 获取，不另行维护游戏工具清单。
- 工具返回 `{ success, tool, data, error }`；`data.status` 区分 completed、pending、uncertain。未确认结果不得报告成功。
- 游戏修改工具互斥，停止优先取消；超时后等待底层调用结算再释放锁。每次异步步骤前后检查取消与连接状态。
- 功能方块使用独立适配器，窗口在结束、异常、取消时清理。持续操作必须有界，失败不得自动无限重试。
- 修改工具时运行全部旧测试及相关新增测试，并分别报告自动化结果与真实游戏验证状态。
- 丢弃工具不判断物品价值，不设物品白名单；按用户明确指定的名称和数量执行，允许钻石、附魔装备及背包最后一件物品。保留单次数量限制与结果验证；价值偏好留待后续记忆/学习层处理。
- 移动统一由 `tools/movement/index.js` 管理任务，`controls.js` 管理跑步/潜行持续模式和单次跳跃，`tools.js` 注册新移动工具；禁止另建方向控制或寻路循环。切换本控制器目标保留模式，停止/失败/超时/死亡/断线清理；正常到达保留模式直到关闭。
- 跑步只设置 pathfinder 跑步许可，潜行优先并在关闭后恢复原设置。同步模式工具可在导航等待期间执行，不取得或替换导航锁；跳跃及其它异步修改工具仍互斥。跳跃只发一个物理帧脉冲，观察起跳和落地；升级 Mineflayer/pathfinder 时须复查事件顺序及起跳队列清理，并重跑实际物理模拟集成测试与游戏验收。
- 熔炉子工具放在 `tools/crafting/furnace.js`，配方/燃料唯一支持表在 `crafting/data.js`，由运行时 registry 过滤；不得在 Python 复制燃料/配方清单。添加与补充统一为增量投料/加燃料，非空槽须同类，空燃料槽优先煤炭；不同原料/产品或空间不足时拒绝，不擅自清炉。使用官方 putInput/putFuel/takeOutput，取产品仅取当前整栈，不用 transfer 把剩余产品写回只读输出槽；加工中须预留完整栈容量。核验窗口背包与槽位增减，并考虑真实加工/燃烧；未知结果返回 uncertain，不重发副作用。保留原 smelt_item 接口的空炉/整批合同，炉子选择属于后续 Skill。

## 10. Planning 前置执行接口

- `agent/app/execution/` 负责显式工具序列的后台串行执行、受限结果引用、有界等待与执行账本；不在这里混入 LLM 计划生成。未来 Planner 应提交经校验的步骤，复用该执行入口。
- Node 注册表仍是工具 Schema 唯一来源。`inspect_recipe`、`inspect_smelting_recipe`、`find_interaction_positions` 为只读查询；候选站位不等于路径可达，导航到附近后必须重新检查实际距离、视线及脚下支撑。
- `pending` 导航通过 `get_movement_task(task_id)` 等待原任务，禁止反复启动相同导航。`smelt_item(start)` 的后台续接只启动一次，再有界 status/take；停止不会撤回已投入的原料/燃料。
- 后台任务使用 Node 的 `instanceId + generation + leaseId`。`tools/session.js` 管理整任务控制权与 30 秒心跳失效；停止、死亡、换维度和断线撤销。旧实例/旧租约的清理不得影响新任务。现有接口可不传租约，但修改操作不能插入正在持有租约的任务。
- Node 租约的正常 `release` 是同一个 Agent Runtime 任务中 Skill/后台子任务间的交接，不得递增 `generation`；否则随后合法的 Node 工具会被误判为 `STALE_TASK`。显式取消、租约过期、死亡/重生/断线仍须撤销任务并使旧 generation 失效。
- 同一请求 ID 的工具回执最多保留 10 分钟，最多 512 项；后台任务记录为内存中最多 64 项。结果不确定时查询原回执和最新状态，禁止盲目重发副作用；服务重启不自动恢复任务，也不提供跨重启的持久幂等保证。
- 前序结果引用只允许 `$ref: {step, path}` 指向已完成步骤的 JSON 字段/数组索引；替换后重新按 Node Schema 校验。禁止 eval、表达式或访问对象属性。
- 工具完成不等于最终目标完成。第一版后台验证仅支持显式背包数量/增量条件；没有验证条件时返回 `finished_unverified`，不得标成 `completed`。
- 本机 API 为可信调用方使用，租约是协调机制，不是身份认证。后台执行器运行单个 Python 服务进程，不启动多 worker；新增规划能力前先通过 `docs/tasks/task007-preparation.md` 的游戏验收。
- 资源语义解析必须使用 Node 工具 `resolve_resource`（`minecraft-bot/src/tools/perception/resources.js`），从运行时 registry 读取当前版本和模组物品/方块/实体类型；`kind=entity` 查询实体，`any` 保留仅物品和方块的原行为。不得在 Python 维护静态别名清单，也不得猜测未知模组名称。候选为空或有歧义时必须澄清，Skill 和 Planning 仍必须使用真实工具目录及结果验证。`agent/app/semantic.py` 只能提供不改写参数的兼容辅助函数。

## 11. Skill 开发规范（Task 006.5）

- Skill 统一放在 `agent/skills/`，与 Node Tool Registry 分开管理。每个 Skill 继承 `Skill`，声明名称、描述、严格 Pydantic 参数模型、前置条件、完成条件及依赖工具；不在 Python 复制 Node 工具 Schema。
- 每个业务 Skill 使用独立目录 `agent/skills/<name>/`，包含 `SKILL.md`（用途、参数、示例、边界）、`metadata.py`（运行时元数据及参数模型）、`SOP.md`（实际工具顺序、分支、失败处理与验收）、`skill.py`（执行/核验）及 `__init__.py`（公开导出）。辅助资源按需添加，当前搜索技能暂不添加。公共执行基础设施仍放在 skills 根目录。
- 元数据与参数 Schema 由 metadata.py 经 Skill Registry 生成，不另行维护重复的 JSON/YAML 工具定义。SKILL.md 与 SOP.md 当前作为说明文件，不自动加载为 LLM 提示或可执行程序。文档必须与实际实现同步，逐个 Skill 提供“测试命令 → 工具调用顺序 → 成功验证方式”。
- SKILL.md 的 YAML `name` 仅使用小写字母、数字和连字符，例如 `explore-and-find`；Python 包名及现有 API 注册名继续使用下划线，例如 `explore_and_find`，在说明中明确对应关系。
- 通过 `SkillRuntime.call()` 调用工具，每次逻辑操作提供唯一 `operation_id`，根据最新 Node 目录再次验证。重复同 ID 只复用结果，不重复副作用；参数变化则拒绝。同一 Skill 的工具串行执行，轮询必须有调用次数和时间上限。
- Skill 与显式后台工具序列共用 `TaskManager` 的执行槽、租约、心跳、账本、回执与取消；禁止另建争用 Bot 的独立任务队列。Skill 不得创建脱离执行器生命周期的后台任务，不得绕过 ToolClient 操作 Minecraft。
- `execute()` 返回的数据不是成功凭证。`verify()` 必须对实际执行前后快照检查所有 `success_criteria`，提供结构化证据；失败或不确定结果不得标为 completed。Skill 内部使用确定性程序，不逐个工具调用 LLM。
- 同一玩家的 `request_id` 在后台工具任务和 Skill 中共享幂等范围，最多保留 64 个内存记录。停止、死亡、换维度、断线和 Node 重启后，不得续跑失效租约；不提供跨重启自动恢复。
- 生产 Skill 统一由 `agent/skills/catalog.py` 注册。已实现 A 基础设施、B `explore_and_find`、C `gather_resource`、D `craft_item` 和 E `smelt_item`，后续按 F LLM 集成与真实游戏验收逐步推进；具体合同、风险与验收见 `docs/tasks/task0065-skills.md`。Mock/回环 HTTP 测试不等同于 Minecraft 验收。
- 搜索使用三维距离、有限已加载范围、有限路点和候选站位；只读搜索不得移动。接近复用原导航，并重新确认同一坐标/实体 ID 和最终距离。不得把“未发现”表述为整个世界没有资源。进度通过 `report_progress()` 写入有界执行记录；失败仍保留搜索范围、路点与真实工具账本。
- `gather_resource.count` 是背包目标总数（1～256），先扣除已有数量。采集来源通过 Node 只读工具 `inspect_mining_sources` 查询运行时 drops，不得在 Python 硬编码掉落表；来源只是可能结果，附魔/数据包仍须以真实背包验证。挖掘与拾取分别核验；脚下支撑/已尝试坐标跳过，无实体只能有界短查，未知结果不得继续挖掘。当前每轮搜索半径最多 16 格，采集后按 Bot 当前坐标更新范围，不自动合成工具或全局探索。
- `gather_resource` 清障必须走 Node `clear_path`，不得打开 Pathfinder 全局 `canDig`。只沿 Bot 到明确目标的有限射线清除允许的天然方块，单次最多 8 块；不挖液体、脚下支撑、矿石、功能方块和未知方块。每次清障后重新导航/检查目标。挖后拾取先核对背包，再读取 `nearby_entities` 中真实掉落实体位置/ID，必要时有限清障后拾取；最终以背包净增量确认。
- `mine_vein` 负责独立矿脉采集，目录为 `agent/skills/mine_vein/`，通过同一 SkillRegistry/TaskManager 执行。复用 `_Gather` 的解析、快照/库存读取，不嵌套提交另一 Skill 或另建队列。种子必须可见或由玩家明确指定且已暴露；`inspect_vein` 只检查种子和六邻接新暴露矿石，单脉半径最多8格，不能透视遍历隐藏整脉或跳向不连通矿脉。
- 矿脉通道工具集中在 `tools/mining/vein.js`：`inspect_mining_route` 用已加载方块做最多512节点的局部加权站位搜索，输出下一步；`mine_route_block` 重新规划后单步挖障碍，`mine_vein_block` 复用基础 digBlock 并额外检查矿石风险/实际可见面。只在有可靠支撑的一人宽两格高通道/现有台阶推进，不搭块、不挖空包围盒、不改变全局 canDig；拒绝液体、坠落物、未知地形/区块、设施及脚下支撑。通道与拾取共用最多32块清障预算，最多128轮诊断，终态核验库存、同维度和矿脉连通证据。天然石头与人工石头无法仅凭材质区分，不得声称已识别所有玩家建筑。
- 自然语言已注册矿脉 Skill 时，矿物目标优先 `execute_skill(mine_vein)`，木材/沙子仍走 gather_resource；模型视图隐藏三个专用通道执行入口，由 Skill 管理统一预算。Skill Schema 必须完整提供，不得截断长描述而丢失后注册 Skill 的参数。正常脚本/HTTP 单工具接口继续保留。
- `craft_item.count` 是本次期望新增产物数（1～16），不能扣除已有产物；整批配方报告实际增量。先用 Node `inspect_recipe` 查询真实材料缺口及 `requiresTable`，缺材料不导航/合成，需要工作台才搜索接近并复查。合成工具一次提交，未知结果不重发；最终独立验证实际背包净增量和维度。不在 Python 维护配方表，不自动采集、放工作台或递归调用其它 Skill。
- 交互站位导航须显式考虑到达容差：`goto_position.arrival_range` 可选 0～2、默认 2；合成 Skill 用 0 进入目标脚部方块，再复查实际距离和视线。不得把 GoalNear 默认两格的“已到达”当成一定可以交互；不能为修复 Skill 改掉原有聊天/HTTP 导航的默认行为。工作台失败保留 workbench_check 诊断，并使用真实 A* 或包含到达误差的模拟测试。
- 熔炼 Skill `smelt_item` 当前仅投放原料和燃料，复用 inspect_smelting_recipe/open_furnace/furnace_add_input/furnace_add_fuel；禁止调用 furnace_take_output 或旧 smelt_item(start)，不等待产品、不轮询、不自动续补，不实现收集 Skill。每次新增 1～64，逐炉投入 min(剩余量, 64-已有原料)，最多 8 炉、每炉提交一次；煤炭优先、同类槽补充、真实距离/视线和修改前复查保持。use_existing 仅允许同工艺续接，不取已有产品，输出满槽跳过；容量不足保留已投/剩余量并失败。最终独立核验原料/燃料背包变化、炉内原料/产品按 Node 配方折算守恒和同维度；completed 只表示投料完成，completion_scope=furnace_loading、smelting_completed=null。弱燃料一次最多 64，剩余需求及未知燃烧容量明确标记需后续处理，不承诺整批完成，也不建立长期燃料预留。结束即释放控制权，不创建后台监视器；未知结果不重发，取消不回滚。旧 wait_timeout 仅兼容且忽略，总任务超时/调用预算继续生效。
- `gather_resource.count` 是背包目标总数，先扣除已有数量。采集来源通过 Node 只读工具 `inspect_mining_sources` 查询运行时 drops，不得在 Python 硬编码掉落表；来源只是可能结果，附魔/数据包仍须以真实背包验证。挖掘与拾取分别核验；脚下支撑/已尝试坐标跳过，无实体只能有界短查，未知结果不得继续挖掘。当前初始搜索半径最多 16 格，不自动合成工具或全局探索。

## 12. 记忆与上下文（Memory）

- 会话事件日志是唯一真源：`turn/*`、`step/*`、`user/message`、`assistant/message`、`tool/call`、`tool/result` 与 `compaction/*` 仅追加；LLM 消息历史必须由 `Session.derive_messages()` 派生，禁止再维护第二份对话历史。
- 每个玩家一个会话，按玩家串行化 turn；同一玩家不得同时存在两个进行中的 turn。`MessageHandler` 的 `PLAYER_BUSY` 与 `MemoryManager` 的锁共同保证这一点。
- 系统提示、压缩摘要和最近历史进入模型可见 surface；当前世界快照与本地时间作为**不持久化**的临时上下文追加，不写入事件日志。
- 压缩只选择当前 turn 之前、已结束且未被最新摘要遮蔽的完整 turn；不得拆开 `assistant/message` 与其 `tool/result`。原始事件不删除，由 `compaction/summary.shadowed_seqs` 将其移出 surface。
- 持久化使用项目根目录 `data/minemate-memory.sqlite3`（`MEMORY_DB_PATH` 可覆盖）；事件写入必须校验 seq 连续并在事务中提交。数据库损坏、磁盘失败或压缩失败只能降级记忆，不得阻断游戏聊天。
- 加载会话时必须修复残留的 step/turn/compaction 标记；不完整的工具调用块不得发送给模型。修改事件表结构需提升 `MEMORY_FORMAT_VERSION` 并提供迁移或明确拒绝旧库。
- 新增 context 贡献、摘要器或存储后端时，必须保持 `EventStore`、`Summarizer` 接口可替换，并补充 `agent/test/test_memory.py` 或对应测试；Mock/回环测试不等同于 Minecraft 验收。
- 记忆当前只在 `TOOL_CALLING_ENABLED=true` 的 Tool Calling 路径生效；旧意图模式保持原行为，若要接入必须先设计其上下文格式与回滚策略。

## 13. 任务执行运行时（TaskRuntime）

- 自然语言入口采用 Skill 优先：Planner 与 Executor 使用相同的动态工具视图，`execute_skill` 排在首位；已注册对应 Skill 时隐藏原生 `craft_item`/`find_block`，原生 `smelt_item` 仅暴露 status/take。Runtime 拦截旧记忆输出的这些替代入口，回填 `SKILL_REQUIRED` 而不执行；不把旧参数自动转换成 Skill 参数。按资源数量采集、类型搜索和整体熔炼由提示词/工具描述优先路由 Skill；精确坐标挖掘、战斗索敌、放置、独立熔炉补料/取出继续可用。视图不得修改完整 Node 目录或影响 Skill 内部调用；未注册 Skill 不隐藏工具，legacy/固定聊天/HTTP 接口保持兼容。修改该策略需覆盖真实 Node 目录契约、direct/plan、旧调用拦截和前置工作台放置测试。
- Skill 终态交给 Runtime 前必须等待 `TaskManager` 的 `_run()` finally 完成租约释放；`ExecutionContext.finish()` 先写 `completed`、后释放 Node 控制权，禁止下一步在此窗口内直接发送修改操作，否则可能收到 `BUSY`。取消/超时仍按原流程清理并保留不确定结果。

- 自然语言入口默认使用 `AgentRuntime`（`AGENT_EXECUTION_MODE=planned`）；旧 `ToolLoop` 仅保留为 `legacy` 兼容路径，不得在新功能中继续扩散旧“失败即结束”语义。
- 简单目标可直接执行；复杂目标必须先由 Planner 生成结果导向步骤。Planner 只输出 strict JSON（direct/plan），不执行工具、不写坐标或固定工具序列。
- 工具失败、`pending`、`uncertain` 必须作为 `role=tool` observation 回填，由模型决定重试、换工具、`update_plan` 或结束；不得把这些结果直接当成任务终态。
- 任务是否结束由模型调用 `finish_task` 决定；普通文本不自动结束。Harness 只在步数、工具调用、总时限、连续错误、重复失败、只输出文字等上限触发时兜底结束，并返回诚实的不完整结果。
- 同一工具+参数+错误不得无限重试；超过 `TASK_MAX_SAME_ERROR` 回填 `REPEATED_FAILURE`。所有兜底必须取消在途 Node 动作（如有）并关闭记忆 turn。
- `update_plan` 只替换计划上下文，不直接执行工具；计划最多 8 步，修改次数受 `TASK_MAX_REPLANS` 限制。计划是单轮运行时上下文，当前不单独持久化为 memory 事件。
- `finish_task`/`update_plan` 是 Python 控制工具，不进入 Node 注册表；Node 仍是游戏工具 Schema 唯一来源。Skill 目录和 `TaskManager` 不受该运行时改造影响。
- Node 的 `AGENT_TIMEOUT_MS` 必须大于等于 Python `TASK_TIMEOUT`；修改超时需同时重启两端。新增控制工具/兜底策略必须补 `agent/test/test_runtime.py` 或对应测试。
- 运行中插话通过 `POST /api/agent/steer` 进入当前 turn 的 next-step 队列，在工具批次结算后作为 `user/message` 插入；不得在工具执行中途插入，也不得由 Harness 解析“停止/换目标”。模型看到插话后自行选择 `stop_movement`/`update_plan`/`finish_task`；固定 `停止` 仍是优先 abort 的旧指令路径。插话写入当前 turn 记忆，但队列是单进程内存，不提供跨重启持久保证。
- 工具批次中不得在 assistant `tool_calls` 与对应 `tool` 结果之间插入 user/system 提示；错误/导航提示必须等整批结果写回后追加。发送模型前必须保证每个 tool_call 都有 tool result；缺失时补 `MISSING_TOOL_RESULT` 并记录 warning，避免 OpenAI 兼容接口因协议不完整返回 400。
- 同一工具累计失败/`uncertain` 3 次必须提示模型核对真实状态，累计 5 次必须兜底结束；不能因为中间穿插了成功的只读查询就无限重试同一拾取/导航动作。
- 拾取工具必须优先使用 `nearby_entities` 暴露的真实 `itemName`/`itemCount`；指定 `entity_id` 但名称不符返回 `ITEM_MISMATCH`。工具本身不得用“无限等待”替代可操作失败。
- 自然语言 Runtime 通过 Python 控制工具 `execute_skill` 动态读取 `SkillRegistry`，再复用现有 `SkillManager`/`TaskManager` 执行已注册 Skill；Skill 的参数、底层 Node 工具和独立核验仍是唯一权威来源。模型可以选择 Skill，但不得把 Skill 当作 Node 工具或绕过注册表；Skill 执行结果（含 verification、steps 和真实错误）必须回填后才能继续或 `finish_task`。Runtime 取消、超时和客户端断开时必须取消在途 Skill，释放共享租约。
