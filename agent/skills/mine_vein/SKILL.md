---
name: mine-vein
description: 有限矿脉采集，必要时开两格高的通道，沿新暴露的六面邻接矿石采集并核验背包数量。
---

# mine_vein

Python/API 名为 `mine_vein`。通过 `execute_skill` 自然语言调用或
`POST /api/agent/skills/execute` 提交；执行程序是确定性的，不在每个步骤调用 LLM。

| 文件 | 用途 |
| --- | --- |
| metadata.py | 元数据、严格参数和依赖工具 |
| skill.py | 矿脉前沿、通道推进、拾取与独立核验 |
| SOP.md | 工具调用顺序与异常处理 |

## 参数

| 参数 | 含义 |
| --- | --- |
| resource | 必填，期望进入背包的矿物，如 raw_iron、coal、diamond；通过运行时资源/掉落查询解析 |
| count | 必填，背包目标总数 1～256，包含已有数量 |
| seed_position | 可选，已发现且暴露的矿石整数坐标 {x,y,z}；不指定时只选择当前可见候选 |
| block_name / tool_name | 可选，限定来源矿石/指定矿石采集工具；不指定时由 Node 校验真实掉落和可用工具 |
| search_radius | 默认16、1～16，初始矿石搜索半径 |
| vein_radius | 默认8、1～8，相对首块矿石的三维半径，仅沿六面邻接扩展 |
| max_blocks | 默认16、1～32，矿石尝试上限；不包括通道障碍 |
| allow_clearing | 默认 true，允许有限通道清障 |
| max_clear_blocks | 默认16、0～32，采矿与拾取共同消耗的清障总数 |
| max_route_steps | 默认64、1～128，通道诊断轮数上限 |

支持运行时存在且名称以 `_ore` 结尾的矿石及 ancient_debris；未知模组命名不猜测。
单个矿脉不足时报告 `VEIN_INSUFFICIENT` 和实际数量，不自动转去挖无关矿脉。
达到 count 会提前停止，不能把它描述成“整脉已挖完”。

## 命令 → 工具顺序 → 验证

重启 Node 和 Python，准备一把能采目标矿石的镐，在附近放置有暴露面的铁矿脉。

自然语言：`采集这片铁矿，让背包有3个粗铁`。

直接测试（项目根目录，玩家名换成实际昵称）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-mine-vein.ps1 -Player Steve -Resource raw_iron -Count 3
```

明确首块矿石时加 `-SeedX 10 -SeedY 64 -SeedZ 8`。

`resolve_resource` → `inspect_mining_sources` → `find_block`/`inspect_vein`
→ `inspect_block`/`inspect_mining_route`
→ 必要时 `mine_route_block` 或 `goto_position(arrival_range=0)`/`get_movement_task`
→ 重新诊断 → `mine_vein_block`
→ 背包核对/`nearby_entities` → 掉落物通道诊断/清障/移动 → `collect_item`
→ 背包增量核对 → `inspect_vein` 发现新暴露相邻矿石 → 有限重复。

成功必须独立验证背包目标、同维度和已挖矿石六面连通性；方块消失和掉落实体消失都不是拾取凭证。

## 边界与游戏验收

- 不挖空包围盒，不扫描隐藏整脉，不搭方块，不修改 pathfinder 的 canDig。
- 通道按距离/挖掘成本选择现有通路或一人宽两格高的通道；只允许常见天然岩土。
- 拒绝液体、坠落方块风险、未知区块、功能方块、其它矿石及脚下支撑。上下层只使用已有可靠支撑的台阶；不会向脚下打竖井。
- 无法仅凭方块类型区分天然石头与玩家放置的石头：请在允许开挖的测试矿区验收。没有区域所有权/建筑识别系统。
- 不把一次路径搜索失败视为全世界不可达；报告 `NO_SAFE_ROUTE`/`SEARCH_LIMIT`，受限512个节点、局部半径及上下4格。
- 掉落物移动时逐步刷新；原实体消失可在最后位置3格内重找合并实体，最多5轮。未确认背包增长则停止后续挖掘。
- 清障不回填；停止、超时、断线不回滚已发生动作。大矿脉受总时间和调用预算约束；默认 planned TASK_MAX_TOOL_CALLS=32 可能只够少量矿石，测试脚本预算192。

游戏里分别验证裸露矿脉、石墙后有暴露面的已知矿石、通道受阻掉落物、无镐、液体邻接、预算不足和执行中“停止”，再回归原有聊天/移动/合成。自动化测试不代替游戏验收。
