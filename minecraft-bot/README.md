# MineMate Minecraft LAN Bot

通过 Mineflayer 连接 Minecraft Java Edition 的 Open to LAN 世界，支持聊天、跟随、停止、坐标导航，以及 Task 003 的自身状态、背包、附近实体和环境方块感知。使用 JavaScript CommonJS，直接依赖为 `mineflayer`、`dotenv`、`mineflayer-pathfinder`；Task 003 没有增加依赖。

Task 001 的连接与聊天已由用户反馈通过真实游戏测试。**Task 002 当前为代码与自动检查阶段，待完成下文的游戏内验收后才算完成。** 本次开发未操作真实游戏、账号授权、`.env` 或认证缓存。

**Task 003 已实现并通过自动化测试，尚待 Minecraft 游戏内验收。** 感知模块只读取数据，通信层也不接入 LLM、规划、记忆或新的游戏能力。

## 环境与版本

- Windows 11，Node.js 24 LTS，npm。
- 本模块锁定 Mineflayer **4.39.0**、dotenv **18.0.10**、mineflayer-pathfinder **2.4.5**，提交 `package-lock.json` 以复现依赖。
- 已核对 pathfinder 发布包：其开发依赖使用 `mineflayer ^4.3.0`，包含当前 Mineflayer；安装后共享兼容的数据/物理依赖，实际插件与寻路算法已用于自动测试。不同 Minecraft 版本的真实移动效果仍需在游戏内验证。
- 当前安装的 Mineflayer 声明最旧支持版本为 **1.8.8**、最新为 **26.1**。具体版本还需核对其支持列表；快照、模组服和后续新版本不保证兼容。代码使用官方自动版本探测，不强行伪装游戏版本。
- Bot 与游戏在同一台电脑时使用 `127.0.0.1`。需要先在游戏内开放 LAN；不需要部署独立服务器。

## 1. 开放世界并获取端口

1. 打开 Minecraft Java Edition，进入一个生存单人世界。
2. 按 Esc，选择“对局域网开放 / Open to LAN”，启动局域网世界。玩家与 Bot 使用生存模式，无需开启作弊。
3. 记下聊天栏公布的**本次端口**。若当前版本允许指定 LAN 端口，以游戏最终显示的端口为准。
4. 保持游戏世界开放。关闭世界后原端口不再有效；下次开放时可能更换端口。

`MC_PORT` 不设默认值，也不会自动扫描端口。不要把 Minecraft 常见默认端口 25565 当作本次 LAN 端口。

## 2. 安装与配置

在 PowerShell 中执行：

```powershell
Set-Location 'D:\桌面\MineMate\minecraft-bot'
npm.cmd install
if (-not (Test-Path -LiteralPath '.env')) {
  Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
```

首次复制一次即可；已有 `.env` 时直接编辑，避免覆盖自己的配置。依赖已安装时不必重复安装；按锁文件重建依赖可使用 `npm.cmd ci`。

使用 VS Code 编辑 `minecraft-bot/.env`，例如：

```dotenv
MC_HOST=127.0.0.1
MC_PORT=实际游戏显示的数字端口
MC_AUTH=microsoft
BOT_USERNAME=MineMate
```

