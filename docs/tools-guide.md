# MineMate 现有游戏工具使用指南

本文依据当前 `minecraft-bot/src/tools/` 注册表、各工具实现及 Python 执行器整理，覆盖 **39 个实际注册工具**。适合查看当前能做什么、如何调用、为什么失败，以及哪些复杂行为还没有实现。

文中范围和数量是代码默认值或硬限制，不代表 Minecraft 本身的限制。示例坐标、玩家昵称和实体 ID 仅展示格式，使用前应替换为当前世界的真实信息。源码更新后，以 `GET /api/tools` 返回的实际 Schema 为准。

## 1. 先区分三种使用方式

| 使用方式 | 入口 | 当前行为 |
| --- | --- | --- |
| 游戏内固定命令 | 聊天栏 | 直接走现有聊天/移动/感知处理，不依赖 LLM |
| 自然语言工具调用 | 聊天 → Python `/api/agent/message` | LLM 从 Node 注册目录选择工具；需开启 Tool Calling，支持有界串行调用 |
| 后台显式工具序列 | Python `POST /api/agent/tasks` | 调用方提供步骤，后台执行、等待导航/熔炼并验证指定背包条件；不自动生成计划 |

固定命令如下：

| 输入 | 功能 |
| --- | --- |
| `你好` | 固定问候 |
| `状态` | 生命、饥饿、坐标、维度、经验和存活信息 |
| `背包` | 物品名称与数量摘要，内容过多会截断 |
| `附近` | 附近实体类别与距离摘要 |
| `环境` | 附近常见方块和资源摘要 |
| `跟随我` | 跟随消息发送者 |
| `前往 100 65 200` | 导航到坐标附近 |
| `停止` | 取消当前工具操作、停止移动并清理移动模式 |

固定命令优先，不会把同一条消息再发给 LLM。Bot 自身聊天会被忽略。

“请拿钻石斧挖指定方块”等自然语言不是新增的固定命令。它能否正确执行，还取决于模型是否选择了正确工具、参数及前置查询。`TOOL_CALLING_ENABLED=false` 时，自然语言主要返回意图和回复，不会执行游戏工具；`AGENT_CHAT_ENABLED` 也需要开启才能从游戏聊天转发。

当前自然语言循环默认最多 4 次工具调用；模型一轮返回多个调用时会按顺序串行执行（单次最多 8 个），执行到失败、`pending` 或 `uncertain` 后停止同批剩余调用。它不会自动提交后台任务，也不是完整的多步骤自主规划系统。

资源名称不在 Python 中维护静态别名。涉及物品或方块的请求必须先调用 Node 工具 `resolve_resource`，从当前 Mineflayer 运行时 registry 返回真实 `name`、显示名和候选置信度；该 registry 包括当前版本和已加载的模组内容。候选为空或不唯一时必须澄清，不得猜测或执行；解析工具只读，不改变 Bot、背包或世界。

## 2. 所有工具共有的规则

- 游戏工具使用同一个 Mineflayer Bot，游戏控制只经过 Node。
- 通过注册表执行时，Bot 必须已出生、仍连接且存活；未就绪会返回 `BOT_NOT_READY`。
- 配置 `OWNER_USERNAME` 后，修改游戏的工具只允许该玩家调用；只读工具不受同样的主人限制。
- 物品和方块名称采用当前版本的英文 ID，例如 `oak_planks`、`raw_iron`、`diamond_axe`，参数中不加 `minecraft:`。中文名称由模型理解后转换，不是工具直接接收的格式。
- 参数严格校验；数字不能写成字符串，布尔值使用 `true/false`，不接受任意额外字段。
- 方块 `position`、`reference_position` 使用整数 `{x,y,z}`；导航 `x/y/z` 可传有限小数，移动控制器会向下取整。
- 实体和候选位置距离使用包含高度差的三维距离。实体距离通常从 Bot 脚部计算，方块交互的距离检查从 Bot 近似眼部到方块中心计算，两者不能直接混同。
- 修改工具互斥执行；一般非移动修改工具会先停止原移动，再进行操作。只读查询不主动停止移动。
- 停止、超时和断线会尽可能清理正在执行的控制，但已经发生的世界变化不会回滚。

工具返回：

```json
{
  "success": true,
  "tool": "craft_item",
  "data": {"status": "completed"},
  "error": null
}
```

`success: true` 表示取得了合法工具结果。**仍要检查 `data.status` 和具体证据：**

| 结果 | 应如何理解 |
| --- | --- |
| `completed` | 工具自己的成功条件已被观察到；不一定等于整个用户目标完成 |
| `pending` | 已启动或尚在等待，不能宣称完成 |
| `uncertain` | 发送过操作，但缺少足够证据；先查询状态，不盲目重试 |
| `failed` / `cancelled` | 例如移动历史的失败或取消终态，不能当作到达 |
| `success: false` | 查看 `error.code`；参数、条件或执行发生问题，不能认为没有任何副作用 |

感知结果通常直接返回状态对象或 `{available, items, ...}`，不一定包含 `data.status`。未知字段可能为 `null`；空列表与不可用状态应结合 `available` / `availability` 判断。

## 3. 感知与名称解析工具：6 个

除名称解析外，以下查询参数都是空对象，只读取当前已知游戏数据。

