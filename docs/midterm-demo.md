# MineMate 中期答辩演示方案

> 目标：10 分钟内让老师看到 **Agent 如何根据真实游戏结果选择下一步、如何执行多步任务、如何验证结果、如何记住上下文**。  
> 原则：优先稳定、可复现；不把 Mock/脚本说成真实游戏验收；不展示隐藏推理链，只展示可观测的工具选择、参数、结果和状态。

## 1. 推荐演示组合（可靠性优先）

| 时间 | 演示 | 主要展示 |
| --- | --- | --- |
| 0:00–1:00 | 架构与分工 | Node 管游戏、Python 管 LLM/记忆、HTTP JSON 边界 |
| 1:00–3:30 | 自然语言多工具任务 | LLM 选择工具、Python 校验、Node 执行、真实结果回填 |
| 3:30–6:30 | `gather_resource` Skill | 规划好的多步流程、搜索/接近/挖掘/拾取、独立背包核验 |
| 6:30–8:30 | 跨轮记忆 | 第二条消息引用第一条；事件日志/SQLite |
| 8:30–9:30 | 安全边界 | 拒绝危险操作；不确认不报成功 |
| 9:30–10:00 | 边界与下一步 | Skill 尚未自然语言路由、LLM Planner 下一阶段 |

不建议把“合成 Skill + 熔炼 Skill + 多任务”全塞进 10 分钟：准备成本高、环境依赖多，容易把演示变成环境排错。

## 2. 为什么选这三个主场景

### 2.1 自然语言多工具任务

展示的是**战术循环**：模型每一步只选一个/一组工具，看到真实结果后再决定下一步。

推荐输入：

```text
先看看你的生命值和背包，然后原地跳一下，再告诉我你现在的状态。
```

预期工具链（顺序可能略有不同）：

```text
query_status
inspect_inventory
jump
→ finish_task（模型显式结束）
→ 最终自然语言回复
```

优点：

- 不依赖寻路和地形；
- 10 秒内完成；
- 日志能清楚看到“选择工具 → 执行 → 结果 → 下一步 → finish_task”；
- 失败时也能展示错误回填、重试/换工具/`update_plan`，以及模型用 `finish_task` 结束。

讲解点：这不是模型直接控制游戏，而是“模型选工具、程序校验、Node 执行、真实结果回填”。

### 2.2 `gather_resource` Skill

展示的是**确定性多步规划 + 执行 + 独立验收**：

```text
resolve_resource
→ inspect_mining_sources
→ find_block
→ 必要时 find_interaction_positions / goto_position
→ get_movement_task
→ inspect_block
→ mine_block
→ collect_item（已自动拾取则跳过）
→ 重新读背包 → 最终核验
```

脚本：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-gather-skill.ps1 -Player Steve -Resource oak_log -Count 2
```

演示时展示：

- 先返回 execution_id，再轮询状态；
- 终端显示 stage、当前背包数量、调用数；
- 最终 `status=completed` 之前必须有背包总数核验；
- 执行账本能看到每一步工具调用；
- 失败时保留真实数量/缺口，不虚报成功。

准备：Bot 站在有橡木的区域（16 格搜索范围），背包最好没有或少于目标数量的橡木；否则 Skill 会直接判定已达标而不移动，演示效果变弱。

### 2.3 跨轮记忆：继续未完成的任务

输入：

```text
第一条：
我们等会儿要做一张工作台。先看看背包里有没有 4 块橡木木板，只告诉我够不够，先别合成。

