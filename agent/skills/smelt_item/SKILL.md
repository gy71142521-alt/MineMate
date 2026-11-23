---
name: smelt-item
description: 查询配方并寻找兼容普通熔炉，精确接近后按容量投入原料和燃料，核验实际投放即结束；不等待熔炼、不取产品、不自动补燃料。
---

# smelt_item

## 职责与入口

Python/API 名称仍为 `smelt_item`，说明文件名称为 `smelt-item`。通过 Skill HTTP API 或测试脚本调用；当前自然语言仍走 Task 006 工具调用，Skill 自动路由留待阶段 F。

此 Skill 只启动熔炼：找炉、接近、检查、投入原料与燃料、核验转移、关闭窗口并释放控制权。`completed` 仅指投料目标完成，`completion_scope=furnace_loading`；`smelting_completed=null` 表示未验证熔炼完成。没有后台等待、自动收取或后续补燃料，产品留在炉内。尚未实现收集产品 Skill。

`metadata.py` 是运行时说明和参数的唯一来源，`skill.py` 实现确定性执行与核验，`SOP.md` 说明实际步骤；说明文件暂不加载到 LLM。

## 参数

| 参数 | 含义 |
| --- | --- |
| item_name | 原料名或显示名，经 Node resolve_resource 确认，如 raw_iron |
| count | 默认 1，整数 1～64，本次新增原料总数；不擅自超出指定数量 |
| fuel_name | 可选指定燃料；空槽默认优先煤炭，没有则足量已支持燃料，非空槽只能同类补充 |
| search_radius | 默认 16，范围 1～16，任务起点周围三维搜索半径 |
| furnace_position | 可选整数 {x,y,z}，指定后只使用该炉 |
| use_existing | 默认 false；true 允许同工艺续接，不取出已有产品，输出满槽时跳过 |
| wait_timeout | 已弃用，仅兼容旧请求的有效参数，当前不生效、不等待加工 |

请求的总执行超时、调用预算和取消机制继续生效；原总预算默认 1200 秒、最多 1800 秒，调用默认 192 次，这些上限不意味着投料后会继续等待。

## 命令 → 工具顺序 → 验证

给 Bot 64 个粗铁和 8 个煤炭，在 16 格内放置空普通熔炉，重启服务，在根目录分别执行：

```powershell
Set-Location 'D:\桌面\MineMate'
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-smelt-skill.ps1 -Player Steve -ItemName raw_iron -Count 64
```

Steve 换为实际请求玩家名。需要续接同工艺炉子时添加 `-UseExisting`；可选 `-FuelName charcoal`。脚本保留旧 `-WaitTimeout` 参数但忽略它。

工具顺序：`resolve_resource` → 快照 → `inspect_smelting_recipe` → `find_block(furnace)` → 必要时 `find_interaction_positions/goto_position(arrival_range=0)/get_movement_task` → `inspect_block/open_furnace` → 炉况、材料与配方复查 → 每炉一次 `furnace_add_input` → 必要时 `furnace_add_fuel` → 一次 `open_furnace` 确认投料 → 剩余量使用下一座兼容炉 → 最终快照核验 → 返回并释放租约。

不调用 `furnace_take_output`、旧 `smelt_item(start)`，不轮询加工进度。导航仍按原 taskId 有界等待，不能把尚未到达当成已可交互。

验收：原料实际从背包减少并进入炉子，燃料按计划实际投放或使用已有燃料，炉内原料与产品的折算总量守恒，维度一致。游戏会自然加工，核验允许输入减少而输出增加；产品不进入 Bot 背包。投料成功后应能立即接受新的移动任务。

## 选炉与燃料边界

- 每炉投入 min(本次剩余数量, 64-炉内已有原料)，优先填当前兼容炉；装满后仅把未投部分交给下一座炉，最多 8 炉。炉内已有 10、本次新增 64 时，先投 54，再给另一炉投 10。
- 默认跳过有原料或产品的炉；`use_existing=true` 才允许同工艺续接。不同工艺、原料满槽、输出满 64 都跳过，不清炉或自动取出。
- 总可用容量不足返回 INSUFFICIENT_FURNACE_CAPACITY，并保留 input_submitted_count、remaining_input、逐炉记录和真实工具账本；部分投料不标为成功。
- 燃料只使用 Node 配方返回的规则。先扣除炉内已有数量，预留本次原料；弱燃料总需求可能超过一栈，首次最多放 64，未放部分以 fuel_pending_count 和每炉 fuel_reserved 记录。只在本次多炉分配中避免重复承诺，不构成任务结束后的背包锁定。
- requires_fuel_followup=true 表示之后仍须加燃料或检查余量；已观察到燃烧但无法确认剩余容量时，fuel_status=remaining_capacity_unknown。不声称当前燃料能完成全部加工，也不自动回来补料。
- 每炉原料只提交一次，未知结果或响应丢失复用原回执，不重投。取消、超时、断线或换维度会终止后续调用，已投材料留炉内，不回滚或跨重启恢复。

结果中 furnaces 记录位置、既有原料/产品、实际新增量、燃料计划及已投量、待补量、before_furnace/loaded_furnace；执行 ID 可查询本次投料记录。兼容的 output_taken、actual_increase、polls、fuel_refills 均为 0，不表示检测到整个加工完成。

只支持现有普通熔炉适配器的配方和燃料，不采矿、造炉、引入 Planning/Memory 或注册收集 Skill。真实验收应覆盖空炉投料、同工艺续接、输出满槽跳过、多炉分配、弱燃料待补提示、停止/断线，以及投料结束后正常移动。Mock/HTTP 测试不能替代 Minecraft 验收。