| 工具 | 参数示例 | 能返回什么 | 注意事项 |
| --- | --- | --- | --- |
| `get_world_snapshot` | `{}` | `player`、`inventory`、`equipment`、`nearbyEntities`、`nearbyBlocks`、时间、可用性、截断与范围信息 | 统一 JSON 快照，不是全世界地图 |
| `query_status` | `{}` | 生命、饥饿、数值坐标、经验等级、维度、存活状态、移动任务摘要 | 移动摘要主要反映跟随/导航，不代表所有动画或工具操作 |
| `inspect_inventory` | `{}` | 非空背包槽位与装备视图：名称、数量、槽位 | 不是按耐久、附魔或 NBT 精确选择物品的接口；不要把物品视图和装备视图简单相加 |
| `nearby_entities` | `{}` | 玩家、被动生物、敌对生物、其他实体，附 ID、坐标、距离和有效标记 | 默认 16 格、最多 32 项；分类依赖 Mineflayer 实体类型信息，不是威胁决策 |
| `nearby_blocks` | `{}` | 附近常见方块的名称、坐标、距离 | 默认 5 格、最多 64 项，按距离截断；只查既定资源列表 |
| `resolve_resource` | `{"query":"raw iron","kind":"item"}` | 当前注册表中的真实名称、显示名和候选；无法唯一确定时澄清 | kind 为 item/block/entity/any；any 仅物品与方块，不维护 Python 静态别名表 |

`nearby_blocks` 当前列表：`oak_log`、`birch_log`、`spruce_log`、`stone`、`cobblestone`、`dirt`、`grass_block`、`sand`、`water`、`coal_ore`、`iron_ore`、`deepslate_coal_ore`、`deepslate_iron_ore`。查其他指定方块应使用 `find_block`。

实体半径、返回数量、方块半径及缓存通过 `PERCEPTION_*` 配置。方块半径最多 5 格；默认缓存 1 秒，位置变化、维度变化、死亡、断线和相关世界更新会使缓存失效。扫描只使用已加载数据，不持续扫描整个世界，也不会自动发现敌人后逃跑。

原始感知接口即使未出生或断线也能生成顶层结构稳定的不可用快照；但注册工具入口会拒绝未就绪 Bot。HTTP `GET /api/world` 在未就绪时可返回带快照的失败信封，调用方需同时看 HTTP 状态与 `availability`。

## 4. 移动与站位工具：8 个

| 工具 | 参数示例 | 能做什么 | 范围与限制 |
| --- | --- | --- | --- |
| `follow_player` | `{}` | 持续跟随调用玩家，动态更新目标 | 保持约 3 格；只跟随调用者，不接收任意玩家名；玩家实体须已加载且同维度 |
| `goto_position` | `{"x":10,"y":64,"z":8,"wait_seconds":0}` | 导航到指定坐标附近，返回移动 taskId | `arrival_range` 可选 0～2，默认 2；0 要求进入指定脚部方块，适用于交互站位。`wait_seconds` 默认 15、范围 0～15 秒；等待结束可返回 pending，到达后仍须检查实际交互条件 |
| `get_movement_task` | `{"task_id":1}` | 查询原移动任务的运行/到达/失败/取消记录 | 不启动新目标；最多保留 128 条记录；arrived 才映射为 completed |
| `stop_movement` | `{}` | 最高优先级取消当前工具，停止移动，释放跑步/潜行/跳跃状态 | 不恢复已消耗材料、不撤回熔炉投料 |
| `set_sprint` | `{"enabled":true}` | 开启或关闭寻路跑步许可 | 不会自行向前走；寻路器只在其判断合适的路段跑步 |
| `set_sneak` | `{"enabled":true}` | 开启或关闭持续潜行 | 潜行覆盖跑步，关闭后恢复此前跑步设置 |
| `jump` | `{}` | 单次跳跃，观察起跳和落地 | 需要地面等起跳条件，最多等 3 秒；可与已有跟随/导航组合，不新增导航目标 |
| `find_interaction_positions` | `{"position":{"x":10,"y":64,"z":8},"radius":3,"max_results":8}` | 查询目标附近可站立、距离合适且射线通畅的候选脚部坐标 | 目标距 Bot ≤16 格；radius 可选 1～4，默认 3；max_results 可选 1～16，默认 8；不保证路径可达 |

默认导航目标距离限制为 256 格，插件搜索半径为 256 格，单次路径搜索时限为 5 秒，分别由 `PATHFINDER_MAX_DISTANCE`、`PATHFINDER_SEARCH_RADIUS`、`PATHFINDER_THINK_TIMEOUT_MS` 控制。超出本次范围不等于世界中永远不可达。

坐标导航另有 60 秒总时限和连续 15 秒无进展限制；跟随没有 60 秒总时限，但目标丢失、超范围、寻路失败或无进展时会停止。

寻路默认禁止挖掘、放置方块、搭柱，不会主动拆墙铺路。新跟随/坐标目标可替换旧目标；停止及失败会清理模式，正常到达可保留模式，直到明确关闭或后续操作清理。

跑步/潜行属于同步模式设置，在兼容的导航工具等待期间可调整；jump 等异步修改工具仍受单操作锁约束。已有导航目标时可以叠加跳跃，不代表能在尚未返回的另一个修改工具调用中并行启动跳跃；遇到 BUSY 应先等待或明确停止。

