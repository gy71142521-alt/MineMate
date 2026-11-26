# gather_resource 标准作业流程

## 1. 受理与确认缺口

1. 注册表校验 Skill 名称和参数，复用共享执行器取得 Node 租约，心跳持续维护。
2. 通过 `/api/world` 读取 Bot 位置、维度、背包；不可用背包不能当成零物品。
3. `resolve_resource(kind=item)` 解析 resource 为真实物品名。未知/歧义返回 RESOURCE_UNKNOWN/RESOURCE_AMBIGUOUS，保留候选供澄清。
4. 记录初始数量，计算 max(0, count - 当前数量)。`count` 允许 1～256，是背包目标总数；已达标直接进入最终验证，不查询来源、不挖掘。

## 2. 确认来源与工具

1. 指定 block_name 时先 `resolve_resource(kind=block)`；随后 `inspect_mining_sources` 查询 item_name 的可能来源，可限定 block_name。
2. 来源仅取当前 Node 注册表 drops 数据，不假定物品与方块同名。没有掉落数据返回 MINING_SOURCE_UNKNOWN，已知数据不匹配返回 MINING_SOURCE_MISMATCH。
3. 最多考虑前 4 种来源，截断必须标记；需要具体来源时由调用方明确指定。
4. 指定 tool_name 时再 `resolve_resource(kind=item)` 确认，交由 mine_block 检查存在性、耐久与适用性。

## 3. 有限搜索与安全接近

1. 每轮先读背包，达标立即结束。
2. 对每种来源调用 `find_block`，最多 16 格、每种最多返回 16 个候选；按实际三维距离排序，并限制在当前采集位置的球形范围。每次确认拾取后，下一轮以 Bot 新位置重新划定范围。
3. 选最近、未尝试过的坐标。记录尝试次数，脚下支撑直接跳过，不能重复锁定该坐标。
4. 足够近时 `inspect_block`；名称、可见性和可挖标记有效才继续。
5. 需要接近时 `find_interaction_positions` 查询站位，最多尝试 3 个。导航用 `goto_position(wait_seconds=0)`，pending 只通过 `get_movement_task` 等待原 taskId，单次等待最多 75 秒。
6. 每次到达后重新查询脚下支撑和 `inspect_block`。目标不可达或视线不通时可调用 `clear_path`，最多消耗 Skill 清障预算；清理后重新走站位和目标检查。保护方块/未知方块不会被清除，预算耗尽则跳过。

## 4. 单次挖掘

1. 重新读取背包数量，记录本次挖掘前数量；此时若已达标就不挖。
2. `mine_block(block_name, position, tool_name?)`；未指定工具时 Node 优先从运行时方块工具定义和挖掘速度中选择背包内的合适工具；没有合适工具才尝试空手，并执行采集条件、保护方块、距离、视线、耐久和脚下支撑检查。
3. UNSAFE_TARGET/NO_LINE_OF_SIGHT/OUT_OF_RANGE/BLOCK_CHANGED/NOT_DIGGABLE/CHUNK_UNAVAILABLE/PROTECTED_BLOCK 允许跳过该坐标；缺工具、错误工具、耐久不足等真实失败停止并保留原错误码。
4. 只有 blockRemoved=true 才进入拾取阶段；未确认移除或工具结果 uncertain 时立即结束，不重发挖掘。

## 5. 拾取与数量复查

1. 先读背包。如果期望物品已增加，视为已自动拾取，不重复 collect_item。
2. 读取 `nearby_entities` 找同名掉落物的位置；存在时先用有限 `clear_path` 检查/清理直线路径，再调用 `collect_item(item_name, entity_id, radius=8)`。无实体感知数据时按名称拾取。
3. 仅 ITEM_ENTITY_MISSING 允许等待 0.25 秒后重查，最多 5 次；每次先读背包，覆盖被动拾取与实体更新同时发生的情况。
4. collect_item 报成功后仍读背包。没有真实增加则 PICKUP_NOT_CONFIRMED/uncertain，停止后续破坏。实体消失也不能当作成功。
5. INVENTORY_FULL、超时、取消、断线、未知结果等立即停止，报告已挖数量和现场进度；已经投入的动作不回滚。掉落物不可达且清障预算用完时，不继续挖新方块。
6. 数量不足则重新从第 3 步搜索，直到达标、来源耗尽或达到 max_blocks/工具调用/时间上限。max_blocks 包括失败/跳过的不同方块尝试，不允许无限循环。

## 6. 独立最终验证与报告

SkillManager 再读取最新世界快照，verify 不依据工具的成功文字判断。

| 条件 | 核验证据 |
| --- | --- |
| inventory_target_reached | 最新快照中目标物品汇总数量 ≥ 请求的 count |
| same_dimension | 初始快照、执行记录和最终快照维度相同 |

全部通过才 completed。资源不足保留已获得数量及 remaining，不虚构完成；阶段结束释放当前租约。重复同玩家/request_id 返回原记录，响应丢失查询原工具回执，禁止盲目重发副作用。

报告按“命令 → 实际工具顺序 → 背包最终数量和验证结果”展示。命令与示例见 [SKILL.md](SKILL.md)。本阶段没有全局 Planning、LLM Skill 选择、自动合成或熔炼。