上面的 `MC_PORT` 是文字占位说明，**必须替换成整数**。例如只有当游戏本次确实显示 `51234`，才填写 `MC_PORT=51234`。

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MC_HOST` | `127.0.0.1` | 游戏所在主机，显式填写时不可为空 |
| `MC_PORT` | 无，必填 | 当前 LAN 端口，整数 1–65535 |
| `MC_AUTH` | `microsoft` | 仅接受 `microsoft` 或 `offline` |
| `BOT_USERNAME` | 无，必填 | Microsoft 的账号缓存标识，或 Offline 的游戏昵称 |
| `OWNER_USERNAME` | 空 | 可选，控制移动的玩家游戏昵称；填写后仅该玩家可以跟随、前往或停止 |
| `PATHFINDER_MAX_DISTANCE` | `256` | 坐标目标与跟随玩家的直线距离上限，整数 1–4096 格；超过时提示超出本次导航范围 |
| `PATHFINDER_SEARCH_RADIUS` | `256` | Pathfinder A* 搜索/总代价限制参数，整数 1–4096；与直线距离上限相互独立 |
| `PATHFINDER_THINK_TIMEOUT_MS` | `5000` | 单次 A* 搜索时限，整数 1–60000 毫秒；超时只代表本次计算超时，不代表目标一定不可达 |
| `PERCEPTION_ENTITY_RADIUS` | `16` | 附近实体的三维距离半径，整数 1–64 格 |
| `PERCEPTION_BLOCK_RADIUS` | `5` | 附近方块的三维距离半径，整数 1–5 格 |
| `PERCEPTION_MAX_ENTITIES` | `32` | 每次最多返回的实体数，整数 1–128 |
| `PERCEPTION_MAX_BLOCKS` | `64` | 每次最多返回的方块数，整数 1–256 |
| `PERCEPTION_BLOCK_CACHE_MS` | `1000` | 方块缓存有效期，整数 1–10000 毫秒；位置及生命周期变化会提前失效 |
| `HTTP_HOST` | `127.0.0.1` | Node.js HTTP API 监听地址 |
| `HTTP_PORT` | `3000` | Node.js HTTP API 端口；`0` 仅用于测试时让系统分配临时端口 |
| `HTTP_MAX_BODY_BYTES` | `16384` | Node.js 控制请求体上限 |

从 Task 001 升级时，运行 `npm.cmd install` 获取新依赖即可，保留已有 `.env`。如需限制移动权限，手动向 `.env` 添加 `OWNER_USERNAME=你的游戏昵称`；不要填写 Microsoft 邮箱。留空或不设置时允许所有玩家控制移动，适用于本机及可信 LAN。权限检查不区分昵称大小写，“你好”和“状态”仍对所有玩家开放。

`.env` 固定从本模块目录加载。已有的操作系统/终端环境变量优先于 `.env`；修改 `.env` 后需要停止并重新启动 Bot，且要确认终端没有同名旧变量。

从 Task 002 升级时可以保留现有 `.env`；未填写感知配置时自动使用上表默认值。需要调整时参照 `.env.example` 手动添加相应变量并重启。

当前 PowerShell 的执行策略会阻止 `npm.ps1`，因此文档使用 `npm.cmd`。它与 `npm` 使用同一个 npm 程序；无需修改系统策略。在允许直接运行 npm 的终端也可以使用 `npm install`、`npm start`。

## 3. 选择认证模式

两种模式通过同一个 `MC_AUTH` 配置接口选择；不会自动切换模式，不修改目标世界或服务器的身份验证行为。

| 模式 | 适用条件 | `BOT_USERNAME` 的含义 |
| --- | --- | --- |
| `microsoft` | 需要正常 Microsoft/Minecraft 正版身份验证的目标；本模块默认选项 | 用于区分认证缓存的稳定账号标识，可以用 `MineMate` 或自己的账号标识。实际游戏昵称取决于浏览器登录账号的 Minecraft 档案 |
| `offline` | **目标本来就允许离线连接**，且你有权连接该目标；仅限本机或可信局域网 | 实际游戏昵称，3–16 位英文字母、数字、下划线，并与玩家昵称不同 |

### Microsoft：正常正版认证

对于原版正版 LAN，请优先使用 `MC_AUTH=microsoft`。不要仅因为是 LAN 就假定可以使用 Offline。

1. 为 Bot 准备一个拥有 Minecraft Java Edition 使用权限、已创建 Minecraft 档案且允许多人游戏的 Microsoft 账号。玩家与 Bot 同时在线时应使用不同账号，避免重复登录或相同档案冲突。
2. 设置 `MC_AUTH=microsoft` 和一个稳定的 `BOT_USERNAME` 标识。该字段不会把正版账号的游戏昵称改成 MineMate，也不替代浏览器登录。
3. 执行 `npm.cmd start`。首次启动且无有效缓存时，终端会显示 Microsoft 登录地址与一次性设备代码。
4. 在自己的浏览器打开终端中的 Microsoft 官方地址，输入设备代码，选择 **Bot 专用账号**并完成授权。若浏览器自动选中了玩家账号，请切换账号。
5. 回到终端等待认证及进入世界。密码只输入 Microsoft 官方网页，**不要填写到 `.env`、代码或聊天中**，也不要把设备代码发给他人。
6. 登录成功后，官方认证库会把认证缓存写入 `minecraft-bot/.auth-cache/`，后续可能复用或刷新缓存，因此不一定每次都要求浏览器登录。

`.auth-cache/` 含敏感认证令牌，不是加密保险箱，应仅保留在自己的电脑，不上传、分享或提交 Git。`.gitignore` 已排除此目录以及 `.env`、依赖、日志、npm 缓存；`.env.example` 可以提交。应用不接收或保存明文密码，不输出完整认证响应。不要开启会打印认证细节的第三方依赖 DEBUG 日志。

需要换账号时，停止 Bot，为新账号更换 `BOT_USERNAME` 标识再重新启动。需要强制重新授权时，先停止 Bot，再手动删除或移走本模块 `.auth-cache/` 中的缓存并重新启动；不要删除游戏本身的 `.minecraft` 目录。如缓存已泄露，还应在 Microsoft 账号侧撤销相关访问权限。

### Offline：目标已允许离线连接

只有确认目标本身允许离线连接时才使用：

```dotenv
MC_HOST=127.0.0.1
MC_PORT=本次实际数字端口
MC_AUTH=offline
BOT_USERNAME=MineMate
```

Offline 不会进行 Microsoft 登录，不配置密码，不创建 Microsoft 令牌缓存。它也**不能绕过**目标要求的正版认证。若出现 `Failed to verify username`、`Invalid session` 等拒绝原因，请核对目标要求并改用正常 Microsoft 登录；不要关闭或篡改服务器认证。

## 4. 启动与聊天

```powershell
Set-Location 'D:\桌面\MineMate\minecraft-bot'
npm.cmd start
```

成功时预期日志如下（这是示意，不代表已在你的游戏中验证）：

```text
[MineMate] 正在连接服务器 127.0.0.1:本次端口（认证：microsoft）...
[MineMate] 成功进入游戏
[MineMate] 已发送回复
[Chat] Steve: 你好
[MineMate] 已发送回复
```

首次进入世界，Bot 会发送欢迎消息。在游戏中公开聊天输入：

- `你好`：Bot 回复问候。
- `状态`：Bot 返回当前生命值、饥饿值、X/Y/Z 坐标，以及维度、经验等级和存活状态。
- `背包`：汇总同名物品数量，最多展示 7 种物品；空背包有明确提示。
- `附近`：展示最近的玩家、被动生物、敌对生物或其他实体及大致距离，最多展示 5 个实体。
- `环境`：展示附近常见方块和资源的名称、坐标及大致距离，最多展示 6 个方块。
- `跟随我`：跟随指令发送者，保持约 3 格距离，玩家移动后动态调整目标。
- `停止`：取消跟随或坐标导航，立即释放主动移动控制，回复“已停止移动”。
- `前往 X Y Z`：前往指定方块坐标约 2 格范围，成功到达后回复。例如 `前往 100 65 200`，请按你所在世界选择实际可达坐标。

生命值和饥饿值均按 Mineflayer 的 0–20 数值显示；生命值 20 相当于 10 颗心。位置是 **Bot 本身**的实时坐标，保留两位小数，不是玩家的坐标。状态尚未收到时会提示稍后再试。命令按去除首尾空格后的完整文字匹配，不处理私聊、采集或 LLM 指令。

Bot 按实际游戏昵称忽略自己的消息，避免循环回复；重生不会重复发送欢迎语。按 Ctrl+C 停止进程。本版没有应用层自动重连；连接失败后，修正问题并手动重新运行。

每次重新开放 LAN 后：**核对新端口 → 停止 Bot → 更新 `.env` 的 `MC_PORT` → 重新启动**。

感知摘要还会按实际文本长度截断到 240 个 JavaScript 字符以内，并提示“结果已截断”，避免长名称和大坐标使消息过长。实体或方块的距离包含垂直高度差，按 `Math.hypot(dx, dy, dz)` 计算三维欧几里得距离。

## 状态感知接口（Task 003）

`startBot()` 在现有 Bot 上创建一次感知模块，并通过 `bot.perception` 暴露同一个实例。后续代码可以直接调用只读接口，不要为每次查询新建模块或 Bot：

```javascript
// bot 为 startBot() 返回的已有实例。
const snapshot = bot.perception.getWorldSnapshot();
const json = JSON.stringify(snapshot);
const oakLogCount = bot.perception.getItemCount('oak_log'); // 可用时为数量，否则为 null
```

还提供 `getPlayerState()`、`getInventory()`、`getNearbyEntities()`、`getNearbyBlocks()`。聊天层只解析指令和展示数据；感知层仅通过移动模块的 `getState()` 读取任务摘要，不会设置或取消目标。

`getWorldSnapshot()` 在正常、未出生、重生等待和断线后，始终返回以下顶层结构。下面是未出生或已断线的示例：

```json
{
  "timestamp": 0,
  "availability": {
    "player": "unavailable",
    "inventory": "unavailable",
    "nearbyEntities": "unavailable",
    "nearbyBlocks": "unavailable"
  },
  "player": {
    "position": null,
    "health": null,
    "food": null,
    "experienceLevel": null,
    "dimension": null,
    "alive": null,
    "movement": null
  },
  "inventory": [],
  "equipment": [],
  "nearbyEntities": [],
  "nearbyBlocks": [],
  "truncated": { "entities": false, "blocks": false },
  "limits": {
    "entityRadius": 16,
    "maxEntities": 32,
    "blockRadius": 5,
    "maxBlocks": 64,
    "blockCacheMs": 1000
  }
}
```

实际 `timestamp` 为 `Date.now()` 的毫秒时间戳，`limits` 为当前有效配置。各区块可独立为 `available` 或 `unavailable`。可用的空数组表示没有匹配结果，不可用的空数组表示无法读取；`player` 的位置可读时标记为可用，其余未收到的字段仍可为 `null`。

| 数据 | 可用时的格式与含义 |
| --- | --- |
| `player.position` | `{ x, y, z }`，保留数值精度，只有聊天展示时才格式化 |
| `player` 其他字段 | `health`、`food`、`experienceLevel` 为数字；`dimension` 为 Mineflayer 的维度字符串；`alive` 为布尔值；未知值为 `null` |
| `player.movement` | `{ active, type, taskId }`；空闲时为 `{ active: false, type: null, taskId: null }`；未提供模块时为 `null`，读取异常时内部字段为 `null` |
| `inventory` 项目 | `{ name, count, slot }`，`slot` 为数字槽位，只复制非空物品 |
| `equipment` 项目 | `{ name, count, slot }`，`slot` 为 `mainhand/offhand/feet/legs/chest/head`；旧版本无副手时没有副手项目 |
| `nearbyEntities` 项目 | `{ id, name, type, category, position, distance, isPlayer, valid }`；`category` 为 `player/passive/hostile/other`，排除 Bot 自身和无效实体 |
| `nearbyBlocks` 项目 | `{ name, position, distance }`，只返回当前可获取的白名单方块 |

装备是单独视图，可能与背包槽位中的物品重叠，不应再次相加。`getItemCount()` 仅统计 `inventory` 一份数据，物品不存在时返回 `0`。实体分类使用游戏注册表的类型和分类，未匹配类别归入 `other`，不推测生物当前是否有攻击意图。

性能和缓存规则：

- 按查询扫描，没有每帧扫描、定时轮询、全世界遍历或主动加载区块。实体只检查已加载的 `bot.entities`，按三维距离过滤、排序并限制数量。
- 方块白名单为三种常见原木、石头、圆石、泥土、草方块、沙子、水、煤矿、铁矿及两种深层矿石。使用官方 `findBlocks()` 和 `blockAt(position, false)`，不探测未加载的世界。
- `findBlocks()` 内部向下取整中心，因此候选搜索补偿不足 `√3` 格的偏移；输出仍严格按 Bot 真实位置筛选到配置半径内，再排序、截断。当前配置上限下候选数量上界为 3375，返回数量由 `maxBlocks` 单独限制。
- 方块缓存只在精确位置、维度相同且未超时时复用。同一方块内移动也会刷新；出生、重生、死亡、维度变化、被踢、断线、方块更新、区块加载或卸载均使缓存失效。
- 死亡时环境不可用；重生等待时不返回旧环境，收到新 `spawn` 后恢复。断线后清理感知监听器，保留可调用且字段稳定的不可用快照。
- 返回普通对象/数组的独立副本，可转换为 JSON；修改返回值不会修改游戏对象或缓存。未知数据使用 `null` 或 `unavailable`。

已知边界：只了解已加载数据，空结果不证明更远处没有资源；缓存仍受网络更新和有效期影响，快照不是服务器保证的原子事务。感知不会打开容器、装备或丢弃物品、挖掘或放置方块、改变移动控制。本版没有新增快照聊天指令或对外服务。

## 移动与寻路规则（Task 002）

- 插件在现有 Bot 上加载一次，进入世界后创建保守的 `Movements` 配置。`canDig=false`，清空 `scafoldingBlocks`，禁止搭柱和放置方块；关闭跑酷及自动开门，最大规划下落高度为 1 格，不启用无限向液体下落。
- 同时只有一个主要移动任务。有效的新跟随或坐标指令替换旧目标；错误坐标、实体缺失或权限不足时拒绝新指令，保留当前有效任务。
- `停止` 优先取消当前任务、定时检查和排队的旧结果，不等待走到下一个节点。实现使用官方 `setGoal(null)` 与 `clearControlStates()`；不会修改速度、传送或关闭物理，因此空中停止后仍可能自然下落，受到碰撞、重力或惯性影响。
- 跟随仅限同维度、Bot 能看到的玩家实体；玩家离开、实体卸载或超出 `PATHFINDER_MAX_DISTANCE` 时停止并反馈。即使玩家在线，超出实体加载范围也可能无法跟随。
- 坐标接受三个有限数字，支持负数和小数，小数向下取整为方块坐标（例如 `-12.1` 变为 `-13`）。不支持 `~`、`^` 等相对坐标；不会把缺失值或 `NaN`/`Infinity` 作为有效坐标。
- X/Z 检查 ±29,999,984 的绝对边界；脚部 Y 使用当前维度的 `minY` 到 `minY + height - 2`，为身体保留空间。自定义世界边界、方块碰撞及地形仍需实际寻路判断。
- `PATHFINDER_MAX_DISTANCE` 是目标几何直线距离检查；`PATHFINDER_SEARCH_RADIUS` 是 Pathfinder A* 搜索的额外代价预算参数，两者含义不同、分别配置。默认值各为 256 格；距离检查或搜索范围触发时分别说明本次导航超出范围，不意味着世界中的目标一定不可达。
- `PATHFINDER_THINK_TIMEOUT_MS` 是单次搜索的计算时限，默认 5000 毫秒。超时表示本次计算未及时完成，不证明目标不可达。坐标任务默认总时限为 60 秒；连续 15 秒无移动进展或路径异常也会停止。
- 跟随没有 60 秒总时限，会持续到玩家停止、目标失效或发生寻路异常。正常跟随在玩家附近等待不会触发无进展超时。
- `partial` 表示路径仍在分片计算，不是失败，也不会重置坐标任务总时限。
- Bot 死亡或换维度后取消旧任务，等待再次进入世界才能接受新任务；不会自动恢复之前的跟随。被踢或断开后清除本模块任务、计时器和事件监听器。

这些配置限制寻路行为，但不保证 Bot 能到达所有位置，也不能保证在生存世界完全免受伤害。超出本次配置限制时可调整环境变量并重启；实际能否到达仍取决于已加载区块、地形、安全路线和计算时限。关闭破坏和搭路后，一些原本能靠挖掘、搭桥通过的路线会明确失败。本版不做跨区块长距离探索、传送门导航或模组协议适配。

## 5. 常见错误

| 提示或现象 | 检查方法 |
| --- | --- |
| `MC_PORT 必须填写…` | 填写本次 LAN 的整数端口；不要留下占位文字 |
| `MC_AUTH 仅支持…` | 填写 `microsoft` 或 `offline`，不支持自动降级 |
| 目标拒绝连接 | 世界是否已开放 LAN、主机是否正确、端口是否刚刚变化 |
| 连接超时或无法解析主机 | 本机用 `127.0.0.1`；检查端口、网络和 Windows 防火墙对 Java/Node 的正常放行；认证也需要外网 |
| Microsoft 认证失败 | 是否及时完成设备登录、账号是否拥有 Java Edition/已创建档案、Xbox 隐私和多人游戏权限是否允许、认证服务是否可访问 |
| 没有再次显示设备代码 | 可能复用了有效缓存；换账号或需要重新授权时按认证章节操作 |
| 登录被拒绝或被踢出 | 查看终端记录的服务器原因；检查模式、重复账号/昵称、会话有效性和多人游戏权限 |
| Offline 被要求验证身份 | 该目标需要正常认证；使用 Microsoft 模式，不修改服务器认证 |
| 版本或协议不兼容 | 确认游戏具体版本和官方支持列表；未经检查不要强行降级/升级依赖或伪装协议版本 |
| `.env` 已修改但仍使用旧值 | 重启 Bot，检查终端是否设置了同名环境变量 |
| 看不到玩家实体 | 靠近 Bot，确认同维度且玩家实体已加载；在线列表存在不代表实体可见 |
| 只有主人可以控制移动 | 检查 `OWNER_USERNAME` 是否为玩家当前游戏昵称；设置主人后其他玩家也不能发送停止指令 |
| 找不到安全可达路径 | 选择已加载且可步行到达的位置；Bot 不会挖墙、搭桥或搭柱 |
| 目标超出本次导航范围 | 检查 `PATHFINDER_MAX_DISTANCE` 与当前维度 Y 范围；可适度调整距离并重启 |
| 路径计算超时或移动受阻 | 用 `PATHFINDER_THINK_TIMEOUT_MS` 调整搜索时限，或清理通道后重试；不会自动重试 |
| 移动系统尚未就绪 | 等待进入世界；若终端记录初始化失败，请检查插件依赖与游戏版本 |

连接、认证或登录失败会设置非零退出状态；断开时输出日志。认证错误只输出分类提示，不转储原始响应、堆栈或请求凭据。

## 6. 自动检查

```powershell
npm.cmd test
```

使用 Node.js 内置 `node:test`，不添加测试依赖。测试覆盖配置校验、统一认证选项、设备登录输出、一次欢迎、自身消息过滤、问候与实时状态、发送异常、连接/认证/登录失败及失败退出，以及以下移动行为：

- 发送者实体识别、动态跟随、坐标解析与范围校验、主人权限。
- 立即停止跟随和导航、新指令替换旧目标、排队的旧结果不影响新任务。
- 到达、无路、搜索超时、卡住、任务总超时、玩家消失、死亡/换维度和断线清理。
- 重复命令不会累积监听器或遗留旧任务的定时器。
- 实际 pathfinder 插件与 A* 算法在合成地形中绕过三格高障碍；必须挖墙或搭路时报告无路，路径中不包含破坏或放置操作，即使模拟背包有方块。
- 实际插件的到达/失败事件与后续任务之间不会发生同步收尾覆盖。

事件与任务测试使用模拟 Bot 和可控时间；合成地形测试使用 Minecraft 1.21.4 的实际方块注册表；启动测试使用真实 Mineflayer 连接一个未监听的本机回环端口。测试不登录真实账号、不进入 Minecraft、不修改你的 `.env` 或认证缓存。模拟寻路不等同于已验证真实服务端、物理运动或所有 Minecraft 版本。

Task 002 本次自动检查：**51/51 项测试通过，10 个源码和测试 JavaScript 文件语法检查通过**；直接依赖版本检查通过（Mineflayer 4.39.0、pathfinder 2.4.5、dotenv 18.0.10）。新增真实插件与 A* 合成地形验证仍通过。测试未连接真实游戏；本次未修改已有 `.env` 或认证缓存。

Task 003 自动检查：**75/75 项测试通过，16 个源码和测试 JavaScript 文件语法检查通过**；`npm.cmd ls --depth=0` 通过，直接依赖版本保持不变，运行环境为 Node.js 24.21.0。覆盖原有连接、认证、聊天、跟随、停止、坐标导航，以及自身状态、物品汇总、空背包、实体分类与三维距离、无效实体、扫描范围与数量上限、缓存失效与有效期、未出生/死亡/断线、稳定字段、JSON 序列化、返回值隔离、只读行为和聊天截断。另使用实际 Mineflayer 方块插件与合成区块验证扫描半径、最近排序和数量限制。合成区块不是真实 Minecraft 游戏；游戏验收仍待执行。

安装时已执行 npm 审计：当前依赖树报告 **6 个 moderate 受影响包条目**，追溯到传递依赖 `uuid <11.1.1` 的 [GHSA-w5hq-g745-h8pq](https://github.com/advisories/GHSA-w5hq-g745-h8pq)。审计当时标记无可用修复，未强制替换官方依赖或运行破坏兼容性的 `npm audit fix --force`。这不等同于已证明当前连接路径可利用；后续应随上游兼容修复更新锁文件并复测。

## 7. 需要你在 Minecraft 中验收

- [ ] 使用支持的 Java Edition 版本创建生存世界并开放 LAN，获取实际端口。
- [ ] 配置 `.env`，确认认证模式符合目标要求、Bot 与玩家的身份不冲突。
- [ ] Microsoft 模式首次通过自己的浏览器完成 Bot 账号登录，验证实际账号权限及认证缓存复用。
- [ ] 运行 `npm.cmd start`，看到 Bot 出现在世界/玩家列表中，终端出现“成功进入游戏”。
- [ ] 游戏聊天出现一次欢迎语；输入“你好”收到问候，Bot 不会对自己的回复反复回应。
- [ ] 输入“状态”，结合 Bot 的实际位置、受伤/饥饿情况核对数据；位置指 Bot，不是玩家。
- [ ] 正常关闭世界，确认终端记录断开且不会自动重连。
- [ ] 再次开放 LAN，核对端口是否变化，更新配置并手动重启，验证可以重新接入。
- [ ] 如有本来允许 Offline 的测试目标，显式选择 Offline 并验证昵称及聊天；不要为了验收而关闭现有目标的认证。

Task 001 已由用户反馈完成实际游戏测试，升级后仍建议回归检查连接和旧指令。

### Task 002 游戏内验收（待执行）

1. 在已成功连接的原版生存世界找一块开阔且已加载的平地，确认附近没有会干扰验证的怪物或危险地形。
2. 发送“你好”“状态”，确认原有回复正常；记录 Bot 坐标，用于选择附近的导航目标。
3. 在 Bot 附近发送“跟随我”，步行改变方向，观察 Bot 是否持续调整路线并保持约 3 格距离；再站定等待，确认 Bot 能正常停留。
4. 跟随过程中发送“停止”，应立即取消主动移动并回复“已停止移动”；玩家继续走动时，Bot 不应恢复跟随。
5. 发送“前往 X Y Z”，使用 Bot 附近真实可达的整数坐标（不要盲目照搬 `100 65 200`）；观察 Bot 到约 2 格范围后回复到达。
6. 导航尚未结束时发送“停止”，验证导航也可中断。
7. 跟随中发送坐标指令；导航中发送“跟随我”或另一个坐标指令；确认新任务接管，旧目标不会恢复或误报到达。
8. 发送格式错误、非数字、超高/超低或超过 `PATHFINDER_MAX_DISTANCE` 的坐标，检查明确反馈；当前有效任务应保留。
9. 用已有障碍测试绕行，再选择需要挖墙或搭桥才能到达的位置，确认 Bot 失败时给出说明，既不破坏方块也不放置方块，不无限尝试。
10. 跟随期间让目标退出、离开实体加载范围，或使 Bot 死亡/换维度，确认旧任务取消；重生后需重新发送命令。
11. 如设置 `OWNER_USERNAME`，用另一名玩家验证不能控制移动（包括停止），但“你好”“状态”仍可使用。
12. 任务运行时关闭世界，确认断开清理；重新开放 LAN、更新端口并启动后，旧任务不应自动恢复。

**以上跟随、动态调整、停止、坐标导航、目标替换、旧指令及不挖掘行为，全部经过真实游戏验证后，Task 002 才算完成。当前尚未进行这些游戏内验证。**

### Task 003 游戏内验收（待执行）

1. 保留现有身份验证配置，开放生存世界 LAN，把当前实际端口填写到 `.env`，运行 `npm.cmd start`；等待 Bot 进入世界。
2. 输入“你好”“状态”，核对原有问候、Bot 的生命值/饥饿值/坐标，以及维度和经验等级。让 Bot 正常获得经验或受到伤害后再次查询，确认更新。
3. 背包为空时输入“背包”；向 Bot 正常给予两组同名物品后再次查询，核对汇总。装备栏可以在下述快照中查看。确认查询没有丢弃、装备或转移物品。
4. 在 16 格内靠近玩家、牛/羊/猪等动物，并在安全条件下观察僵尸/骷髅；输入“附近”，核对类别和距离。使用有垂直高度差的位置，确认距离包含高度差；Bot 自身不应出现在列表。
5. 在 5 格内选择木头、石头、泥土或矿石，输入“环境”，核对名称、坐标和距离；密集场景应提示截断。移动 Bot 后再次查询，确认不复用旧位置环境；由玩家正常改变附近方块后查询，确认更新。
6. 验证统一快照：在 VS Code 的 JavaScript 调试终端启动 `npm.cmd start`，在 `src/events/chat.js` 的 `getSnapshot()` 中设断点，游戏中输入“状态”。在调试控制台执行 `JSON.stringify(bot.perception.getWorldSnapshot(), null, 2)`，检查顶层结构、数值字段、槽位、装备和截断标记，然后继续运行。只读取快照，不调用控制或写入 API。
7. 在跟随及坐标导航过程中分别发送“状态”“背包”“附近”“环境”，确认不会取消或替换目标；重复验证“停止”、新目标替换和旧聊天。快照中的 `player.movement` 应随任务变化。
8. 在你允许的测试条件下验证死亡/重生、维度变化和关闭世界；重生后读取新环境，旧跟随不自动恢复，断线后不继续回复。未出生、被踢和断线后的稳定快照已由自动化测试覆盖，真实连接的状态变化仍需核对。
9. 查询前后观察周围方块和 Bot 背包，确认感知没有破坏、放置、丢弃、自动装备或干预正常移动。

**以上感知、快照、只读行为和 Task 001/002 回归全部通过真实游戏验证后，Task 003 才算完成。目前未执行本节游戏内测试。**

## Task 004：Node.js 与 Python Agent 通信层

Task 004 增加了本机 HTTP + JSON 通信基础设施。Node.js 仍是唯一的 Minecraft 控制入口；Python 只调用 Node.js API，不直接连接 Minecraft，也没有接入 LLM、自动规划或长期记忆。

### 通信流程

```text
Python FastAPI :8000
        │ HTTP + JSON
        ▼
