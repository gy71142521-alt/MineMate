# craft_item 标准作业流程

## 1. 受理与初始状态

1. Skill Registry 严格校验参数，复用已有 TaskManager 串行执行槽、租约、心跳和回执。
2. `/api/world` 读取真实坐标、维度和背包，未知背包不能视为空背包。
3. `resolve_resource(kind=item)` 取得真实物品名；未知/歧义返回 RESOURCE_UNKNOWN/RESOURCE_AMBIGUOUS 并保留候选。
4. 记录初始数量。count 表示新增量，不扣除已存在产物。

## 2. 检查材料与配方

1. `inspect_recipe(item_name,count,max_results=16)` 使用 Node 当前版本配方，Python 不维护材料表。
2. 检查结构、输出、操作数、材料缺口及 delta 中的目标净增量；不能把同时消耗目标物品的毛产量当成新增量。
3. 没有配方返回 NO_RECIPE；材料不足返回 MATERIALS_MISSING 和最多 4 种备选缺口；配方不支持新增量时返回 UNSUPPORTED_RECIPE。此时不调用移动或合成。
4. 可行配方优先选择无需工作台的一种。只用背包合成则跳过设施步骤。

## 3. 查找和接近工作台

1. 如果指定 table_position，则只检查指定坐标。否则 `find_block(crafting_table,radius=search_radius,max_results=8)`。
2. 只考虑任务起点三维半径内的台子；去重并按距离排序。未找到不能说整个世界没有工作台。
3. 近处先 `inspect_block`，实际名称、交互距离和可见性均满足才可使用。
4. 需要靠近时调用 `find_interaction_positions(radius=3,max_results=6)`，最多尝试 3 个站位。候选站位限制在起点半径加 3 格内，不表示路径一定可达。
5. `goto_position(wait_seconds=0,arrival_range=0)` 要求进入选定脚部方块，避免默认两格到达误差叠加到交互距离。pending 只通过原 taskId 的 `get_movement_task` 有界等待，最多 75 秒。不重复启动相同导航。
6. 到达后重新确认同一台子的名称、实际距离和视线；确定性导航失败可以换站位/台子，未知结果、取消、断线立即结束。最后一次检查保存在 workbench_check，失败时终端打印原因、距离、方块名和坐标，避免只看到汇总错误码。

## 4. 合成前复查与一次提交

1. 再读快照并检查维度，再次 `inspect_recipe` 确认材料未在移动期间变化。
2. 需要工作台时再 `inspect_block`，不把早先的到达结果当作设施仍存在的证据。
3. `craft_item(item_name,count,position?)` 一次提交。窗口由现有 Node 合成工具管理，不另外 open_workbench 再合成。
4. Node 执行真实配方、材料、窗口、超时/取消检查；工具未知结果不重新合成，响应丢失只按原 requestId 查询回执。
5. 多产物配方按整批取整，重新读背包记录实际增量；不依赖 tool_result 的文字宣告。

## 5. 独立验证和交付

SkillManager 最后再次读取世界快照，验证：

| 条件 | 核验证据 |
| --- | --- |
| inventory_increase_reached | 最终目标物品总量减执行前总量 ≥ count；以管理器初始和 Skill 初始两者较高的基准保守核验 |
| same_dimension | 初始、执行记录、最终维度一致 |

全部成立才 completed。失败保留 stage、材料缺口/设施尝试和真实工具账本。取消不会回滚已消耗材料和已完成移动，不自动恢复旧任务。

游戏验收按 [SKILL.md](SKILL.md) 命令分别测试木板、工作台、铁头盔，并测试缺材料、工作台被拆、停止、满背包和原有功能回归。报告采用“命令 → 工具顺序 → 真实背包增量”的格式。真实 Minecraft 验收仍需执行，Mock 不替代它。
