# 日志与诊断

本文说明 Agent 与 Node 的日志格式、级别、请求 ID、脱敏边界，以及如何用日志定位常见失败。目标是**服务端能看到完整因果链，玩家侧仍只看到固定错误码和中文提示**。

## 1. 一条日志长什么样

```text
2026-11-19 19:20:31 WARNING minemate.tools [rid=8f3a1c2d] [Tools] 模型输出校验失败：reason=PARALLEL_TOOL_CALLS code=PARALLEL_TOOL_CALLS detail=parallel calls rejected count=2 names=mine_block,collect_item
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

不建议长期开 DEBUG：会明显增加日志量，但不会打印提示词、模型回复全文或工具参数全文。

## 4. 脱敏边界

始终替换：

- 配置中的 `LLM_API_KEY` 原值；
- `sk-...` 形态的密钥；
- `Bearer ...`；
- `api_key=...`、`password=...`、`token=...` 等键值形态。

不写入日志：

- 模型回复全文、工具参数全文、玩家消息全文；
- Authorization 请求头；
- Pydantic 校验错误的 `input`/`context`（只记录字段名和错误类型）；
- Node 返回的完整工具结果。

会写入日志：

- 玩家名、消息长度、工具名、参数缺失项数量、错误码、阶段、耗时、计数；
- 供应商 HTTP 错误的**有界、脱敏**正文片段（最多 2 KiB 读取、600 字符输出），用于区分“模型不支持 tools”“余额不足”“参数不兼容”等问题。

## 5. 典型排查

### 5.1 `reason=PARALLEL_TOOL_CALLS`

模型一次返回了多个工具调用，而 MineMate 要求每轮只调用一个以保持串行和可取消。

```text
reason=PARALLEL_TOOL_CALLS code=PARALLEL_TOOL_CALLS detail=parallel calls rejected count=3 names=mine_block,collect_item,inspect_inventory calls=0:id=10,type=function,name=mine_block,args=str(42) | 1:id=12,type=function,name=collect_item,args=str(18) | 2:id=9,type=function,name=inspect_inventory,args=str(2)
```

`calls=` 是每个调用的结构摘要（id 长度、type、工具名、参数类型和长度），不会包含参数值。设置 `LOG_TRACEBACK=all` 后还会追加 `preview=`，可以看到模型返回的原始调用 JSON（脱敏、限长）。

处理：确认提示词是否被改动；不要把 `parallel_tool_calls` 改成 true；模型不支持串行约束时换模型。

### 5.2 `reason=ARGUMENTS_SCHEMA`

模型给出的参数不符合 Node Schema。

```text
reason=ARGUMENTS_SCHEMA code=ARGUMENTS_SCHEMA tool=mine_block schema=missing=position unexpected=1 arguments_preview=...
```

默认模式不记录参数值；`LOG_TRACEBACK=all` 时 `arguments_preview` 会给出脱敏、限长的参数预览。用 `GET /api/tools` 查看该工具的真实 Schema。

### 5.3 `LLM_TOOL_CALLING_REJECTED`

供应商返回 400/422，通常是不支持 `tools` 或参数不兼容。日志中的 `body=` 会给出脱敏后的供应商说明。

### 5.4 `NODE_UNAVAILABLE` / `TOOL_API_TIMEOUT`

- `NODE_UNAVAILABLE`：Python 连不上 Node，先看 Node 是否启动、端口是否一致。
- `TOOL_API_TIMEOUT`：结果**未确认**，不要直接重发副作用；用原 `requestId` 查询回执。

### 5.5 后台任务

```text
[Execution] 工具调用开始 task=... tool=smelt_item request_id=... call=3
[Execution] 工具调用完成 task=... tool=smelt_item request_id=... success=true status=pending error=- elapsed_ms=12
[Execution] 任务失败 task=... code=WAIT_TIMEOUT uncertain=true calls=8
```

后台任务不会把异常吞成 `EXECUTION_FAILED` 一个码：`ExecutionError` 记录具体 code，`LLMError` 记录状态码，未预期异常记录堆栈。

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