Node.js MineMate API :3000
        │
        ▼
Mineflayer Bot / perception / movement
```

Node API 在 `npm.cmd start` 时与 Bot 一起启动，监听 `HTTP_HOST:HTTP_PORT`。Python 服务是独立进程，默认监听 `127.0.0.1:8000`，通过 `agent/.env` 的 `NODE_API_URL` 找到 Node 服务。

### Node.js API

所有响应都包含 `success`、`data`、`error` 三个字段；成功时 `error` 为 `null`，失败时 `data` 通常为 `null`，错误只包含稳定的 `code` 和简短 `message`。

| 方法与路径 | 请求体 | 说明 |
| --- | --- | --- |
| `GET /health` | 无 | 返回 Node 服务与 Bot 的 `connected`、`spawned`、`ready` 状态 |
| `GET /api/world` | 无 | 返回现有 `getWorldSnapshot()`；Bot 未出生时返回 503 和 `BOT_NOT_READY` |
| `POST /api/movement/follow` | `{ "username": "Steve" }` | 调用现有 `movement.follow()` |
| `POST /api/movement/goto` | `{ "username": "Steve", "x": 100, "y": 65, "z": 200 }` | 调用现有 `movement.goTo()`，坐标必须是有限数字 |
| `POST /api/movement/stop` | `{ "username": "Steve" }` | 调用现有 `movement.stop()` |

示例：

```powershell
Invoke-RestMethod http://127.0.0.1:3000/health
Invoke-RestMethod http://127.0.0.1:3000/api/world
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:3000/api/movement/goto `
  -ContentType 'application/json' -Body '{"username":"Steve","x":100,"y":65,"z":200}'
```

