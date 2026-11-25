# 流式问答事件统一定义

## Why

流式问答用 SSE 在一个 HTTP 响应内逐条发送进度和结果。现在接口说明列了各类数据的形状，实际发送和接收时却只有 `result` 走响应模型，其他事件可以把字段缺失或类型错误的字典一路传给网页。网页甚至会把 `result` 配上空对象当作问答结束。

## What Changes

- **BREAKING** `POST /chat/query/stream` 的每条业务消息统一为 `data: {"type":"…","data":{…}}`。`type` 决定 `data` 的格式。网页与 API 在同一次改动中切换到新格式。
- 定义 `stage`、`sql`、`summary`、`tool_call`、`result`、`error` 六种公开事件。`result.data` 复用同步问答的响应模型。Agent 的 `trace` 诊断消息限于内部流。
- API 先检查 Agent 的过程消息，再用同一个出口发送事件。坏过程消息跳过。结尾消息异常时，按失败发生的位置给出明确的错误结果。
- 从同一份事件定义生成接口说明、网页类型和网页接收时的校验函数。网页收到第一条合法的结尾消息才结束接收。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `query-streaming`：定义公开事件格式、检查规则及结束条件，增加坏过程消息、坏结尾消息和编码失败的验收场景。
- `chat`：流式问答端点引用统一事件规则，明确 API 检查和转发的职责。
- `tool-calling`：工具调用进度采用同一公开事件格式，工具状态和摘要字段按现有工具调用规则产生。

## Impact

- `apps/api/kongming/chat` 负责解析 Agent 消息、组织公开事件和编码 SSE。Agent 与 API 的进程和凭证边界见 [ADR-002](../../../../docs/adr/002-trust-boundary.md)。
- `apps/web/src/lib/api.ts` 负责验证收到的消息并分发，现有聊天组件继续接收各类事件的数据。契约生成从现有 OpenAPI 入口扩展一项定向校验产物，见 [ADR-005](../../../../docs/adr/005-contract-pipeline.md)。
- 外部直接读取 `/chat/query/stream` 的客户端须按 `type` 分发。项目按学习环境安排一次切换，前后端及生成产物一起交付。

## Non-goals

- Agent 的内部流、问答图、工具执行、SQL 安全检查和请求鉴权属于现有能力的职责。
- 同步问答、历史保存、断线后继续执行和显式取消按各自规格验收。
- 回放、续传和逐词生成总结属于其他需求。
