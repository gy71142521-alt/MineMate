# Task 006 实施记录

本记录区分自动化验证与真实模型、Minecraft 验收。任务原文见 [task006.md](task006.md)。

## 阶段 A：目录、注册与调用循环

- 移动和感知实现迁到 `minecraft-bot/src/tools/movement/`、`tools/perception/`；旧路径只转发同一个模块，保留导出与事件行为。
- `tools/registry.js` 提供白名单、Schema 校验、主人权限、互斥、请求 ID 去重、超时与取消。底层调用未结算时保留锁。
- Node 提供 `GET /api/tools`、`POST /api/tools/execute`、`POST /api/tools/cancel`。Python 以注册目录为唯一工具定义来源。
- Python `tool_client.py` 负责目录与结果验证，`tool_loop.py` 处理真正的 function `tool_calls`，通过 `role=tool`、`tool_call_id` 回传真实结果。
- 工具循环有总超时、最多调用次数、串行调用、失败不重试。pending/uncertain/失败结果不能被最终回复改写为成功。
- `TOOL_CALLING_ENABLED` 默认 false，保留 Task 005 模式；启用后复用同一 `/api/agent/message` 和原有并发/频率限制。
- 已核对当前安装的 Mineflayer 4.39.0、pathfinder 2.4.5 的类型、官方 API 文档及本地插件源码。OpenAI 格式参考：https://platform.openai.com/docs/guides/function-calling 。当前供应商真实模型的工具能力仍需单独验证。

迁移及注册首次检查：Node 99/99 通过；Python 调用循环首次检查：36/36 通过。后续阶段继续扩大测试，最终结果另行记录。上述均未执行付费模型请求或真实游戏操作。

## 阶段 B：挖掘、拾取和放置

- `mining/index.js` 实现有限查找与明确目标的单方块挖掘，校验工具、耐久、视线、距离和实际方块变化。
- `inventory/collect.js` 独立实现拾取，复用保守 pathfinder 配置，10 秒内验证真实背包增量；实体消失本身不能证明拾取。
- `building/index.js` 提供只读放置检查及单方块放置，校验支撑面、空位置、距离、实体碰撞和物品。
- 未改变 Task 002 的 `canDig=false` 与禁止自动放置配置；寻路不会为完成导航使用挖掘/建造工具。
- 分阶段执行对应测试后继续开发；最终完整回归结果见下文。

## 阶段 C：战斗与物品

- 限半径查找实体，全部距离使用三维欧几里得距离，排除 Bot 和无效实体。
- 仅攻击白名单敌对生物，不攻击玩家或中立生物，最多 3 次、不追击，通过受伤/死亡事件分别标记结果。
- 装备、食用、少量丢弃、道具使用分别选择对应 API，核对实际槽位和数量。举盾与剪刀不凭 API 返回虚报效果。
- 丢弃只支持普通低价值白名单，单次最多 16 个，拒绝清空整个背包。

## 阶段 D：功能方块

- 工作台、普通熔炉、容器各自提供独立适配器；普通门/按钮/拉杆交互与窗口、道具使用分开。
- 打开窗口仅返回可序列化快照，不向模型暴露窗口对象或持久句柄；正常、错误、取消和迟到打开时均清理。
- 合成检查实际版本配方、材料与输出批次数，验证产物数量。
- 熔炉 start/status/take 分阶段处理，投入返回 pending；查询实际槽位和进度，取出后验证数量。拒绝覆盖已有物品的熔炉。
- 箱子存取核对背包和容器双方变化。已核实 Mineflayer 在功能窗口关闭时同步背包的行为：窗口打开期间使用其背包区域校验，避免读取旧 `bot.inventory` 数量。

## 阶段 E：整合与取消边界

- 游戏修改操作互斥，最多 4 个工具请求在途。优先停止能够中断工具，旧本地移动入口也受互斥保护。
- 请求 ID 去重，旧消息携带接收时的任务代数；停止/生命周期变化后旧请求不能重新执行。带 requestId 的取消仅针对对应动作，防止迟到取消误停新任务。
- Node 支持取消到 Python HTTP 请求，Python 监听断开并清理正在执行或 pending 的动作。超时/异常最终回复也尝试取消对应动作。
- 底层 Promise 尚未结算时保留互斥锁，避免超时后新动作与旧动作并发；已生效的游戏改变不会回滚。
- 保留原聊天命令、Task 004 HTTP 接口和 Task 005 关闭工具时的只识别模式，无持久任务规划、记忆或复杂框架。

