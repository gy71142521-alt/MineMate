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
| `minecraft-bot/src/tools/index.js` | 26 个工具的唯一注册组装入口 |
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

完整启动命令（逐条）、环境变量、26 个工具及参数、HTTP 请求/响应示例、游戏内 12 项验收清单见 [README 的 Task 006 小节](../../minecraft-bot/README.md#task-006统一游戏工具与-tool-calling)。

- Python：在 `agent` 中执行 `.\.venv\Scripts\python.exe -m app.main`。
- Node：在 `minecraft-bot` 中执行 `npm.cmd start`，并配置当前 LAN 端口和正常认证。
- 启用新循环：Python `TOOL_CALLING_ENABLED=true`；Node `AGENT_TIMEOUT_MS=120000`，修改后重启两端。
- Node `GET /api/tools`、`POST /api/tools/execute`、`POST /api/tools/cancel` 复用原 HTTP 服务；旧接口保留。
- 外层 HTTP success 表示请求被处理，工具自身是否成功必须继续检查 `data.success`；pending 和 uncertain 都不是任务完成。

**真实 Minecraft 游戏内测试尚未执行。不能据自动化通过认定 Task 006 实机验收完成。** 需要用户依次验证旧聊天/移动/感知、明确目标的挖掘/拾取/放置、有限攻击、物品交互、工作台/熔炉/容器、取消与断线清理，以及真实模型能根据实际结果回答。

## 已知问题与 Task 007 建议

1. 当前供应商的工具协议请求被拒绝，需要核实其 Chat Completions function tools 参数支持或选择明确兼容的接口/模型；不会绕过参数验证执行普通文本。
2. 未执行 Minecraft 实测，不同游戏版本、模组、自定义配方、时延和其他玩家同时修改环境仍需验证；结果不明时返回 uncertain/pending。
3. 基础熔炼、丢弃、道具使用和敌对实体采用保守白名单。酿造、附魔、铁砧、砂轮、高炉、烟熏炉等未实现，预留独立适配器扩展。
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