`find_interaction_positions` 只接受完整安全支撑和两格空气，排除危险支撑及其他实体占用，默认最多检查 245 个网格、radius=4 时最多 405 个。它不移动、不转头、不设路径，也不缓存候选。返回 `pathVerified:false`；半砖等复杂站位可能被保守排除。

**到达“附近”不保证已经能交互。** 后续挖掘/放置/攻击仍要通过实际距离和目标条件检查。当前没有独立注册的自由转头或“跳起来同时向脚下搭方块”工具。

## 5. 方块查询与挖掘：4 个

| 工具 | 参数示例 | 能做什么 | 范围与限制 |
| --- | --- | --- | --- |
| `inspect_block` | `{"position":{"x":10,"y":64,"z":8}}` | 读取该坐标实际方块、距离、当前可挖与可见标记 | 只读，但目标须在交互范围内，默认 ≤4.5 格且区块已加载 |
| `find_block` | `{"block_name":"iron_ore","radius":8,"max_results":8}` | 在有限范围查找当前版本的指定方块 | radius 可选整数 1～16，默认 5；max_results 可选整数 1～16，默认 8；按距离排列已找到的候选，并返回 `visible`/`diggable`；最近候选不代表可见或可达，挖掘前仍需 inspect_block/调整站位 |
| `inspect_mining_sources` | `{"item_name":"raw_iron","max_results":16}` | 从运行时 drops 查询可能的来源方块；可选 block_name 限定来源 | 只读，不扫描世界；max_results 1～16，默认 16。不是实际掉落保证，不考虑附魔/服务端数据包，须验证背包 |
| `mine_block` | `{"block_name":"oak_log","position":{"x":10,"y":64,"z":8},"tool_name":"diamond_axe"}` | 挖掘一个明确坐标、明确名称的方块；可指定工具 | 可选 max_distance 为 1～4.5，默认 4.5；不自动导航、不自动拾取、不批量挖掘 |

挖掘前会检查名称是否变化、方块是否允许破坏、是否为脚下支撑、采集条件、工具耐久、距离、可挖性和视线。明确指定的工具不存在或不适用时会报错；未指定且当前手持不能采集时，会尝试从背包寻找可用工具。

基础挖掘拒绝空气、水、岩浆、基岩，以及名称匹配箱子、木桶、熔炉、潜影盒、刷怪笼和工作台等受保护目标。它不会因为一块矿石在脚下就自动改挖另一块。

`mine_block` 观察目标变成空气后才返回 completed，`pickupVerified:false` 明确表示没有确认掉落物入包。Minecraft 可能在挖掘后自动把掉落物吸进背包；此时 `collect_item` 会返回 `ITEM_ENTITY_MISSING`，应先 `inspect_inventory` 核对数量，不要重复挖同一位置或反复调用拾取。挖铁矿不一定得到同名物品，现代版本可能得到 `raw_iron`；应按当前游戏结果查询和拾取。需要“采集到背包总数为 N”时，优先使用 `gather_resource` Skill，它会在拾取前检查库存并在最终背包核验。

`canSeeBlock` 检查射线遮挡，不要求当前已经朝向方块；Mineflayer 默认挖掘会转向目标。背对目标与隔墙是两种情况，转头不能消除实体障碍。

## 6. 放置与建造：2 个

| 工具 | 参数示例 | 能做什么 | 范围与限制 |
| --- | --- | --- | --- |
| `inspect_placement` | `{"block_name":"dirt","position":{"x":10,"y":64,"z":8},"reference_position":{"x":10,"y":63,"z":8},"face":"up"}` | 只读检查目标为空、支撑面正确、实体不碰撞 | 用于放置，不是挖掘前查询；不检查背包中是否有放置物品 |
| `place_block` | `{"block_name":"dirt","position":{"x":10,"y":64,"z":8},"reference_position":{"x":10,"y":63,"z":8},"face":"up"}` | 从背包装备并放置一个方块，再读回实际方块验证 | 不覆盖原方块，目标及参照方块都需在交互范围内 |

四个参数都必填。`face` 为 `up/down/north/south/east/west`，目标必须等于参照位置沿该面移动一格的位置。参照方块应提供可靠实体支撑；箱子、门、按钮等会触发交互的参照方块被拒绝。

当前不是蓝图建造系统，不会自行选择参照面、连续搭桥、调整站位或协调跳跃放置。`inspect_placement` 对已有方块返回 OCCUPIED 是正确的放置反馈，不能用它判断“是否应该挖这个方块”。

## 7. 背包、拾取、装备与使用：5 个

| 工具 | 参数示例 | 能做什么 | 范围与限制 |
| --- | --- | --- | --- |
| `collect_item` | `{"item_name":"raw_iron","radius":8}` | 接近并拾取指定名称的有效掉落物，核对背包增量 | radius 可选 1～8、默认 8；可选 entity_id 精确指定实体；无数量参数 |
| `equip_item` | `{"item_name":"diamond_sword","destination":"hand"}` | 将指定物品装备到目标位置并验证名称 | destination 必填，可选 hand、off-hand、head、torso、legs、feet |
| `consume_item` | `{"item_name":"enchanted_golden_apple"}` | 装备并食用当前支持的食物、牛奶或蜂蜜，核对物品减少 | 一次消费操作，不支持按数量连续进食；未确认数量下降返回 uncertain |
| `drop_item` | `{"item_name":"oak_planks","count":4}` | 丢弃明确名称和数量的物品，核对背包减少量 | count 必填整数 1～16；允许任何物品种类及丢完最后一件，不判断贵重程度 |
| `use_item` | `{"item_name":"shears","entity_id":42}` | 食物/牛奶/蜂蜜转入消费流程，或短暂举盾、对指定羊使用剪刀 | 不是通用右键工具；剪刀要求有效羊实体在 3 格内；盾牌举约 0.5 秒 |

