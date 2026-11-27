# 流式问答事件设计

## Context

| 位置 | 从代码确认的行为 |
| --- | --- |
| Agent 产生事件 | 普通问答和 ReAct 使用同一个 `emit_event(name, dict)`。两条内部流都用 SSE 发送事件名和数据，内部 `result` 使用 `QueryOut`。见 [发射函数](../../../../apps/agent/kongming_agent/graph/streams.py#L26)和 [Agent 响应](../../../../apps/agent/kongming_agent/main.py#L489)。 |
| API 接收 Agent 流 | 按事件名白名单筛选。过程消息只检查数据能否解析为字典，随后进入队列，阶段消息还会写入阶段记录。当前 `trace` 也会转发，内容可包含表预筛结果、评分或引用净化信息。见 [中继](../../../../apps/api/kongming/chat/stream_runner.py#L189)和 [诊断事件](../../../../apps/agent/kongming_agent/graph/nodes.py#L373)。 |
| API 对外发送 | `result` 先将领域结果转成 `ChatQueryOut`，再由响应模型编码。其余事件用 `json.dumps(default=str)`，遇到不支持的值时可能直接转成文本。接口说明用 `oneOf` 列出裸数据结构，200 响应还额外声明了实际不会返回的 `application/json`。见 [发送函数](../../../../apps/api/kongming/chat/router.py#L194)、[模型与响应说明](../../../../apps/api/kongming/chat/schemas.py#L189)和 [生成的接口说明](../../../../contracts/openapi.json)。 |
| 网页接收 | 按 SSE 的 `event:` 名查表，用 TypeScript 的 `as` 直接信任解析后的数据。现有测试把空对象视为合法结尾。读流逻辑标记结尾后还会继续分发同一网络分块中的消息。见 [分发函数](../../../../packages/api-client/src/api.ts#L276)和 [现有测试](../../../../apps/web/src/test/shared/api.test.ts)。 |

## Goals / Non-Goals

- API 的公开事件模型同时供校验、编码和契约生成使用。每种事件保留自身的业务数据结构。
- Agent 内部 `QueryOut` 与 API 公开 `ChatQueryOut` 分属两个边界。前者包含问答引擎的内部字段，后者带有 API 补充的会话、保存结果和展示信息。两进程继续遵守 [ADR-002](../../../../docs/adr/002-trust-boundary.md)。
- HTTP 发送失败与问答保存各自处理。发送端只处理输出，轮次结果仍由现有问答流程决定。

## Decisions

### 1. 用 JSON 的 `type` 选择事件数据结构

公开流采用默认 SSE 消息。每条业务帧的 `data:` 包含完整的应用事件，类型字段只写在 JSON 内。网页继续通过 `fetch` 发起带身份凭证和请求体的 POST，并按 `type` 分发。SSE 注释用于心跳。

```text
: ping

data: {"type":"stage","data":{"node":"generate","status":"done","retry_count":0}}

data: {"type":"sql","data":{"sql":"select count(*) from products"}}

```

WHATWG 把文本分帧规则和应用消息内容分开。`event:` 可用于命名，省略时 SSE 采用默认消息类型。Claude 的流同时提供 SSE 事件名和 JSON `type`。OpenAI Responses 的事件模型也按 JSON `type` 区分。Kongming 当前的网页直接读 `fetch` 响应，因此使用 JSON `type` 一个位置即可完成校验和分发。见 [WHATWG SSE](https://html.spec.whatwg.org/multipage/server-sent-events.html#interpreting-an-event-stream)、[Claude 事件定义](https://platform.claude.com/docs/en/build-with-claude/streaming#event-types)和 [OpenAI Responses 流式事件](https://developers.openai.com/api/reference/resources/responses/streaming-events)。

| 做法 | 复杂度与维护 | 运行开销 | 测试与切换 |
| --- | --- | --- | --- |
| 现在的命名帧和字典 | 事件名、字典形状和网页 `as` 各自维护 | 每帧解析和编码，坏结构也会到网页 | 现有测试偏重能否分帧，空对象能触发结束 |
| 保留 `event:` 并给命名帧加类型 | API 和网页解析后都要把事件名与 JSON 数据组合成有类型的对象 | 逐帧检查，外加两处组合动作 | 能保留原格式，需要验证事件名和数据的配对 |
| 采用 JSON `type` | 完整事件直接进入同一个模型，新增事件时生成的类型会要求网页处理该类型 | 逐帧检查和一次输出编码。网页验证结果数据时也要遍历声明了结构的字段 | 需同时替换 API 和网页。用同一份定义检查样例、真实输出和网页行为 |

### 2. 在 API 接收端和发送端各检查自己负责的数据

1. `schemas.py` 保留已有的数据模型，并为六种公开事件各定义一个带 `Literal` 的 `type`。用 `Annotated[..., Field(discriminator="type")]` 表达按类型选模型。这里的 `Annotated` 向 Pydantic 声明选择规则。将公开联合注册为 `ChatStreamEvent`，从同一批定义派生四种过程事件的校验器。严格检查过程字段和事件外层，保留工具摘要字段的现有默认值。见 [Pydantic 按字段选择类型](https://pydantic.dev/docs/validation/latest/concepts/unions/#discriminated-unions)。
2. 在 API 与 Agent 流的交界处继续读 Agent 的命名帧。将过程数据组合成 `{type, data}` 后校验，再记录有效阶段或放入队列。`trace` 留在 Agent 的内部流。未知或校验失败的过程事件跳过，日志使用 `trace_id`、已知类别或失败类别，原始内容不参与日志。
3. Agent `result` 的数据必须是 JSON 对象且 `success` 为布尔值，随后复用现有 `parse_agent_reply` 和问答收尾。Agent `error` 的数据必须是 JSON 对象且有字符串 `error`，API 使用自己的友好文案。两种事件均按第一条合法结尾处理。仍由后台任务完成内部流的清理，已经选定结尾后停止处理后续业务帧。现有独立数据库会话和显式取消的优先规则沿用。
4. 把出站队列的 `(str, object)` 改为已检查的过程事件、领域 `ChatOutcome`、有类型的输出错误或结束标记的联合。普通问答、无需调用 Agent 的结果和已取消结果都汇入同一个发送出口。只在该边界使用同步端点现有的 `to_chat_query_out` 转换 `ChatOutcome`，把结果装入 `ResultEvent`。路由使用同一个 Pydantic 编码器检查并编码每条公开消息，序列化警告按错误处理，编码器复用。见 [Pydantic 编码接口](https://pydantic.dev/docs/validation/latest/api/pydantic/type_adapter/#pydantic.type_adapter.TypeAdapter.dump_json)。
5. 流开始前照常处理认证、权限、参数和配额错误。流开始后，单个坏过程事件可跳过。Agent 传输失败按现有规则保存失败并发送 `AGENT_UNAVAILABLE`。API 转换或编码结尾失败、或队列提前结束时，发送一条预先由同一模型产生的 `STREAM_OUTPUT_FAILED` 错误帧并结束。这个错误只描述答案传输，已有问答结果与保存状态仍以问答流程为准。客户端断开或任务取消由现有机制处理。

保留当前先检查请求、再返回 `StreamingResponse` 的路由形态，使用一个声明 `text/event-stream` 的响应类。FastAPI 原生 SSE 支持在生成器路由中按元素类型处理数据，手动 `ServerSentEvent` 对象则走另一条编码路径。项目锁定的 FastAPI 0.144.0 中，给现有协程路由直接换 `EventSourceResponse` 会进入生成器处理，独立实验得到协程不可迭代的错误。保留当前路由形态可在发送响应头前返回 HTTP 错误，并由当前发送出口明确处理结尾编码失败。见 [FastAPI SSE 文档](https://fastapi.tiangolo.com/tutorial/server-sent-events/)。

### 3. 生成网页类型和接收时的校验函数

- OpenAPI 继续使用仓库当前的 3.1.0。200 响应仅声明 `text/event-stream`，响应体表示为文本。用媒体类型的 `x-sse-data-schema` 引用 `components.schemas.ChatStreamEvent`，明确它描述的是单条 `data:` 解码后的 JSON。通过路由的响应模型注册组件并由 Pydantic 生成带 `oneOf` 和 `discriminator` 的规则。`result.data` 引用现有 `ChatQueryOut`。OpenAPI 3.2 定义了 `itemSchema`，本次改动的范围是单个接口，继续用 3.1 的扩展字段。见 [OpenAPI 3.1 媒体类型](https://spec.openapis.org/oas/v3.1.0.html#media-type-object)和 [OpenAPI 3.2 SSE](https://spec.openapis.org/oas/v3.2.0.html#special-considerations-for-server-sent-events)。
- 沿用 `openapi-typescript` 生成事件类型。基于同一个 `x-sse-data-schema` 引用，用 Ajv 8 在构建阶段生成仅用于聊天流的校验模块。Ajv 已出现在网页锁文件中，实施时声明为相同版本线的直接开发依赖。只将生成的校验模块提供给浏览器。校验使用每个分支必填的 `type` 常量和 JSON Schema `oneOf`，关闭隐式转换与默认值填充。将校验产物纳入 `contracts:gen` 和 `contracts-check`，保持与 [ADR-005](../../../../docs/adr/005-contract-pipeline.md) 的单一来源一致。见 [类型生成说明](https://openapi-ts.dev/advanced#use-oneof-by-itself)和 [Ajv 独立校验模块](https://ajv.js.org/standalone.html)。
- 网页按 SSE 规则先恢复完整数据消息，再 `JSON.parse` 并调用生成的校验函数。检查通过后用 `switch (event.type)` 分发，保留现有组件回调。使用 TypeScript 的穷尽检查让新增事件在缺少处理分支时无法通过类型检查。对跨网络分块的 UTF-8、LF、CRLF、CR、注释和多行 `data:` 写分帧测试。第一条合法结尾触发回调并停止该请求的读取和分发，即使同一块字节还包含后续消息。见 [TypeScript 类型收窄](https://www.typescriptlang.org/docs/handbook/2/narrowing.html)和 [WHATWG SSE](https://html.spec.whatwg.org/multipage/server-sent-events.html#interpreting-an-event-stream)。

### 4. 用锁定版本验证技术路径

使用 FastAPI 0.144.0、Pydantic 2.14.1、`openapi-typescript` 7.13.0 和 Ajv 8.20.0 建了独立实验，复用了项目的 `ChatQueryOut`。验证了以下行为：

- 独立 HTTP 请求能在流开始前返回 403 JSON，成功时返回合法 SSE。生成的 OpenAPI 3.1 响应只声明 `text/event-stream`，可找到六种事件的定义。
- 生成的 TypeScript 类型可以按 `type` 推断正确的 `data`，穷尽检查通过。生成的网页校验函数接受合法的中文进度及错误事件，拒绝错类型、空 `result` 和内部 `trace`。
- 非法 Python 对象进入 `result` 的动态数据时，Pydantic 报编码错误。生成的网页校验模块可单独运行，实验生成尺寸约 79 KB，为打包、压缩前的源码大小。部署体积与真实问答表现列入实施验证。

## Risks / Trade-offs

- **接收侧多一次检查** → 每次只检查完整的一条事件。过程模型复用校验器，大结果只有结尾一条，验收时记录实际构建体积并用正常大结果验证显示。
- **结束失败可能发生在保存之后** → `STREAM_OUTPUT_FAILED` 用固定文案说明答案未完整送达，保存状态从历史读取。发送端不重新执行或保存问答。
- **新版 API 与旧网页会读不到合法结尾** → 同步发布并重新加载网页资源。公开格式只维护一套。
- **当前公开流能看到内部 `trace` 内容** → 新契约只发布六种已声明的事件。原始诊断仍可从现有内部流获取。

## Migration Plan

1. 在同一次改动中交付 API 发送端、网页接收端、OpenAPI 和生成产物，按新格式更新相应测试样例。
2. 运行合并请求现有的 `oasdiff` 检查，正常处理所有差异。若它把移除虚假的 200 JSON 响应等变动报为错误，仅为已确认的 `POST /chat/query/stream` 指定差异添加按方法、路径和消息匹配的记录。其他操作仍使用原检查。见 [oasdiff 1.39.0 的单项忽略规则](https://github.com/oasdiff/oasdiff/blob/v1.39.0/docs/BREAKING-CHANGES.md#ignoring-specific-breaking-changes)。
3. API 与网页一起切换或回退。验收时用新版网页走一轮普通问答和一轮 ReAct 问答，再验证显式停止及断线后查看历史。