## 本次新增和修改文件

以下仅列 Task 006 的工作；仓库已有 Task 005 的未提交文件不等同于此次全部新增。

| 文件 | 职责或修改 |
| --- | --- |
| `minecraft-bot/src/tools/index.js` | 统一注册组装入口，首次交付 26 个工具，补充 inspect_block 后为 27 个 |
| `minecraft-bot/src/tools/registry.js` | 白名单、权限、互斥、超时、取消、请求去重、任务代数 |
| `minecraft-bot/src/tools/schema.js` | 双端可验证的有限 JSON Schema 定义 |
| `minecraft-bot/src/tools/common.js` | 距离/方块/背包/耐久检查、结果与错误 |
| `minecraft-bot/src/tools/movement/index.js` | 从 `src/movement.js` 迁入的 Task 002 原实现，调整配置导入路径 |
| `minecraft-bot/src/tools/perception/{index,playerState,inventory,nearbyEntities,nearbyBlocks}.js` | 从 `src/perception/` 迁入的 Task 003 原实现，调整配置导入路径 |
| `minecraft-bot/src/movement.js`、`src/perception/{index,playerState,inventory,nearbyEntities,nearbyBlocks}.js` | 旧路径仅转发，保留模块导出兼容性 |
| `minecraft-bot/src/tools/mining/index.js` | `find_block`、`mine_block` |
| `minecraft-bot/src/tools/building/index.js` | 放置条件检查及 `place_block` |
| `minecraft-bot/src/tools/combat/{helpers,index}.js` | 实体查找与有限攻击 |
| `minecraft-bot/src/tools/inventory/{index,collect}.js` | 装备、使用、丢弃、拾取 |
| `minecraft-bot/src/tools/interaction/{index,window,transfer}.js` | 窗口生命周期、普通交互、容器存取 |
| `minecraft-bot/src/tools/interaction/adapters/{workbench,furnace,container}.js` | 三类功能方块独立适配器 |
| `minecraft-bot/src/tools/crafting/index.js` | 配方合成和熔炉 start/status/take |
| `minecraft-bot/src/bot.js` | 在同一 Bot 上组装工具并保护旧移动入口 |
| `minecraft-bot/src/httpServer.js` | 目录、执行与取消接口，保留旧接口 |
| `minecraft-bot/src/agentclient.js`、`src/events/chat.js` | 取消在途请求，停止后丢弃旧消息，携带任务代数 |
| `minecraft-bot/package.json`、`package-lock.json` | 将已有传递依赖 `vec3@0.1.10` 显式声明为直接依赖 |
| `minecraft-bot/.env.example` | 工具循环的 Node 超时配置说明 |
| `agent/app/{tool_client,tool_loop}.py` | 动态 Schema、双端校验、真实 Tool Calling 循环与取消 |
| `agent/app/check_tool_calling.py` | 一次无游戏副作用的手动云端协议探测 |
| `agent/app/{config,intent,llm_client,message_handler,main}.py` | 开关与限额、任务代数、协议、消息路由和断开清理 |
| `agent/.env.example` | Tool Calling 开关、次数及超时配置 |
| `minecraft-bot/test/{tools,toolactions,toolsHttp}.test.js` | 注册、各类游戏工具、HTTP 与兼容测试 |
| `minecraft-bot/test/{agentclient,agentchat}.test.js` | 追加取消与旧消息停止边界测试，保留原断言 |
| `agent/test/{test_tool_loop,test_tool_probe}.py`、`test_config.py` | 调用循环、目录契约、错误取消、协议探测及配置测试 |
| `AGENTS.md`、`minecraft-bot/README.md`、本文 | 新工具开发规范、操作说明和交付记录 |

未修改真实 `.env`、Microsoft 认证缓存或 Python 依赖。未引入 Python 直接游戏控制、LLM 任意代码执行或长期任务规划。

## 最终自动化验证

实际执行于 2026-11-12：