拾取默认选择范围内最近的指定名称掉落物；目标初始位置用于本次靠近，不是持续追踪任意移动掉落物。最多观察约 10 秒；实体消失但没有背包增量时返回 uncertain。它不能从箱子拿物品，箱子取物应使用 `transfer_item`。

普通食物在饥饿值满时会被拒绝；金苹果、附魔金苹果允许满饥饿食用。当前不提供通用药水、弓、钓鱼竿、水桶等使用适配器。

举盾和剪羊目前返回 uncertain：发送动作或观察到 metadata 改变，不足以确认防御状态或剪羊最终收益。equip_item 验证装备名称，不验证后续攻击、护甲效果或特定附魔。

按名称操作不等于按槽位或附魔选择。同名但耐久、附魔不同的多件物品，当前接口不能可靠表达“只用这把附魔剑”“只丢最旧的一个”；查询得到的槽位不能直接作为这些工具的选择参数。

## 8. 实体查找与战斗：2 个

| 工具 | 参数示例 | 能做什么 | 范围与限制 |
| --- | --- | --- | --- |
| `find_entity` | `{"entity_type":"zombie","radius":16,"max_results":8}` | 查找指定实体名称或类型，返回 ID、位置、距离 | radius 可选 1～16、默认 16；max_results 可选整数 1～16、默认 8；按距离排序 |
| `attack_entity` | `{"entity_id":42,"weapon":"diamond_sword","max_distance":3,"max_hits":3}` | 转向并攻击指定敌对实体，可先装备剑或斧 | entity_id 必填；max_distance 可选 1～3、默认 3；max_hits 可选整数 1～3、默认 1；不追击 |

实体查找排除 Bot 自身、无效实体及超范围实体。**attack_entity 自身不自动选择最近怪物**：调用方先查询，再提供真实 entity_id。

攻击只允许当前代码中的敌对类型：zombie、skeleton、creeper、husk、drowned、stray、wither_skeleton、silverfish、endermite、blaze、magma_cube、slime、ghast、guardian、elder_guardian、phantom、shulker、pillager、vindicator、evoker、ravager、zoglin、piglin_brute、warden。玩家、动物、中立生物及其他未列出的类型被拒绝。

参数 `weapon` 可省略，工具不会因此自动选择最佳武器；指定时仅接受名称以 `_sword` 或 `_axe` 结尾的物品，并检查存在性与耐久。每次攻击前检查目标仍在三维距离内、身份仍有效，转向后还要求 entityAtCursor 命中该实体。

观察到受伤或死亡事件可以返回 completed，但只有 `deathObserved:true` 才确认死亡；`damageObserved:true` 只确认造成过伤害。没有这些证据时返回 uncertain，不会自动持续打到死亡，也没有自动索敌、躲避爆炸或撤退恢复系统。

## 9. 功能方块交互与容器：5 个

| 工具 | 参数示例 | 能做什么 | 范围与限制 |
| --- | --- | --- | --- |
| `open_workbench` | `{"position":{"x":10,"y":64,"z":8}}` | 打开并检查工作台窗口类型，然后关闭 | 不会合成，合成用 craft_item |
| `open_furnace` | `{"position":{"x":10,"y":64,"z":8}}` | 返回普通熔炉输入、燃料、输出、进度及燃料状态，然后关闭 | 不投料、不取物；不支持高炉/烟熏炉适配 |
| `open_container` | `{"position":{"x":10,"y":64,"z":8}}` | 返回箱子、陷阱箱或木桶的物品快照，然后关闭 | 不包括末影箱、潜影盒及任意模组容器 |
| `interact_block` | `{"position":{"x":10,"y":64,"z":8}}` | 对指定门、按钮或拉杆发送一次交互，观察方块属性变化 | 不接受期望 open/closed 状态；实际能否切换取决于游戏规则，例如铁门需红石 |
| `transfer_item` | `{"position":{"x":10,"y":64,"z":8},"direction":"withdraw","item_name":"coal","count":4}` | 在支持的箱子/木桶与背包间存取，验证双方增减 | direction 为 deposit/withdraw；count 必填整数 1～16；不操作熔炉槽位 |

功能方块操作需要已加载、类型匹配且在约 4.5 格交互范围内。窗口工具在本次操作结束、异常或取消时关闭窗口，不返回可供下次继续操作的 Mineflayer 句柄。

这些 open 工具会真实打开游戏窗口，注册为修改工具，可能停止移动；它们不等同于纯只读感知。无需先调用 open_workbench 才能 craft_item，也无需先 open_container 才能 transfer_item，实际操作工具会自行处理窗口。