第二条：
继续刚才那件事，材料够的话就做吧。
```

预期：

```text
第一条：inspect_inventory →“有 4 块橡木木板，够做一张工作台，我先没动。”
第二条：craft_item(crafting_table, count=1) →“工作台做好了，在背包里。”
```

这个示例比身份/偏好问答更正式：

- 第一条包含**任务目标、材料门槛、明确约束**（先别合成）；
- 第二条没有重复“工作台/4 块木板/不要放”，模型必须从上下文恢复；
- 展示的是任务状态连续性，不是身份/偏好问答；
- 只依赖 4 块橡木木板，`craft_item` 是 2×2 配方，不需要工作台，成功率高；
- 不需要坐标或复杂地形。

展示证据：

- 第二条 Python 日志中的 `turn=1` 上下文包含第一条的 user/assistant；
- `GET /api/agent/memory/Steve` 能看到事件数、turn 数、摘要状态；
- 若模型在第二条重新 `inspect_inventory`，也可以接受，但最终应正确合成工作台而不是重新询问任务。

可选的空间记忆（风险更高，必须提前彩排）：

```text
第一条：把工作台放在你右边的空地上，放好后记住位置。
第二条：用刚才放下的工作台做一把木镐。
```

它需要材料、工作台交互距离和模型正确使用上一轮 `place_block` 的结果；时间紧时不要用。

### 2.4 安全边界（最后 1 分钟，可选）

输入：

```text
攻击我。
```

预期：模型拒绝攻击玩家；或输入“挖掉你脚下的方块”，展示 `UNSAFE_TARGET` 拒绝。

讲解点：工具有主人权限、修改互斥、危险方块保护；模型说“完成”不算数，真实结果才作数。

## 3. 备选场景（按需替换）

| 场景 | 适合展示 | 前置成本 |
| --- | --- | --- |
| `craft_item` Skill | 查真实配方、找/接近工作台、合成、背包增量核验 | 需要材料和工作台 |
| `smelt_item` Skill | 找炉、按容量投料、燃料/产品守恒核验 | 需要熔炉、矿石、燃料 |
| `/api/agent/tasks` 显式步骤 | 后台串行、租约、账本、`$ref`、终态验证 | 需要手工构造步骤 |
| 自然语言“找树并走过去” | LLM 依据 `find_block` 结果选择导航 | 依赖地形和寻路，失败率较高 |

如果现场网络或模型很慢，建议用**录屏 + 真实日志/执行账本**替代实时调用，不要现场排错。

## 4. 演示前检查清单

1. Minecraft Open to LAN，端口写入 `minecraft-bot/.env` 的 `MC_PORT`。
2. `agent/.env` 配好 `LLM_API_KEY` / `LLM_MODEL`，`TOOL_CALLING_ENABLED=true`，`MEMORY_ENABLED=true`。
3. 重启 Node/Python，确保加载最新代码（`find_block` 的 visible/diggable、回复风格、Skill）。
4. Bot 已出生、存活；玩家在 `OWNER_USERNAME` 内（如果配置）。
5. 演示前重置记忆：

```powershell
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/agent/memory/Steve/reset'
```

6. 准备一片有橡木的区域；Bot 背包清空或少于 2 个橡木。
7. 打开三个窗口/区域：
   - Minecraft 游戏画面；
   - Node 终端（`[Tools]`、`[Movement]`）；
   - Python 终端（`[Agent]`、`[Tools]`、`[Memory]`）。
8. 提前完整跑一遍；把成功日志、Skill 执行 JSON、记忆查询结果保留为 fallback。
9. 注意 Node 默认 `AGENT_TIMEOUT_MS=150s`；复杂任务可能接近 Python `TASK_TIMEOUT`，演示前确认两端超时配置一致。采集 Skill 走 Python 后台接口，不受聊天超时限制。

## 5. 讲解话术要点

- **推理**：不展示 chain-of-thought；展示“上一步真实结果 → 下一步工具选择”的可观测轨迹。
- **规划**：自然语言是战术循环；`gather_resource` 是确定性多步计划。当前还没有 LLM Planner，下一阶段接 `/api/agent/tasks`。
- **执行**：所有游戏动作都经 Node Tool Registry；Python 不直接操作 Minecraft。
- **验证**：Skill 的 `completed` 必须通过独立快照/背包核验；`pending/uncertain` 不会报成功。
- **记忆**：消息历史从事件日志派生；上下文超限时压缩旧轮次，SQLite 重启可继续。
- **边界**：Skill 目前不能从自然语言自动选择；这是下一阶段工作，不要在现场说“已经全自动规划”。

## 6. 一句话收尾

> MineMate 已经跑通“自然语言 → 工具选择 → 真实执行 → 结果回填 → 记忆”的 Agent 主链路；下一步是把确定性 Skill 和 Planner 接进自然语言入口，让复杂任务从“能演示”变成“能稳定完成”。
