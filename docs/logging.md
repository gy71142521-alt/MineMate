# 日志与诊断

本文说明 Agent 与 Node 的日志格式、级别、请求 ID、脱敏边界，以及如何用日志定位常见失败。目标是**服务端能看到完整因果链，玩家侧仍只看到固定错误码和中文提示**。

## 1. 一条日志长什么样

```text
2026-11-19 19:20:31 WARNING minemate.tools [rid=8f3a1c2d] [Tools] 模型输出校验失败：reason=PARALLEL_TOOL_CALLS code=PARALLEL_TOOL_CALLS detail=batch too large count=9 limit=8 names=mine_block,collect_item
2026-11-19 19:20:31 WARNING minemate.agent [rid=8f3a1c2d] [Agent] 消息处理失败 | player=Steve code=INVALID_TOOL_CALL status=502 message_len=12 active=1 elapsed_ms=31 | LLMError: 模型返回了无效工具调用或回复，已拒绝继续执行；此前操作请查询状态。
2026-11-19 19:20:31 WARNING minemate.http [rid=8f3a1c2d] [HTTP] /api/agent/message 失败 player=Steve status=502 code=INVALID_TOOL_CALL elapsed_ms=35
```

- `rid`：一次请求的关联 ID。同一次消息处理中的 Node 调用、LLM 请求、工具执行和最终 HTTP 结果共用它。
- `reason`：失败阶段（`MODEL_MESSAGE`、`TOOL_CALL_LIST`、`PARALLEL_TOOL_CALLS`、`ARGUMENTS_SCHEMA` 等）。
- `code`：程序定义的错误码，和返回给 Node 的 `error.code` 一致。
- `detail` / 字段：安全上下文，例如并发工具数量、工具名、缺失参数、耗时分片。
- 5xx 基础设施异常或未预期异常会追加脱敏后的完整 `Traceback`（含异常链）。

## 2. 级别策略

| 场景 | 级别 | 堆栈 |
| --- | --- | --- |
| 请求开始/完成、工具结果、任务状态 | INFO | 否 |
| 模型输出格式不合法、参数不合法、4xx 业务拒绝 | WARNING | 否（模块日志已给出阶段和字段） |
| Node/LLM 超时、连接失败、服务端 5xx、未预期异常 | ERROR/WARNING | 是 |
| 取消、停止、断开 | INFO/WARNING | 否 |

模型输出校验失败不会打印整个堆栈，避免每次模型“说错话”都刷屏；`reason` + `code` + 字段足以定位。真正的程序异常（例如 `RuntimeError`、`AttributeError`）一定会保留堆栈。

## 3. 配置

```dotenv
LOG_LEVEL=INFO          # DEBUG / INFO / WARNING / ERROR
LOG_TRACEBACK=errors    # none | errors | all
```

| 值 | 行为 |
| --- | --- |
| `none` | 只输出异常摘要，不打印任何堆栈 |
| `errors`（默认） | 未预期异常和基础设施 5xx 打印堆栈；模型输出校验失败只给 `reason/code/detail` |
| `all` | 连 `PARALLEL_TOOL_CALLS`、`ARGUMENTS_SCHEMA` 等预期校验也打印堆栈，并输出**有界、脱敏**的模型响应预览（最多约 700 字符） |

`all` 最适合排查“模型到底返回了什么格式”。预览会包含模型生成的工具名、ID、参数键和值片段；已配置的 API Key、`sk-...`、Bearer 等仍会被替换。不要把 `all` 模式日志直接公开上传。

Pydantic `TOOL_CALL_FORMAT` 是例外：即使 `all` 模式也不打印 `ValidationError` 原文（可能含完整模型输入），只输出 `fields=... call=... preview=...`。

`LOG_LEVEL=DEBUG` 会增加：

- LLM 请求的模型、消息数、是否带 tools、耗时；
- Node 每次请求的路径、状态码、字节数、耗时；
- 工具目录读取数量与 generation。

不建议长期开 DEBUG：会明显增加日志量，但不会打印提示词或模型回复全文。自然语言 Runtime 选择 Skill 时，INFO 级别即可看到下述参数日志。

## 4. 脱敏边界

始终替换：

- 配置中的 `LLM_API_KEY` 原值；
- `sk-...` 形态的密钥；
- `Bearer ...`；
- `api_key=...`、`password=...`、`token=...` 等键值形态。

不写入日志：

- 模型回复全文、底层工具参数全文、玩家消息全文；Skill 参数按下面的例外规则输出；
- Authorization 请求头；
- Pydantic 校验错误的 `input`/`context`（只记录字段名和错误类型）；
- Node 返回的完整工具结果。

会写入日志：

- 玩家名、消息长度、工具名、参数缺失项数量、错误码、阶段、耗时、计数；
- 自然语言 Runtime 选择的 Skill 名称及参数：单行、脱敏，参数最多 1200 字符，超出部分以 `…(+剩余字符数)` 标记；
- 供应商 HTTP 错误的**有界、脱敏**正文片段（最多 2 KiB 读取、600 字符输出），用于区分“模型不支持 tools”“余额不足”“参数不兼容”等问题。