`interact_block` 观察不到属性变化时返回 uncertain，而不是宣称门已打开。容器存取需要来源数量足够、目标存在可接收槽位；不自动整理背包、分拆 64 个的请求或跨多个容器寻找物品。

## 10. 配方、合成与熔炼：7 个

| 工具 | 参数示例 | 能做什么 | 范围与限制 |
| --- | --- | --- | --- |
| `inspect_recipe` | `{"item_name":"stone_pickaxe","count":1,"max_results":8}` | 只读查询当前版本配方、工作台需求、整批产量、材料数量与背包缺口 | count 可选整数 1～64、默认 1；max_results 可选整数 1～16、默认 8；不保证附近有工作台 |
| `inspect_smelting_recipe` | `{"item_name":"raw_iron","count":2}` | 只读查询现有熔炼适配器的原料、产物及燃料需求 | count 可选整数 1～64、默认 1，可查询续接炉内旧原料后的完整队列；不代表支持所有原版/模组熔炼配方 |
| `craft_item` | `{"item_name":"stone_pickaxe","count":1,"position":{"x":10,"y":64,"z":8}}` | 使用已有材料合成，读背包实际增量验证产物 | count 必填整数 1～16，表示期望产物数；position 只能是**已有工作台**的坐标，不是放置目标；不需要工作台的配方省略position；实际整批产量最多 64 |
| `smelt_item` | `{"position":{"x":10,"y":64,"z":8},"action":"start","item_name":"raw_iron","fuel_name":"coal","count":2}` | 普通熔炉分阶段 start/status/take | position/action 必填；count 与 fuel_count 上限统一 64；count 默认 1，未指定 fuel_count 时计算需求，超过单槽容量则在投料前拒绝 |
| `furnace_add_input` | `{"position":{"x":10,"y":64,"z":8},"item_name":"raw_iron","count":3}` | 向空原料槽添加、向同类原料槽补充本次数量 | count 必填 1～64；校验配方/输出类型/真实堆叠容量；不覆盖已有不同物品 |
| `furnace_add_fuel` | `{"position":{"x":10,"y":64,"z":8},"count":1}` | 添加/补充燃料，省略 fuel_name 时自动选择 | count 可选 1～64、默认 1；空槽优先煤炭；非空槽只补同类，不能混放 |
| `furnace_take_output` | `{"position":{"x":10,"y":64,"z":8},"item_name":"iron_ingot"}` | 独立取出当前输出整栈，包含满栈 64 个 | count 可选 1～64，显式给出须等于当前整栈；加工中也可取，不支持部分取出 |

### 10.1 合成的具体边界

合成按当前版本 Mineflayer 配方执行，不另行硬编码石镐、工作台等制作方法。`position` 表示“去哪个已有工作台合成”，不是“把产物放到哪里”；`inspect_recipe` 的 `requiresTable=false` 时必须省略 position。制作工作台本身是 2×2 配方，不需要工作台；制作后用 `inspect_placement` 和 `place_block` 放下，`craft_item` 不会自动放置。

`count` 是期望产物数量，不是配方执行次数。例如配方一批生成 4 块木板，要求 5 块会执行两批，预期得到 8 块；查询和返回结果中的 operations、expected、actualIncrease 用于核对不同阶段的数量。

它不会自动采木、合成前置材料、放置工作台、从箱子取材料或挑选指定配方变体。配方查询可以看到多个变体，但 craft_item 没有 recipe_id 参数，会使用当前条件下 Mineflayer 返回的可用配方。

### 10.2 熔炼的三个阶段

| action | item_name 的含义 | 当前行为 |
| --- | --- | --- |
| `start` | 原料，例如 raw_iron | 需要 item_name 和 fuel_name；检查材料及燃料量，要求输入/燃料/输出槽均无物品，再投入并返回 pending |
| `status` | 可选的期望产物，例如 iron_ingot | 返回炉内快照；输出名称符合且数量至少为 count 时 outputReady=true、completed，否则 pending |
| `take` | 必填的期望产物，例如 iron_ingot | 要求实际输出名称相同，**实际输出数量恰好等于 count**；取出整批并核对背包增量 |

status/take 的调用格式示例：

```json
{"tool":"smelt_item","arguments":{"position":{"x":10,"y":64,"z":8},"action":"status","item_name":"iron_ingot","count":2}}
```

```json
{"tool":"smelt_item","arguments":{"position":{"x":10,"y":64,"z":8},"action":"take","item_name":"iron_ingot","count":2}}
```

当前支持的熔炼输入：

| 输入 | 输出 |
| --- | --- |
| raw_iron、iron_ore、deepslate_iron_ore | iron_ingot |
| raw_gold、gold_ore、deepslate_gold_ore | gold_ingot |
| raw_copper、copper_ore | copper_ingot |
| sand | glass |
| cobblestone | stone |
| beef、porkchop、chicken | cooked_beef、cooked_porkchop、cooked_chicken（分别对应） |
| potato | baked_potato |
| oak_log、birch_log、spruce_log | charcoal |

