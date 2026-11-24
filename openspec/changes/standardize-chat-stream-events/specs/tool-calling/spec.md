# 工具调用规格差异

## MODIFIED Requirements

### Requirement: 工具调用流式契约

ReAct 问答的公开流 SHALL 按 `query-streaming` 规格发送 `type=tool_call` 的过程事件。其 `data` SHALL 包含工具名 `tool_name` 和 `status`，状态取值为 `running`、`done` 或 `rejected`。`args_digest` 和 `result_digest` 为可选的截断摘要。一次工具调用 SHALL 报告开始与完成或被拒绝。工具完成或被拒绝后，问答流 SHALL 继续至问答自己的结尾事件。

#### Scenario: tool_call 事件下发

- GIVEN ReAct 开始一次工具调用
- WHEN 工具开始执行，随后执行完成或被防御层拒绝
- THEN API 发送 `tool_call` 事件，先报告 `running`，再报告 `done` 或 `rejected`
- AND 网页继续接收本轮问答，直到收到合法的 `result` 或 `error`

#### Scenario: 老前端向后兼容

- GIVEN 网页与 API 使用相同的事件格式，网页认识的事件类型少于 API
- WHEN 网页收到无法识别的事件类型，随后收到合法的 `result` 或 `error`
- THEN 网页跳过未知事件，照常处理问答结尾
