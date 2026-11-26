---
name: gather-resource
description: 需要采集指定物品时使用，组合有限搜索、接近、挖掘与拾取，让背包中的目标总数达标；不负责合成、熔炼或全局探索。
---

# gather_resource

## 用途与入口

采集指定物品至背包目标总数，例如橡木原木、沙子、圆石或粗铁。`count` 包括已有物品：已有 2 个、目标为 4 时，只需再获得 2 个。已达标时不移动、不挖掘。单次目标总数最多 256 个，但实际能否达到仍受搜索候选、背包空间、工具耐久和调用预算限制。

技能目录使用 Python 包名 `gather_resource`；本文档标识为 `gather-resource`。通过 `POST /api/agent/skills/execute` 提交，运行时名称仍为 `gather_resource`。当前不由自然语言聊天自动选择，也不调用 LLM。

| 文件 | 职责 |
| --- | --- |
| metadata.py | 名称、描述、严格参数、前置条件、完成条件及依赖工具的唯一代码来源 |
| skill.py | 确定性采集循环和独立核验 |
| SOP.md | 工具顺序、分支、失败处理和验收 |
| __init__.py | 公开导出 |

元数据由 Skill Registry 导出，不复制 Node Tool Schema。说明文档目前用于阅读，尚未作为 LLM 提示加载。暂不添加辅助资源。

## 参数与前置条件

Bot 已连接、已出生、存活，维度和背包可读；使用实际玩家昵称提交，遵守 OWNER_USERNAME。采集仍由 Node 工具检查工具耐久、采集条件、距离和视线；需要镐时请事先把合适的镐交给 Bot。

| 参数 | 含义 |
| --- | --- |
| resource | 必填，要进入背包的物品名或运行时显示名，例如 oak_log、cobblestone、raw_iron |
| count | 必填，整数 1～256，背包目标总数 |
| block_name | 可选，限定要挖的来源方块，仍须通过 Node 注册表掉落候选校验 |
| tool_name | 可选，指定背包中要使用的工具；不指定时优先使用合适工具，没有工具且空手可采集才使用空手 |
| search_radius | 默认 16，整数 1～16，以 Bot 当前采集位置为球心、每轮重新划定的三维目标范围 |
| max_blocks | 默认 16，整数 1～64，最多尝试的不同方块数，包含跳过/失败候选 |
| allow_clearing | 默认 true；允许有限清理 Bot 到采集目标/掉落实体的路径方块 |
| max_clear_blocks | 默认 4，整数 1～8；整个 Skill 共用的清障预算 |

名称先经 `resolve_resource` 确认。来源由只读 `inspect_mining_sources` 从当前运行时 drops 数据获取，支持 stone→cobblestone、iron_ore→raw_iron，不在 Python 枚举别名或掉落表。最多考虑 4 种来源方块；源数据不明确时不猜测挖掘。

## 命令 → 工具顺序 → 成功验证

先重启原 Node/Python 服务，在项目根目录执行，将 Steve 换成实际游戏昵称。

背包凑齐 4 个橡木原木：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-gather-skill.ps1 -Player Steve -Resource oak_log -Count 4
```

`resolve_resource(item)` → 读背包计算缺口 → `inspect_mining_sources` → `find_block` → 必要时 `find_interaction_positions` → `goto_position` / `get_movement_task` → `inspect_block` → `mine_block` → 检查是否已自动拾取 → 必要时 `collect_item` → 重新读背包。未达标则有限重复，达标后独立核验。

采集 3 个沙子：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-gather-skill.ps1 -Player Steve -Resource sand -Count 3
```

工具顺序同上，目标换成 sand。脚下沙子会被跳过，改找其它候选，不关闭原有挖掘安全检查。

从石头获得圆石，指定镐：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-gather-skill.ps1 -Player Steve -Resource cobblestone -BlockName stone -ToolName iron_pickaxe -Count 3
```

额外 `resolve_resource(block/item)` 确认来源与工具。`mine_block` 挖 stone，`collect_item` 拾取 cobblestone，成功条件检查圆石数量。

采集 3 个粗铁：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-gather-skill.ps1 -Player Steve -Resource raw_iron -Count 3
```

Node 自动给出铁矿石/深层铁矿石等可能来源，只搜索已加载范围，矿石不可达或没有镐时明确失败，不自动挖隧道或合成工具。

不可达/视线遮挡时会用 `clear_path` 沿 Bot 到明确目标的有限直线清理少量天然方块，每次后重新检查；挖掘后拾取失败会先检查真实掉落实体位置，预算允许时先清障再拾取。此工具不会启用全局自动挖掘，也不清除液体、脚下支撑、矿石、功能方块或未知方块。

以上流程通过 `/api/world` 读取真实快照；不会额外调用同名快照 Tool，也不会另开 Skill 执行槽。成功必须满足最终背包数量 ≥ count，且维度未变化；仅方块消失、实体消失或 Tool 返回成功均不够。

## 进度、失败与边界

测试脚本显示 stage、背包数量、已挖方块数和调用数，结束时输出执行 ID、数据、账本和核验证据。`data` 包含 initial_count、current_count、collected（相对初始背包的净增加）、remaining、sources、attempt_count、blocks_mined 和最近 16 条 attempts；全部调用仍在 steps 中。

- 没有资源或资源不足：返回 RESOURCE_NOT_FOUND/RESOURCE_INSUFFICIENT，保留真实数量和缺口。max_blocks 是尝试预算，不是目标一定能完成的保证。
- 掉落物可能延迟生成：只有 ITEM_ENTITY_MISSING 可最多重查 5 次，间隔 0.25 秒。已自动入包则跳过 collect_item；拾取结果不确定后停止，不继续破坏其它方块。
- 背包满：collect_item 返回 INVENTORY_FULL 后结束。当前没有挖掘前的完整容量预检，已挖的掉落物可能留在现场，不会为腾空间自动丢弃物品。
- 注册表 drops 只是可能来源，附魔/模组/数据包可能改变掉落。未知规则停止并反馈，精准采集等造成预期物品没有入包时不宣称成功。
- 不全世界扫描、不挖墙、不自动探索路点、不攻击、不放置；每轮目标范围最大 16 格，采集并移动后会以当前位置重新划定范围，因此仍受有限候选和预算约束。
- 来源或方块候选经过截断时通过 sources_truncated/candidates_truncated 标记，coverage_complete=false，不声称已搜遍范围。
- 使用同一后台执行器的租约、回执、超时、取消和去重；Ctrl+C 尽力取消本次脚本，游戏「停止」仍可抢占。已发生的挖掘和移动不回滚。

详细流程见 [SOP.md](SOP.md)，游戏验收见 [阶段说明](../../../docs/tasks/task0065-skills.md#阶段-cgather_resource)。自动化测试不等于真实 Minecraft 验收。