共享燃料表位于 `tools/crafting/data.js`：coal/charcoal 每个支持 8 个原料，coal_block 为 80、dried_kelp_block 为 20、blaze_rod 为 12、stick 为 0.5、bamboo 为 0.25；橡木、云杉、白桦、丛林、金合欢、深色橡木、红树、樱花、苍白橡木的 planks/log/wood 及去皮 log/wood 按 1.5 计算，仅使用当前 registry 存在的名称。未指定旧 start 的 fuel_count 时按本批 count 向上取整。该表是明确支持的原版燃料规则，不能据此声称识别所有可燃物、模组燃料或数据包修改。

**旧 smelt_item 接口在熔炉使用中会怎样：**

- 输入、燃料或输出槽只要有任何物品，start 返回 FURNACE_BUSY，即使已有物品也是铁矿或剩余煤炭。
- 旧 start 不会追加原料或单独补燃料，这两项改用下节的新子工具；取回输入/燃料和自动选择其它炉子仍未实现。
- status 的“至少够数量”与 take 的“恰好等于数量”不同。例如期望 2 个但输出 3 个，status 可显示就绪，take 仍拒绝。
- 旧 start/status/take 的 count 与 fuel_count 上限均为 64，take 仍要求产物整批数量准确匹配；新 furnace_take_output 可省略 count 取当前整栈，支持加工中取出。两者都不支持分取。
- 取出前无需重复 start。原料投入后的等待不等于已熔炼完成，更不等于铁锭已入背包。

### 10.3 添加/补充与独立取出

“添加”和“补充”都是向指定槽位追加本次数量，因此共用三个工具覆盖五种操作。每次自行打开/关闭窗口，复用原互斥、租约、超时、取消和回执；不需要先 open_furnace 保持窗口。

- **原料**：空槽可添加，同类且数据兼容时可补充。已有不同原料或不同产品分别返回 FURNACE_INPUT_CONFLICT / FURNACE_OUTPUT_CONFLICT，不替换工艺。输出槽已满返回 FURNACE_OUTPUT_FULL，应先明确取出产品；没有自动取走他人物品。
- **燃料**：空槽自动选煤炭；背包没有煤炭时按共享支持表尝试木炭、煤炭块等已支持燃料，也可用 fuel_name 指定。非空燃料槽只能同类补充，即使背包有煤也不覆盖已有其它燃料。count 是本次燃料件数，不是期望加工件数；所选燃料不足会失败，不静默减少数量或混合多种燃料。
- **产品**：使用官方 takeOutput 取当前输出整栈，无需等待炉内全部原料加工完。可省略 count 自动取当前栈；显式 count 与整栈不符时返回 OUTPUT_COUNT_MISMATCH，避免误以为只取了部分。输出 64 时可整栈取出，再补充原料；不会自动循环取出/补料。
- **容量**：按当前窗口背包和实际 stackSize 检查，不假定所有物品都能堆叠 64 个。加工中取出须预留一个完整产品栈的容量，防止操作期间新增产物溢出；容量不足不自动丢弃物品。
- **核验**：读取窗口内实时背包；原料已经加工为产品时检查数量守恒，燃料即时消耗一件时观察燃烧秒数增长。属性/槽位更新最多有限等待，不能确认时返回 uncertain，不重复投料/加燃料。completed 只表示当前转移已确认，不代表熔炼目标全部完成。
- **范围**：当前仅普通 furnace，原料仍受共享配方适配器约束。不支持任意模组燃料、高炉/烟熏炉、取回输入/燃料或输出任意数量分取。冲突时寻找其它炉子留给后续 Skill。

### 10.4 独立工具测试命令

先重启 Node/Python，确认 Bot 已进入测试世界并站在熔炉 4.5 格内，准备至少 6 个粗铁和 2 个煤炭。在项目根目录分别执行，Steve 和 10,64,8 换成实际玩家名、熔炉坐标：

```powershell
Set-Location 'D:\桌面\MineMate'
```

添加 3 个粗铁：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "& .\scripts\test-furnace-tool.ps1 -Player Steve -Action add_input -Position 16,72,-324 -ItemName raw_iron -Count 3"
```

添加燃料，默认自动选煤炭：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "& .\scripts\test-furnace-tool.ps1 -Player Steve -Action add_fuel -Position 16,72,-324 -Count 1"
```

使用中继续补充 3 个粗铁，使用相同的 add_input 命令；补燃料也重复 add_fuel，表示新的明确追加操作，不是响应丢失后的自动重试。