控制接口只接受 3–16 位合法游戏昵称，拒绝未知字段、非有限坐标、无效 JSON 和超过 `HTTP_MAX_BODY_BYTES` 的请求。Bot 未连接或未出生时，健康检查仍可用，世界和移动接口返回明确的 503 错误。服务不会返回内部堆栈。

### Python FastAPI 服务

首次准备 Python 服务：

```powershell
Set-Location 'D:\桌面\MineMate\agent'
python -m venv .venv
\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

按需编辑 `agent/.env`：

```dotenv
AGENT_HOST=127.0.0.1
AGENT_PORT=8000
NODE_API_URL=http://127.0.0.1:3000
NODE_API_TIMEOUT_SECONDS=5
```

启动：

```powershell
\.venv\Scripts\python.exe -m app.main
```

Python API 路径为 `/health`、`/api/game/world`、`/api/game/follow`、`/api/game/goto` 和 `/api/game/stop`。后四个接口使用 Pydantic 校验请求，并转发 Node 的统一响应；Node 不可用、请求超时、无效 JSON 响应和非成功响应会转换为简洁的 503、504 或 502 错误。Python 配置不包含密码、API Key 或认证令牌。

启动顺序建议：先在 Minecraft 中开放 LAN，再启动 Node.js Bot，确认 `http://127.0.0.1:3000/health` 可访问，最后启动 Python 服务并检查 `http://127.0.0.1:8000/health`。Python 服务的健康接口只表示 Agent 进程存活，不代表 Minecraft 已连接，应另外检查 Node 健康接口。

