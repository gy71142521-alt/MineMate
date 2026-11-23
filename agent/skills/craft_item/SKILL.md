---
name: craft-item
description: 根据当前版本真实配方和背包材料合成指定物品，自动判断工作台需求、搜索与接近工作台，核验真实新增产物；不自动采集或递归合成材料。
---

# craft_item

## 用途与入口

第三个业务 Skill。说明文件标识为 `craft-item`，Python/HTTP 名称为 `craft_item`。通过 `POST /api/agent/skills/execute` 或测试脚本调用；与同名 Node 原子工具分属不同注册表。

`metadata.py` 定义运行时元数据和严格参数；`skill.py` 实现确定性流程；`SOP.md` 记录执行顺序与失败处理；`__init__.py` 提供导出。SKILL.md/SOP.md 当前用于阅读，不自动加载到 LLM；不添加辅助资源或依赖。

## 参数与条件

| 参数 | 含义 |
| --- | --- |
| item_name | 必填，目标物品名称或运行时显示名；先经 resolve_resource(kind=item) 解析 |
| count | 默认 1，整数 1～16，本次希望新增的产物数，不扣除已有产物；与 Node 合成工具上限一致 |
| search_radius | 默认 16，整数 1～16，从任务起点搜索工作台的三维半径 |
| table_position | 可选，工作台整数坐标对象 {x,y,z}；指定后只检查此工作台，仍须在搜索范围内 |

Bot 须已连接、出生、存活且背包可读，提交玩家须符合 OWNER_USERNAME。合成材料必须已经在 Bot 背包内。需要工作台时，范围内必须有可达、可见的真实工作台；背包里有工作台物品不等于已经放置。

`count` 表示本次新增量：背包已有 1 个工作台，再请求 1 个，应最终至少有 2 个。请求 5 个木板可能按两批实际得到 8 个，不虚报精确数量。

## 命令 → 工具顺序 → 验证

先启动/重启 Node 与 Python，确认 Bot 进入游戏。在项目根目录执行，Steve 换成你的玩家名。

准备至少 4 个橡木木板，合成 1 个工作台（不需要已有工作台）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-craft-skill.ps1 -Player Steve -ItemName crafting_table -Count 1
```

`resolve_resource` → 快照/初始数量 → `inspect_recipe` 检查材料和工作台需求 → 合成前重新读快照/`inspect_recipe` → `craft_item` → 最终快照核验背包增量。

准备 5 个铁锭，在 16 格内放置工作台，合成铁头盔：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-craft-skill.ps1 -Player Steve -ItemName iron_helmet -Count 1
```

`resolve_resource` → 快照 → `inspect_recipe` → `find_block(crafting_table)` → 近处直接 `inspect_block`；远处调用 `find_interaction_positions` → `goto_position` / `get_movement_task` → `inspect_block` 复查工作台/距离/视线 → 重新查询配方与材料 → `craft_item(position=工作台)` → 背包增量核验。Skill 自动判断台子需求，不需要在自然语言里补“使用工作台”。当前自然语言尚未自动选择 Skill，请用脚本测试。

准备 2 个橡木原木，测试整批产量（期望新增 5，通常实际新增 8）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-craft-skill.ps1 -Player Steve -ItemName oak_planks -Count 5
```

指定工作台坐标可在 PowerShell 内直接调用脚本：

```powershell
& .\scripts\test-craft-skill.ps1 -Player Steve -ItemName iron_helmet -TablePosition 10,64,0
```

只有最新背包中的目标物品相对执行前实际增加至少 count，且维度一致，才 completed。单靠 Node 工具返回 completed 或文本描述不能通过验证。

## 结果与边界

- 脚本显示 stage、背包数量、实际增量和调用数，结束输出完整结果与账本。data 包含初始/当前数量、requested_increase、actual_increase、配方、材料缺口、工作台位置/尝试和 tool_result。
- 材料不足返回 MATERIALS_MISSING，data.material_shortages 给出最多 4 种候选配方的缺口（候选之间是替代关系，不能相加）；配方查询经过截断时标记 recipes_truncated。没有配方为 NO_RECIPE。
- 无工作台为 WORKBENCH_NOT_FOUND；不可达、被拆除或不可见为 WORKBENCH_UNREACHABLE_OR_CHANGED。每次最多考虑 8 个台子，每个台子最多 3 个站位，使用 arrival_range=0 精确进入选定脚部方块，单次导航等待 75 秒；没有自动放台子。
- 失败时查看 data.workbench_check 和终端的“工作台最后检查”：包含实际交互距离（最大 4.5）、Bot/工作台坐标、实际方块名、visible 和 reason。OUT_OF_RANGE 为距离不足、BLOCK_CHANGED 为方块变化、NO_LINE_OF_SIGHT 为视线不通；不是单靠总错误码判断地形有问题。
- 全流程共用原执行器/租约/取消/超时和幂等回执，只提交一次合成工具。结果不确定时不重发合成。已消耗材料不会回滚。
- 不采集材料、不递归制作中间产物、不学习配方，不开发全局 Planning；例如背包只有原木却请求工作台，仍先返回缺木板，需要你先调用木板合成。
- Node 工具根据实际背包重新选择可用配方，Skill 中 recipe 是预检依据，不锁定配方 ID。特殊配方/元数据、模组数据不完整或无正向净产量的回收配方可能不支持；最终以实际背包为准。
- 未增加新的容量管理：满背包时由现有工具/API 拒绝或返回不确定结果，不自动丢弃/整理物品。不会绕过已打开窗口、连接或主人限制。

详细作业流程见 [SOP.md](SOP.md)。自动化与 HTTP 模拟通过不等于 Minecraft 游戏验收。