产物出现后取出当前铁锭整栈：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "& .\scripts\test-furnace-tool.ps1 -Player Steve -Action take_output -Position 16,72,-324 -ItemName iron_ingot"
```

脚本直接调用 Node 现有工具接口，一次只发一个修改请求，打印 request_id 和真实结果。不启动服务、不接入 LLM、不重试超时；原 tool_calling 模型仍可从同一 Node 目录发现这些工具。自定义 Node 端口用 `-NodeUrl`。

游戏验收还应检查：无煤但有木炭/原木时回退选料；燃料槽已有木炭时只同类补充；炉内是其它原料或产品时拒绝；输出 64 取出后可继续补料；背包不足、停止、断线、窗口被占用时正确终止。自动化测试使用受控世界/窗口及官方 API 插件，不能代替真实 Minecraft 验收。

## 11. 可以组合哪些实际流程

以下为调用顺序建议，不代表自然语言模型会自动完整执行。

| 目标 | 现有工具可组成的流程 | 仍需调用方决定或确认 |
| --- | --- | --- |
| 挖一个已知坐标方块并收取掉落 | inspect_block → 必要时 find_interaction_positions/goto_position → 重新检查 → mine_block → collect_item → inspect_inventory | 方块被挡住时换哪一站位；预期掉落物名称；背包最终增量 |
| 制作工作台 | inspect_recipe(crafting_table) → craft_item(crafting_table, count=1，省略 position) → inspect_placement → place_block → inspect_inventory | 缺木材时不能跳过采集和木板合成；制作不等于放置，place_block 需要 position/reference_position/face |
| 使用现有材料做石镐 | inspect_recipe → 确认材料和可触及工作台 → craft_item → inspect_inventory | 木棍、石料不足时先补齐；未提供工作台不能完成相应配方 |
| 用空熔炉烧两块粗铁并取出 | inspect_smelting_recipe → open_furnace 核对空炉 → smelt_item(start) → 有界 status → take → inspect_inventory | 燃料、背包空间、真实炉子位置；占用时停止，不覆盖 |
| 攻击附近一只僵尸 | find_entity → 必要时 goto_position → 再确认目标 → attack_entity | 选择哪个 entity_id；移动后实体可能已离开；造成伤害不等于击杀 |
| 从木桶拿煤 | open_container → transfer_item(withdraw) → inspect_inventory | 煤炭数量和背包空间，不会自动搜索全世界所有木桶 |

挖掘、拾取、合成、放置是不同的操作及成功条件；不要把“挖掉一块木头”当作“拿到木材”，或把“背包有工作台”当作“工作台已在指定位置放好”。

## 12. 后台执行器目前能补上什么

后台入口、完整 JSON 示例、启动方式见 [Task 007 前置衔接说明](tasks/task007-preparation.md)。当前支持：

- 提交显式序列后立即返回任务 ID，查询进度或取消，不占用整个聊天 HTTP 请求等待。
- 从 Node 取得真实工具 Schema；只允许受限 JSON 前序结果引用，替换后再校验。
- 任务步骤串行执行；最多 24 步，总时限默认 300 秒/最大 600 秒，工具调用默认 96 次/最大 192 次。
- goto_position 返回 pending 后查询原 taskId，真实到达后继续；不反复重启导航。
- smelt_item(start) 返回 pending 后，自动有界 status 轮询并 take 整批产物；**此流程已经包含 take，不要再排一次重复取出。**
- 保存请求回执和执行账本；结果不确定时核对原请求及状态，不自动重复副作用。
- 显式 verify_inventory 验证最终物品总量或任务期间增量；未提供最终验证条件时返回 finished_unverified，不报告整个目标完成。

后台步骤的 wait_seconds 与 goto_position 参数中的 wait_seconds 不同：前者限制执行器的后续等待，默认 90 秒、最大 180 秒；后者限制单次导航工具返回前等待，最大 15 秒。

Python `smelt_item` Skill 当前只启动熔炼：新增原料 1～64，优先填当前兼容炉，再把剩余量分配到其它炉；实际投放核验后结束并释放控制权，不等待产品、不自动补燃料、不取旧/新产品，尚未实现收集 Skill。use_existing 仅允许同工艺续接，输出满槽时跳过。completed 指投料完成，不能当成铁锭已入包；弱燃料待补量另行报告。原 Node start/status/take 与取产品工具保留独立能力，旧显式工具序列的等待合同保持。见 [熔炼技能说明](../agent/skills/smelt_item/SKILL.md)。

**熔炼等待期间当前不会调度其他修改动作。** 后台序列仍持有整个任务租约，其他修改工具被拒绝；只读状态查询可进行。没有独立的威胁监测、安全撤离、暂停恢复或“炉子烧矿、Bot 同时干别的”的调度。

需要停止时可使用固定“停止”或取消接口。停止后已经投入的炉子仍按游戏规则工作，但自动等待/取出流程结束；再次提交 start 可能因炉内已有物品而失败。不要把取消当作清空炉子。

后台序列不接受持续的 follow_player 和抢占式 stop_movement，二者继续使用原入口。任务运行时发新的手动导航可能在无单次操作锁时抢占旧任务；稳妥的明确切换方式是先停止，再发新目标。

## 13. 常见反馈如何处理

| 错误或状态 | 常见原因 | 下一步 |
| --- | --- | --- |
| BOT_NOT_READY / NOT_READY | 尚未出生、死亡、断线或游戏数据不齐 | 等待就绪，检查连接日志 |
| INVALID_ARGUMENT | 类型、字段、数量或范围不符合 Schema | 查询 GET /api/tools，按工具参数修正 |
| BUSY | 正在执行另一修改操作，或后台任务持有租约 | 等待或先明确停止，不重复发送同一动作 |
| OUT_OF_RANGE | 超出该工具交互/查找/导航范围 | 检查三维距离，分段靠近后重新查询 |
| CHUNK_UNAVAILABLE | 方块所在区块未加载 | 先靠近，不能把未知区块当作空气 |
| UNSAFE_TARGET | 目标是 Bot 脚下支撑方块 | 移动到其他安全站位或改选目标，再检查 |
| NO_LINE_OF_SIGHT | 方块射线受阻，或实体未处于攻击命中视线 | 调整站位并重新确认目标，不能只重复攻击/挖掘 |
| ENTITY_MISSING | 实体失效、已离开、身份变化或超出本次攻击距离 | 重新 find_entity；高度差也影响距离 |
| BLOCK_CHANGED | 当前方块与请求名称不符 | 重新 inspect_block，不能沿用旧假设 |
| TOOL_MISSING / WRONG_TOOL / TOOL_DAMAGED | 工具缺失、不适合采集、耐久不足或不可确认 | 选择背包中真实可用的工具 |
| WRONG_TABLE | `craft_item.position` 不是工作台，或不需工作台的配方却传了 position | 省略 position，或改传真实工作台坐标；放置用 place_block |
| ITEM_ENTITY_MISSING | 地面上没有指定掉落物实体，可能已被自动拾取 | 先 inspect_inventory 核对背包，不重复挖同一位置；精确数量用 gather_resource |
| PROTECTED_BLOCK / FORBIDDEN_TARGET | 当前基础工具拒绝该目标 | 查看挖掘/攻击支持列表，Planning 不能绕过工具规则 |
| OCCUPIED / ENTITY_COLLISION | 放置位置已有方块或可能碰到实体 | 查目标与站位；需要挖掘时使用正确挖掘工具 |
| INVENTORY_FULL / NO_SPACE | 没有可接收物品的槽位或堆叠空间 | 由玩家整理，或明确调用受支持的存放/丢弃工具 |
| MATERIALS_MISSING / INSUFFICIENT_ITEMS | 原料、燃料或来源数量不足 | 查询背包与容器，再准备材料 |
| NO_RECIPE | 当前版本或当前工作台条件下没有可用配方 | 查 inspect_recipe，确认工作台和版本 |
| FURNACE_BUSY | start 检测到炉内已有物品 | 查 open_furnace，不自动追加或清炉 |
| OUTPUT_NOT_READY | 输出名称不匹配或数量不恰好相等 | 查 status，核对整批数量；满栈需要现有接口之外的处理 |
| NAVIGATION_FAILED / 移动失败终态 | 无路、超时、卡住、目标丢失或取消 | 查询原 taskId，修改目标/站位后再决定新任务 |
| TIMEOUT / uncertain | 调用超时或缺少操作效果证据 | 查询原回执与真实状态，不能推断“没发生”并直接重发 |
| STALE_TASK / STALE_INSTANCE / STALE_LEASE | 已停止、生命周期变化、服务重启或租约过期 | 结束旧任务，重新观察现场；旧任务不能自动恢复 |

## 14. 当前还不能承诺的行为

- 自然语言复杂目标自动分解、动态重规划、长期记忆和自主学习。
- 自动寻找所有缺失原料、探索未加载区域、无限采集或持续挖矿。
- 跨熔炉自动调度、取回原料/燃料、输出任意数量分取；已有炉子现在可用子工具同类补充，取产品仍为当前整栈。
- 熔炼时自动安排其他工作、发现敌人自动撤离并在安全后恢复任务。
- 任意生物战斗、持续追击、自动打到死亡、远程武器或完整防御策略。
- 通用道具使用、任意功能方块、任意模组协议/容器、任意 Minecraft 版本兼容。
- 跳跃和放置等多个修改工具同时控制 Bot；当前没有“跳起垫脚”原子动作。
- 跨服务重启恢复任务、持久幂等记录、按附魔/NBT 精确选择同名物品。

这些需要后续扩展工具、调度器或 Planning；应先判断缺的是操作接口、执行协调还是任务决策，不能仅靠提示词声称已经支持。

## 15. 查看实时目录与调用示例

在服务已启动时，PowerShell 查询工具目录：

```powershell
$catalog = Invoke-RestMethod -Uri 'http://127.0.0.1:3000/api/tools'
```

```powershell
$catalog.data.tools | Select-Object name, mutates, description | Format-Table -Wrap
```

单独查看一个工具参数：

```powershell
$catalog.data.tools | Where-Object name -eq 'smelt_item' | ConvertTo-Json -Depth 20
```

直接调用只读背包工具的完整请求（昵称替换为实际调用者）：

```powershell
$request = @{ username = 'Steve'; tool = 'inspect_inventory'; arguments = @{}; requestId = [guid]::NewGuid().ToString('N'); generation = $catalog.data.generation; instanceId = $catalog.data.instanceId } | ConvertTo-Json -Depth 10
```

```powershell
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:3000/api/tools/execute' -ContentType 'application/json' -Body $request | ConvertTo-Json -Depth 20
```

HTTP 外层还有一层 `{success,data,error}`，因此工具自身结果位于外层 data 中。HTTP 200 不保证游戏动作完成，还要检查内层 success、data.status、error 和证据字段。

同一操作重试前保留原 requestId 以便核对；新操作使用新 ID。停止或生命周期变化后重新获取目录代次。不要擅自获取新代次继续已取消的旧任务。后台租约、回执查询和任务提交格式见 [前置接口说明](tasks/task007-preparation.md)。

本文是源码能力指南。工具目录当前共 39 项，具体定义以 GET /api/tools 为准。Task 006.5 阶段 C/D 与熔炼前置子工具的实现、自动化结果和游戏验收清单见 [阶段说明](tasks/task0065-skills.md)。本次没有连接真实 Minecraft 或请求付费 LLM。自动化通过不能替代组合流程的实际游戏测试。