| 检查 | 实际结果 |
| --- | --- |
| Node `npm.cmd test` | **118/118 通过**；失败 0、跳过 0 |
| Python `.\.venv\Scripts\python.exe -m unittest discover -s test` | 首次交付 **44/44 通过**；响应校验修正后 **50/50 通过**；调用格式兼容修正后 **54/54 通过**；无跳过 |
| Node `node --check`，遍历 src 与 test | **47 个 JavaScript 文件语法通过** |
| Python `-m compileall -q app test` | 通过 |
| Python `-m pip check` | No broken requirements found |
| Node `npm.cmd ls --depth=0` | mineflayer 4.39.0、pathfinder 2.4.5、dotenv 18.0.10、vec3 0.1.10，依赖有效 |

迁移前基线为 Node 94、Python 28 个测试，最终完整套件包含这些原有测试。测试模拟游戏与模型；另外实际执行本地回环 HTTP、真实 Node 子进程目录导出和现有启动错误测试，没有把 Mock 当作真实 Minecraft。

一致性验证：Python 测试启动 Node 子进程，从实际 `createToolSystem().list()` 获取目录，再由 `ToolClient` 解析，与原目录逐项比较名称、描述和参数 Schema；检查包含新旧工具且无重名。Node HTTP 测试另验证 `/api/tools` 和注册表相同。旧路径导出与迁移后实现为同一模块，原聊天/移动/感知/HTTP 测试全部通过。

本机实际 Python 为 **3.14.10**，上述结果基于该虚拟环境；项目规范建议 3.12，未声称已在 3.12 重跑。Node 为 24.21.0。

## 真实云端 API 验证

实际执行一次 `python -m app.check_tool_calling`，使用现有本地配置，只请求无副作用的 `protocol_probe` function，未创建游戏客户端、未执行游戏工具。

结果：**退出码 1，`LLM_TOOL_CALLING_REJECTED`**。程序把供应商的 400/422 参数拒绝归入此类；未输出密钥或原始供应商错误。仅凭该结果无法判定具体哪一参数被拒绝，也不能断言模型彻底不支持 Tool Calling。

因此真实模型的协议兼容性尚未通过，完整 LLM→游戏→LLM 实机链路仍待供应商兼容问题解决。没有自动重试或重复付费探测，未承诺当前供应商可以执行工具。

## 启动、接口和游戏验收

