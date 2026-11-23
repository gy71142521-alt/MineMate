# smelt_item 投料作业流程

## 1. 检查

1. 校验 Skill 参数，复用 TaskManager 执行槽、租约、心跳、超时及取消；总量 1～64。
2. 读取快照，确认存活、维度和背包可读；原料及燃料由 resolve_resource 解析，歧义不猜测。
3. inspect_smelting_recipe 读取 Node 配方、产品和燃料需求；记录初始背包，缺本次原料时在修改前失败。

## 2. 找炉与接近

1. 指定位置只检查该炉；否则在起点三维半径内 find_block，最多 8 炉，按距离排序去重。
2. 保留实际 4.5 格交互距离、名称和视线检查；必要时寻找站位，最多尝试 3 个，goto_position(arrival_range=0)，pending 等待原 taskId。
3. open_furnace 读取槽位，不同工艺跳过；默认跳过已有原料/产品，use_existing=true 允许兼容续接。
4. 原料满槽或输出满 64 时跳过。不得为腾空间而取出任何产品。
5. 按 min(剩余本次数量, 64-已有原料) 计算当前炉新增量，通过 Node 查询该炉总队列燃料需求。检查真实背包，空槽优先煤炭，非空槽只同类补充，并预留尚未投炉的可燃原料。

## 3. 投料并确认

1. 修改前重读炉况、配方和背包；变化时停止，不能使用旧判断。
2. 记录 before_furnace，每炉 furnace_add_input 只提交一次；确认后累计 input_submitted_count、减少 remaining_input。
3. 必要时 furnace_add_fuel，数量不超过单槽 64。记录实际 fuel_added，剩余需求记为 fuel_reserved/fuel_pending_count。多炉分配期间预留前炉待补量，避免重复承诺；结束后不锁定背包。
4. 已在燃烧但剩余容量未知时允许沿用，明确 requires_fuel_followup，不能假装已备足整批燃料。
5. 仅一次 open_furnace 记录 loaded_furnace。可出现自然加工导致原料减少/产品增加，不能据此等待或取出。
6. 当前炉装满且仍有未投原料则找下一炉，排除已投炉。无更多可用炉时返回 INSUFFICIENT_FURNACE_CAPACITY，保留真实部分进度和现场。

## 4. 核验并结束

| 条件 | 证据 |
| --- | --- |
| input_deposited | 本次全部原料提交已确认，remaining_input=0，初始/最终背包原料实际减少；同物品兼作燃料时额外计算燃料消耗 |
| fuel_deposited | 每炉已投燃料与本次计划一致，最终背包对应燃料真实减少；允许使用已存在的燃料/已观察的燃烧 |
| furnace_load_confirmed | 每炉 before_furnace 与 loaded_furnace 的原料/产品按 Node 配方折算守恒，增量等于实际新增投料；工艺一致 |
| same_dimension | 初始与最终真实快照维度相同 |

全部通过才 completed，completion_scope=furnace_loading，stage=loaded。smelting_completed=null，产品不入包；不等待加工、不轮询、不自动续补、不取旧/新产品。释放租约后其它任务可使用 Bot。尚未实现收集 Skill。

任一步骤 uncertain、取消、超时、死亡、断线或换维度时停止后续调用，关闭窗口由原 Node 工具处理；已投原料/燃料不回滚，未知结果不重发。不创建脱离本次执行生命周期的后台监视器。

测试命令见 [SKILL.md](SKILL.md)。验收重点是投料数量、产品保留、无等待、结束后可移动；测试仍需真实 Minecraft 验证。
