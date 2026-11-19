---
name: explore-and-find
description: 搜索指定方块或实体类型，可选择有限探索和接近目标，返回实际目标与验证结果。
---

# explore_and_find

## 适用目标

- 查找附近的指定方块，例如 `oak_log`、`iron_ore`。
- 查找指定实体类型，例如 `cow`、`zombie`。
- 在限定范围内尝试探索路点，或找到目标后接近。

仅完成发现和接近，不包含采集、拾取、攻击、放置或持续跟随。实体目标表示类型，不支持直接以玩家用户名作为目标。

## 文件与元数据

| 文件 | 职责 |
| --- | --- |
| `metadata.py` | 名称、描述、参数模型、前置条件、完成条件和依赖工具的唯一代码来源 |
| `skill.py` | 确定性执行与独立结果核验 |
| `SOP.md` | 工具顺序、分支、失败处理和验收标准 |
| `__init__.py` | 对外导出，保持原导入路径兼容 |

注册仍由 `agent/skills/catalog.py` 统一完成。`GET /api/agent/skills` 从实际注册对象生成参数 JSON Schema，不另行手写 JSON/YAML Schema。本说明文件用于阅读，目前不由运行时加载，也尚未发送给 LLM；后续接入时仍由程序校验调用。

文档标识为 `explore-and-find`，遵守 SKILL.md 名称仅含小写字母、数字和连字符的格式。Python 包名和 HTTP 请求中的运行时名称为 `explore_and_find`，继续使用原接口。

## 输入与前置条件

Bot 必须已出生、存活且维度和坐标可用，所需 Node 工具必须已注册。使用真实游戏昵称提交请求；配置 OWNER_USERNAME 时遵守现有主人权限。

| 参数 | 含义与默认值 |
| --- | --- |
| `target_kind` | 必填，`block` 或 `entity` |
| `target` | 必填，目标真实名称或运行时显示名，最多 64 字符 |
| `search_radius` | 以初始位置为中心的三维目标半径，默认 16，范围 1～64 |
| `explore` | 是否允许有限路点探索，默认 false |
| `approach` | 是否接近目标，默认 false |
| `approach_distance` | 接近距离，默认 3.5，范围 3～4.5 格 |
| `max_waypoints` | 探索路点上限，默认 4，范围 1～8 |
| `max_results` | 展示结果上限，默认 4，范围 1～8 |

实际参数约束以 `metadata.py` 和 HTTP 导出为准。目标必须经过 `resolve_resource` 确认，候选为空或有歧义时结束并保留候选供澄清。没有静态中文别名表，中文显示名不可用时推荐使用真实名称。

## 测试命令与工具顺序

在项目根目录执行，先启动原 Node/Python 服务，将 `Steve` 换成实际昵称。脚本路径为 `scripts/test-explore-skill.ps1`。

只搜索原木，不移动：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-explore-skill.ps1 -Player Steve -Target oak_log
```

`resolve_resource` → `find_block` → 再次 `find_block` 确认目标 → 返回坐标。

搜索并接近原木：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-explore-skill.ps1 -Player Steve -Target oak_log -Approach
```

名称解析、搜索与复查 → `find_interaction_positions` → `goto_position` → `get_movement_task` → `find_block` → `inspect_block` → 核验距离。已经足够靠近且可见时跳过导航。

搜索并接近牛：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-explore-skill.ps1 -Player Steve -TargetKind entity -Target cow -Approach
```

`resolve_resource` → `find_entity` 搜索并复查 → `goto_position` → `get_movement_task` → `find_entity` 复查同一 ID → 核验距离。接近前还会刷新实体位置；最多主动接近两次。

增加 `-Explore -SearchRadius 40`，搜索/接近未成功时会有限执行“导航到路点 → 查询导航结果 → 再次搜索”。准确分支见 [SOP](SOP.md)。所有模式还会通过 `/api/world` 读取快照及检查租约；这不是额外调用 `get_world_snapshot` 工具。

## 输出与完成条件

后台请求先返回 execution_id，提交成功不代表目标完成。通过执行查询接口观察：

- `status`、`success`、`error`：最终执行状态和真实错误码。
- `data.stage`、`scans`、`waypoints`、`attempts`：进度、搜索位置、路点和失败记录。
- `data.selected`：确认的方块坐标或实体 ID、位置及三维距离。
- `steps`：实际工具账本；`verification`：独立核验证据。

只有目标重新被观察到、位于初始搜索范围、维度一致，且请求接近时最终距离合格，才能 completed。方块接近阶段还必须通过可见性检查。仅搜索成功不表示可交互；接近实体不表示具备攻击视线。

## 边界与验证状态

单次搜索最多 16 格；只搜索已加载数据。有限水平探索不保证覆盖整个范围，`coverage_complete` 始终为 false。最多尝试 6 个不同目标、每个方块 3 个候选站位；总时限和工具预算由现有执行器控制。超时、取消、断线或未知结果立即终止后续调用。

本技能已有 Mock 和真实 Node HTTP 链路自动化测试；真实 Minecraft 验收仍需执行。阶段 B 的运行方法及完整验收清单见 [项目说明](../../../docs/tasks/task0065-skills.md)。本次暂不增加辅助资源文件。