### Task 004 自动检查与边界

Node 测试覆盖健康、快照、三个移动接口、参数拒绝、请求体大小、Bot 未就绪和执行异常；Python 测试覆盖健康、配置、Pydantic 参数、正常转发、连接失败、超时和无效 Node 响应。当前结果为 **Node 80/80、Python 9/9**，分别使用 Node Mock HTTP 和 Python ASGI/HTTP Mock，不连接真实 Minecraft。Node 服务没有自动重连；Python 也不会绕过 Node 直接操作游戏。下一阶段如需 Agent 决策，应在 Python 服务上层增加明确的工具调用策略，并继续保留 Node 的参数校验和安全边界。

## 文件职责与官方参考

- `src/index.js`：入口与退出状态。
- `src/config.js`：环境变量加载与校验。
- `src/auth.js`：统一 Microsoft/Offline 认证选项。
- `src/bot.js`：Bot 创建、连接事件和错误分类。
- `src/movement.js`：保守移动配置、统一跟随/导航/停止接口、单任务状态、超时与生命周期清理。
- `src/perception/index.js`：统一只读接口、稳定快照、异常隔离与生命周期/缓存管理。
- `src/perception/playerState.js`：自身状态、数值坐标及只读移动任务摘要。
- `src/perception/inventory.js`：背包槽位、装备副本和物品数量汇总。
- `src/perception/nearbyEntities.js`：已加载实体筛选、分类、三维距离与数量限制。
- `src/perception/nearbyBlocks.js`：常见方块扫描、三维距离、最近结果、缓存和数量限制。
- `src/events/chat.js`：欢迎语、聊天指令解析、移动/感知接口、聊天摘要及异步反馈。
- `src/httpServer.js`：Node HTTP 路由、JSON 响应、请求体限制和控制参数校验。
- `agent/app/main.py`：Python FastAPI 路由和统一错误响应。
- `agent/app/node_client.py`：Python 到 Node 的 HTTP 转发、超时和无效响应处理。
- `agent/app/schemas.py`：Python Pydantic 请求模型。
- `agent/requirements.txt`：Python 服务依赖。
- `test/`：不依赖真实账号和游戏世界的自动测试。