完整启动命令（逐条）、环境变量、当前 27 个工具及参数、HTTP 请求/响应示例、游戏内 12 项验收清单见 [README 的 Task 006 小节](../../minecraft-bot/README.md#task-006统一游戏工具与-tool-calling)。

- Python：在 `agent` 中执行 `.\.venv\Scripts\python.exe -m app.main`。
- Node：在 `minecraft-bot` 中执行 `npm.cmd start`，并配置当前 LAN 端口和正常认证。
- 启用新循环：Python `TOOL_CALLING_ENABLED=true`；Node `AGENT_TIMEOUT_MS=120000`，修改后重启两端。
- Node `GET /api/tools`、`POST /api/tools/execute`、`POST /api/tools/cancel` 复用原 HTTP 服务；旧接口保留。
- 外层 HTTP success 表示请求被处理，工具自身是否成功必须继续检查 `data.success`；pending 和 uncertain 都不是任务完成。

**真实 Minecraft 游戏内测试尚未执行。不能据自动化通过认定 Task 006 实机验收完成。** 需要用户依次验证旧聊天/移动/感知、明确目标的挖掘/拾取/放置、有限攻击、物品交互、工作台/熔炉/容器、取消与断线清理，以及真实模型能根据实际结果回答。

## 已知问题与 Task 007 建议

1. 当前供应商的工具协议请求被拒绝，需要核实其 Chat Completions function tools 参数支持或选择明确兼容的接口/模型；不会绕过参数验证执行普通文本。
2. 未执行 Minecraft 实测，不同游戏版本、模组、自定义配方、时延和其他玩家同时修改环境仍需验证；结果不明时返回 uncertain/pending。
3. 基础熔炼、道具使用和敌对实体采用保守白名单；丢弃物品白名单已按用户后续要求移除，见本文最后的变更记录。酿造、附魔、铁砧、砂轮、高炉、烟熏炉等未实现，预留独立适配器扩展。
4. 跟随/导航异步启动不表示到达；熔炉加工在世界中继续，停止不能撤销已经投入、消耗或生效的操作。底层 API 挂起时会保留锁，可能需要重新连接。
5. HTTP 面向本机和可信 LAN，无互联网身份认证；requestId 去重是内存中有限保留，不是跨进程持久化保证。
6. 下一阶段先完成真实工具验收，再引入任务 ID、子任务状态和有界重试；失败/取消后先重新读取状态，再决定下一步，避免重复破坏或存取。

## 后续修正：模型响应校验诊断

用户报告自然语言挖掘/跟随返回 `LLM_INVALID_RESPONSE`、`INVALID_TOOL_CALL` 后，检查确认本地配置已启用工具模式。旧错误码无法区分具体格式问题，也没有保留原响应，因此不能断言这两次请求的唯一根因。

本次修复了可由 Mock 重现的展示边界：合法自然语言回复带换行或超过 500 字符，会被旧的单行模型拒绝，进一步取消已经 pending 的动作。现在普通换行/空白会规范为单行，展示最多 500 字符；空内容、超大内容、代码围栏和非法控制字符仍拒绝，工具参数的严格校验不变。

增加 `TOOL_LLM_MAX_TOKENS`（默认 2048，上限 8192），工具模式及手动探测共用此预算；Task 005 请求仍为 512，不加入自动重试。`finish_reason=length` 单独返回 `LLM_OUTPUT_TRUNCATED`，不会解析执行半截调用。

增加静态诊断分类，区分响应结构、缺完成状态、工具列表/格式、未知/重复/并行工具、参数 JSON/Schema 与最终回复问题。日志不输出模型原文、未经验证的工具名、参数或密钥。提示词强调参数严格遵循目录，不额外附加身份字段。

修改：`agent/app/{config,llm_client,tool_loop,check_tool_calling}.py`、`agent/test/{test_config,test_tool_loop}.py`、`agent/.env.example`、README 及本文。真实 `.env` 未修改。

实际再次执行全部测试：**Node 118/118、Python 50/50 通过**，Python 编译检查通过。新增覆盖多行/长回复不取消 pending 动作、非法回复仍取消、输出预算与截断、日志不回显敏感输入、参数与调用格式继续拒绝。没有执行新的付费 API 请求或 Minecraft 实测；需重启 Python 后使用新诊断进一步确认当前供应商的具体返回问题。

## 后续修正：Tool Call 结构兼容

用户再次反馈“请跟着我走”触发 `reason=TOOL_CALL_FORMAT`。该阶段发生在工具白名单和参数 Schema 校验之前，不足以判断具体哪个字段出错。

为检查结构，实际新执行一次无游戏操作的强制 `protocol_probe` 请求，结果仍为 `LLM_TOOL_CALLING_REJECTED`，未取得有效调用结构；没有重试或执行游戏。因此不能宣称已确认用户这次响应的具体字段值。

代码审查确认旧模型人为限制了调用 ID 字符，且拒绝网关可能附带的 `index` 和已解码的对象参数。现已做受限兼容：

- 调用 ID 为 1–256 字符可见 ASCII，允许冒号、点号等关联标识字符，拒绝控制字符及空白；它只作为 JSON 中的结果关联 ID。
- 单调用允许整数 `index: 0`，规范化回传时排除该字段；非零和布尔值拒绝。
- 对象参数用 `json.dumps(allow_nan=False)` 编码，再按原流程解析和 Schema 校验；不会删除多余字段、转换数字类型或放行未注册工具。
- 其他未知字段仍拒绝。新增日志 `fields=ID:INVALID`、`FUNCTION_ARGUMENTS:TYPE` 等，只显示程序预定义字段与错误分类，不输出输入值或未知字段名。

修改 `agent/app/tool_loop.py`、`agent/test/{test_tool_loop,test_tool_probe}.py`、README 和本文，手动探测与正常消息共用同一解析模型。真实配置、Node 游戏逻辑和工具权限未改动。

实际完整回归：**Node 118/118、Python 54/54 通过**，Python 编译检查通过。新增测试验证兼容格式的执行/结果回传、ID 原样关联、index 不回传、非法字段诊断不泄密、参数与白名单限制不变。真实跟随仍需重启 Python 后在游戏中验证；若未解决，新日志会标出被拒绝的具体字段类别。

## 2026-11-16：食用、丢弃、导航后交互及挖掘工具选择修正

用户游戏日志提供了以下证据；本次未把这些日志当作修正后的实机验证：

| 现象 | 已确认原因 | 修改 |
| --- | --- | --- |
| 附魔金苹果返回 CANNOT_CONSUME | 业务层将所有 food 都限定为饥饿值小于 20，漏掉原版例外 | 允许满饥饿食用 golden_apple / enchanted_golden_apple；与已安装 Mineflayer inventory.js 的消费例外核对，普通食物仍检查饥饿 |
| 攻击请求只导航 | goto_position 启动即返回 pending，Python 对 pending 终止本轮，未执行攻击 | 工具导航默认最多等待 15 秒，按当前 taskId 的真实 arrived 事件返回 completed 后，原有工具循环可继续；未到达仍 pending |
| 挖掘却调用 inspect_placement 并报 OCCUPIED | 模型选了放置工具；已有方块在放置检查中确实应被拒绝，且缺少任意单点方块查询 | 添加只读 inspect_block；说明放置检查与挖掘的差异；mine_block 加可选 tool_name，按玩家指定工具检查、装备和挖掘 |
| 丢弃橡木木板返回 PROTECTED_ITEM | 木板未包含在原低价值白名单 | 加入原版各种木板，保持 1–16 数量、重要物品保护和禁止清空整个背包 |

导航等待封装在 `tools/movement/navigation.js`，复用原 `movement/index.js` 与真实 result 事件，不重写 Task 002、不创建第二个目标。`goto_position.wait_seconds` 为 0–15，默认 15；0 为即时返回模式。等待期间仍可最高优先级停止，取消只针对当前 taskId，路径失败、断线或状态读取异常会结算等待并清理监听/定时器。注册表绑定原控制器的事件方法，避免包装对象产生另一套事件状态。旧聊天“前往”和 Task 004 `/api/movement/goto` 保留原来的即时返回行为。

Agent 提示词明确：攻击必须使用真实实体 ID，attack_entity 的 weapon 可直接装备；导航到达不是攻击完成；读取待挖方块使用 inspect_block 而非 inspect_placement。工具失败的最终回复按程序定义的错误分类提供具体原因，不回显上游原文，不把失败改写成成功。这仍是真实 Tool Calling，没有添加字符串匹配执行路由或长期规划。

修改文件：

- Node：`src/tools/{index,registry}.js`、`src/tools/inventory/index.js`、`src/tools/mining/index.js`、`src/tools/building/index.js`；新增 `src/tools/movement/navigation.js`。
- Python：`agent/app/tool_loop.py`。
- 测试：`minecraft-bot/test/{toolactions,movement}.test.js`、`agent/test/test_tool_loop.py`。
- 文档：README 与本文；真实配置、认证缓存和依赖未修改。

实际最终检查：**Node 126/126、Python 57/57 通过**，48 个 JavaScript 文件语法检查、Python 编译检查和 `git diff --check` 通过。没有跳过原有测试或改变原有测试断言；补充测试覆盖：

- 满饥饿食用两类金苹果、普通食物仍拒绝、实际数量未变时 uncertain。
- 丢弃 4 个橡木木板、实际数量变化，以及超量/钻石/清空背包仍拒绝。
- 单点查询只读，钻石斧装备、缺失/不合适工具拒绝，原挖掘保护仍保留。
- 导航等待真实到达、忽略旧任务 ID、有界 pending、取消/路径失败/断线/读取异常清理。
- 工具注册表与原 Task 002 控制器的到达事件整合、监听恢复与 canDig=false。
- Mock 模型在导航 completed 后同轮继续 attack_entity，在 inspect_block 后传 tool_name 挖掘，以及失败回复不泄露原始错误。

未进行新的付费模型请求或 Minecraft 实测。Mock 能验证程序控制链路，不能证明真实模型一定选对工具。需要重启 Node 与 Python，在游戏中复测用户原来的四条指令；导航超过 15 秒仍会结束本轮，移动继续由原控制器管理，不会无限等待或自动追击。未知物品/模组食物、重要物品丢弃、隔墙挖掘等原保护与限制保持。

## 2026-11-16：按用户要求移除丢弃物品的价值限制

本节覆盖前文阶段记录中的丢弃白名单与禁止丢完背包规则。用户明确要求工具开发阶段不判断贵重程度，物品价值偏好由后续记忆/学习层考虑。

- 移除 `DROPPABLE` 白名单和 `PROTECTED_ITEM` 拒绝；任意实际背包物品均可按名称和数量丢弃，包括钻石、附魔装备和附魔金苹果。
- 移除 `EMPTY_INVENTORY_FORBIDDEN` 限制，明确指定的物品即使是背包最后一件也可丢出；仍没有自动清空其他物品的批量接口。
- 保留单次 1–16 个整数、物品存在/数量充足、原主人权限、互斥/取消与实际数量变化验证；数量未变化返回 uncertain。
- Agent 提示词同步移除“不丢弃重要物品”，不要求按价值额外确认；名称或数量缺失时仍先澄清。
- 同步更新 AGENTS.md、任务规范和 README，避免未来重新引入价值白名单。旧测试中针对白名单及最后一件物品的断言按新的明确需求更新，其他功能断言保留。

修改：`minecraft-bot/src/tools/inventory/index.js`、`agent/app/tool_loop.py`、两端对应工具测试、AGENTS.md、README、任务规范及本文。

实际完整回归：**Node 126/126、Python 57/57 通过**。测试覆盖钻石、附魔装备、木板、丢完最后物品，以及数量为零/负数/小数/字符串/超上限、数量不足、不存在物品和执行未确认的情形。没有付费模型请求或游戏内实测。需要重启 Node 和 Python 后在游戏中验收。

## 2026-11-16：Movement 跑步、潜行与单次跳跃扩展

本次在原移动控制器上增加状态管理和物理帧协作，不重写 Task 002 的 GoalFollow、GoalNear、看门狗或导航等待，也没有新增依赖、Bot 实例或独立方向控制循环。

### 修改文件与职责

| 文件 | 变更 |
| --- | --- |
| `minecraft-bot/src/tools/movement/controls.js`（新增） | 管理跑步/潜行模式、优先级与恢复；有界单次跳跃；协调 pathfinder 物理帧按键及清理 |
| `minecraft-bot/src/tools/movement/tools.js`（新增） | 注册 set_sprint、set_sneak、jump 和各自参数 Schema |
| `minecraft-bot/src/tools/movement/index.js` | 原控制器接入模式/跳跃；保持旧接口和 getState 结构；增加 getMotionState；停止及生命周期统一清理 |
| `minecraft-bot/src/tools/index.js` | 组装三项新工具，现为 30 个注册工具；标记跟随/导航可接受同步模式调整 |
| `minecraft-bot/src/tools/registry.js` | 导航等待期间允许同步模式设置，保留导航锁、taskId 和取消归属；跳跃仍为互斥异步动作 |
| `minecraft-bot/src/tools/schema.js` | 通用严格布尔参数校验，不将字符串或数字当作布尔值 |
| `agent/app/tool_client.py` | 通用布尔 Schema/参数校验；继续从实际 Node 目录获取定义 |
| `agent/app/tool_loop.py` | 告知模型模式组合方式；跳跃失败使用程序定义提示，不回显内部错误 |
| `minecraft-bot/test/movement.test.js` | 新增模式、跳跃、取消、超时、互斥及实际插件/物理模拟集成覆盖 |
| `agent/test/test_tool_loop.py` | 模式→跟随串行调用、布尔严格校验，以及真实 Node 注册目录契约校验 |
| `AGENTS.md`、`minecraft-bot/README.md`、`docs/tasks/task006.md`、本文 | 记录开发规范、真实行为、30 个工具、调用示例及手动验收 |

旧转发模块、聊天命令、感知实现、原移动 HTTP 路由和真实配置均未改动。本节只记录本次移动扩展；工作区此前已有的导航等待、物品丢弃等修改保留。

### 接口和行为

新增工具通过原 `POST /api/tools/execute` 调用，仍使用 `{success,tool,data,error}` 工具信封，外层 HTTP 信封保持原样：

- `set_sprint({enabled:true|false})`：配置 pathfinder 是否允许安全路段跑步，返回 completed 和 motion 状态；不会自行向前走。
- `set_sneak({enabled:true|false})`：配置持续潜行，期间关闭有效跑步；关闭潜行恢复原先跑步设置。
- `jump({})`：仅地面/非液体状态发出单帧起跳脉冲，观察离地并落地才 completed；3 秒内不能确认则失败并停止移动。

`getMotionState()` 返回 `{sprint:null|boolean,sneak:boolean,jumping:boolean,allowSprinting:boolean}`。默认 sprint=null 延续原 pathfinder 自动速度选择，显式 false 禁止跑步。停止等清理操作恢复此默认值并释放按键；不代表下一次新导航也永远禁止跑步。

同一控制器的目标替换与正常到达保留持续模式；停止、取消、路径失败、看门狗超时、目标丢失、死亡、重生、维度切换及断线清理。同步模式设置可在有限导航等待中执行，不替换 active 工具或 pendingMovement。跳跃不创建新目标，并保留后台跟随/导航的请求取消归属；其它异步修改工具依然互斥。

已核对本机安装的 Mineflayer 4.39.0 官方文档及 physics.js、pathfinder 2.4.5 的 index.js / movements.js。使用实际 setControlState、getControlState、clearControlStates、physicsTick、allowSprinting 和 setMovements 接口。物理帧前后钩子只注册一次：释放上一帧手动脉冲→pathfinder 更新→施加模式/本次脉冲。在释放本模块手动脉冲或取消整个移动目标时补清理 Mineflayer 起跳队列 jumpQueued，避免停止后延迟起跳；升级依赖必须复查此版本细节。

### 实际自动化结果

- `npm.cmd test`：**Node 138/138 通过，失败/取消/跳过均为 0**，含全部原有测试。
- `.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s test -v`（agent 目录）：**Python 59/59 通过**，含全部原有测试。
- 50 个 JavaScript 文件 `node --check` 通过；Python `compileall` 通过。
- 当前 Python 虚拟环境实际为 **3.14.10**，没有声称在规范要求的 3.12 上验证；Node 依赖和 Python 依赖均未新增。

新增自动化验证了持续模式开关、潜行优先/恢复、权限与就绪、严格布尔参数、目标替换及旧回调、单次脉冲/落地确认、跳跃有界、停止与队列清理、AbortSignal、工具超时、死亡/断线、锁与监听器清理、导航等待时切换模式，以及 Python 使用实际 Node Schema。

三项新增集成测试使用实际 pathfinder A*、prismarine-physics 与有限内存地形，验证步行/跑步/潜行速度差异、潜行导航到达、单次跳跃起落、注册工具与控制器协作，以及移动中的跟随叠加跳跃、玩家改变方向后继续寻路。它们不连接真实 Minecraft，也没有调用付费模型；不能替代游戏内验收。

### 启动和 Minecraft 验收

重启 Node 和 Python 即生效，无需新增环境变量或安装依赖。保持正确的 LAN 端口；自然语言需要原有 `TOOL_CALLING_ENABLED=true` 和支持 function tools 的模型。启动命令及可绕过模型直接验证工具的 PowerShell 示例见 README“跑步、潜行与跳跃”“配置与启动”。

请在平坦、可控区域按以下顺序手动验证并记录结果：

1. 回归“你好”“状态”“背包”“附近”“环境”“跟随我”“前往 X Y Z”“停止”和原 HTTP 接口；确认寻路不挖掘或放置方块。
2. 检查 GET /api/tools 包含 30 个无重名工具，新模式参数为 boolean。自然语言“开启跑步，然后跟着我”，确认日志先 set_sprint 再 follow_player，Bot 仍保持跟随距离；禁止跑步后观察平地速度变化。
3. 跟随途中开启潜行，观察姿态与减速；关闭潜行后确认跟随继续、原跑步设置恢复。分别测试原先允许和禁止跑步两种情况。
4. “潜行前往”附近安全坐标，确认模式保留且最终到达；切换跟随/导航不创建双重目标。正常到达仍潜行，显式关闭可恢复站立。
5. 站立时和后台跟随/导航期间“跳一下”，确认只主动跳一次，实际落地后才回复完成；玩家改变方向后继续跟随。头顶受阻时应在 3 秒内反馈不能确认并停止，不能无限起跳。
6. 跳跃或移动期间输入旧指令“停止”，确认目标、跑步/潜行和手动跳跃脉冲被清理；之后不再重复起跳或恢复潜行。停止不会撤销已经发生的腾空惯性，Bot 会继续受游戏物理作用直到落地。
7. 在测试世界验证死亡/重生、换维度、失去跟随目标和断线后的清理；重新进入并发新指令时不继承旧显式模式。验证超时/无路不会无限重试。

### 已知限制

尚未进行本次 Minecraft 或真实模型测试，不能标记游戏验收完成。模型可能选错工具，可使用 README 的直接工具调用区分选择问题与执行问题。同步模式设置可与导航等待组合；jump 仍不能插入另一个尚未返回的异步工具（BUSY），需等返回 pending/completed，或以 wait_seconds=0 启动后台导航。路径安全判断、饥饿和服务端规则决定能否实际跑步；潜行较慢，原坐标任务 60 秒和无进展 15 秒限制保留。不会自动游泳跳跃、连跳或调整任务时限。