## 5. 典型排查

### 5.1 `reason=PARALLEL_TOOL_CALLS`

单次模型响应返回的工具调用超过上限（当前 8 个）时，整批拒绝，不执行其中任何一个。2–8 个调用是允许的，会按模型给出的顺序串行执行；执行到 `pending`、失败或 `uncertain` 后，同一批剩余调用标记为 `NOT_EXECUTED`，不会继续执行。

```text
reason=PARALLEL_TOOL_CALLS code=PARALLEL_TOOL_CALLS detail=batch too large count=9 limit=8 names=mine_block,collect_item,inspect_inventory calls=0:id=10,type=function,name=mine_block,args=str(42) | 1:id=12,type=function,name=collect_item,args=str(18) | 2:id=9,type=function,name=inspect_inventory,args=str(2)
```

`calls=` 是每个调用的结构摘要（id 长度、type、工具名、参数类型和长度），不会包含参数值。设置 `LOG_TRACEBACK=all` 后还会追加 `preview=`，可以看到模型返回的原始调用 JSON（脱敏、限长）。

处理：如果只是 2–3 个查询，不需要处理，系统会串行执行；如果模型一次返回大量调用，提示它优先一次一个，或检查供应商是否忽略 `parallel_tool_calls=false`。

### 5.2 `reason=ARGUMENTS_SCHEMA`

模型给出的参数不符合 Node Schema。

```text
reason=ARGUMENTS_SCHEMA code=ARGUMENTS_SCHEMA tool=mine_block schema=missing=position unexpected=1 arguments_preview=...
```

默认模式不记录参数值；`LOG_TRACEBACK=all` 时 `arguments_preview` 会给出脱敏、限长的参数预览。用 `GET /api/tools` 查看该工具的真实 Schema。

### 5.3 `DUPLICATE_TOOL_CALL`

现在只拒绝**相同 `tool_call_id`** 的重复调用，不再因为“工具名和参数相同”而拒绝。连续 `jump`、连续查询等成功动作可以在总调用预算内重复执行；失败、`pending`、`uncertain` 后不会重试。

```text
reason=DUPLICATE_TOOL_CALL code=DUPLICATE_TOOL_CALL detail=duplicate call id tool=jump call_id_len=32
```

如果看到该错误，检查模型是否复用了上一条 tool call 的 `id`；这不是工具参数格式问题。

### 5.4 `LLM_TOOL_CALLING_REJECTED`

供应商返回 400/422，通常是不支持 `tools` 或参数不兼容。日志中的 `body=` 会给出脱敏后的供应商说明。

### 5.5 `NODE_UNAVAILABLE` / `TOOL_API_TIMEOUT`

- `NODE_UNAVAILABLE`：Python 连不上 Node，先看 Node 是否启动、端口是否一致。
- `TOOL_API_TIMEOUT`：结果**未确认**，不要直接重发副作用；用原 `requestId` 查询回执。

### 5.6 后台任务

```text
[Execution] 工具调用开始 task=... tool=smelt_item request_id=... call=3
[Execution] 工具调用完成 task=... tool=smelt_item request_id=... success=true status=pending error=- elapsed_ms=12
[Execution] 任务失败 task=... code=WAIT_TIMEOUT uncertain=true calls=8
```

后台任务不会把异常吞成 `EXECUTION_FAILED` 一个码：`ExecutionError` 记录具体 code，`LLMError` 记录状态码，未预期异常记录堆栈。

### 5.7 模型选择的 Skill 和参数

重启 Python Agent 后，在 `LOG_LEVEL=INFO` 或 `DEBUG` 下可看到：

```text
[Skills] 模型选择 skill=gather_resource arguments={"count": 4, "resource": "oak_log"} player=Steve call_id=call_123
```

此日志来自自然语言 `execute_skill` 路由，显示模型选择的参数，不包含 Skill 补充的默认值。它在基础类型检查后、Skill 提交与完整参数校验前记录，因此后续参数被拒绝时也能用于排查。看到“模型选择”只表示发起调用，不代表执行或核验成功；结果仍应查看后续 `[Execution]` 日志。直接通过测试脚本提交的 Skill 不会产生“模型选择”日志。

## 6. Node 日志

Node 工具注册表区分两类错误：

- 已知 `ToolError`：输出 `code`、`requestId` 和程序定义的中文 message；
- 未预期异常：输出 `[Tools] <name> 未预期异常 request=...` 并附带 Error 堆栈。

HTTP 层在移动接口异常、请求处理异常和端口占用时也会附带 Error 对象，便于看到真实原因。

## 7. 已知限制

- 日志只输出到 stderr，不做文件轮转；需要留存时请用启动器重定向或系统日志收集。
- `rid` 是进程内随机值，不跨服务重启持久化。
- 日志不能替代真实游戏验收；Mock/回环测试通过不代表 Minecraft 中一定成功。
- 仍有可能包含敏感信息的自由文本（例如供应商错误正文）仅做了已知密钥与常见凭据替换；不要把它当作完整的 DLP 方案。