API 已对照 [Mineflayer 官方说明](https://github.com/PrismarineJS/mineflayer/blob/master/docs/README.md)、[Mineflayer API](https://github.com/PrismarineJS/mineflayer/blob/master/docs/api.md) 和 [node-minecraft-protocol 认证选项](https://github.com/PrismarineJS/node-minecraft-protocol/blob/master/docs/API.md#mccreateclientoptions) 检查，使用 `createBot`、`spawn`、`chat`、`error`、`kicked`、`end`、`bot.chat()`、`bot.health`、`bot.food`、`bot.entity.position`。

感知 API 已对照本地安装的 Mineflayer 4.39.0 官方 `docs/api.md` 及插件源码检查：`bot.experience.level`、`bot.game.dimension`、`bot.isAlive`、`bot.inventory.slots`、`bot.entity.equipment`、`bot.entities`、`bot.findBlocks()`、`bot.blockAt()`，以及出生/死亡/重生、方块和区块更新事件；未使用自定义协议包。

移动 API 对照 [mineflayer-pathfinder 官方文档](https://github.com/PrismarineJS/mineflayer-pathfinder#readme) 和实际安装的 2.4.5 发布包源码检查。移动模块的 `follow(username)`、`goTo(username, x, y, z)`、`stop(username)` 均返回 `{ ok, status, message, username, taskId }`；每个任务都有单调递增且唯一的 ID，定时器、排队回调、异步结果都先验证 ID 再生效。结果事件携带对应 `taskId`，`getState()` 返回当前类型、玩家和 ID，`dispose()` 清理状态。未来扩展应复用这些接口及同一个 Bot，保持单一主要移动目标。
