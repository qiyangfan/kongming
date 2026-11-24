# 流式问答规格差异

## MODIFIED Requirements

### Requirement: SSE 事件协议

`POST /chat/query/stream` SHALL 以 UTF-8 `text/event-stream` 返回问答事件。业务帧仅用 SSE `data:` 发送形如 `{"type":"…","data":{…}}` 的 JSON 对象，`type` 和 `data` 均必填。公开类型为 `stage`、`sql`、`summary`、`tool_call`、`result`、`error`。Agent 的 `trace` 诊断消息 SHALL 限于内部流。消费方 SHALL 忽略 SSE 注释行。

校验通过的 SQL SHALL 先于终态发送，自愈重试可多次下发，以最后一次为准。总结增量 SHALL 在终态之前发送。连接可写时，API SHALL 发送恰好一个结尾事件 `result` 或 `error`，发送后结束响应。成功、业务拒绝、业务失败和已取消的问答结果 SHALL 使用 `result`。传输或输出失败 SHALL 使用 `error`。

#### Scenario: 事件序列完整形态

- GIVEN 一次普通问答成功，SQL 校验通过且查询有结果
- WHEN API 发送完整事件流
- THEN 节点 `stage` 的 `running` 和 `done` 按执行进度发送，通过校验的 `sql` 先于 `summary` 和结尾的 `result`
- AND 每条业务帧的 `type` 都位于 JSON 中，`result` 是唯一的结尾事件

#### Scenario: 业务拒绝仍走 result 终态

- GIVEN 生成的 SQL 被安全策略拦截
- WHEN 流式响应结束
- THEN 唯一的结尾事件为 `result`，其 `data.success` 为 `false` 且包含友好说明
- AND 流中没有被拦截的 `sql` 事件或 `error` 事件

#### Scenario: 直接得出问答结果或问答已取消

- GIVEN API 已得到无需调用 Agent 的问答结果，或本轮收敛为已取消
- WHEN API 发送本轮结尾事件
- THEN 该事件使用与普通问答相同的 `result` 格式
- AND 已取消结果的 `data.verdict` 为 `CANCELLED`

## ADDED Requirements

### Requirement: 公开事件字段与接口说明

API SHALL 按 `type` 校验每条公开事件的数据。`stage` 的 `node` 为字符串，`status` 为 `running` 或 `done`，`retry_count` 为非负整数。`sql.data.sql` 和 `summary.data.delta` 各为字符串。`tool_call.data` 包含字符串 `tool_name`、`status` 和可选的字符串 `args_digest`、`result_digest`。`result.data` 与同步问答响应逐字段一致。`error.data` 包含友好字符串 `error` 及必填的 `reason_code`。接口说明 SHALL 声明 200 成功响应为事件流，并按 `type` 列出对应数据结构。

#### Scenario: 事件类型与数据必须匹配

- GIVEN API 准备发送 `type=stage` 的事件
- WHEN `data` 只含 `sql`，或 `retry_count` 为字符串 `"1"`
- THEN 该数据不能以 `stage` 发送

#### Scenario: 接口说明与实际响应一致

- GIVEN 流式问答端点生成的接口说明
- WHEN 查阅其 200 响应及逐条事件的数据定义
- THEN 成功响应只声明 `text/event-stream`
- AND 六种公开事件各有一个由 `type` 选定的数据结构，`result.data` 引用同步问答的响应定义

### Requirement: 无效的 Agent 过程事件跳过

API SHALL 在转发前检查 Agent 过程消息。未知事件、无法解析的数据以及与已知事件不匹配的数据 SHALL 被跳过，后续有效事件照常处理。只有通过检查的 `stage` 事件 SHALL 进入阶段记录。API SHALL 能用轮次标识和失败类别定位坏消息，且 SHALL NOT 记录或对外返回其原始内容。

#### Scenario: 坏过程事件夹在有效事件中

- GIVEN Agent 依次发送有效的 `stage`、字段错误的 `stage`、内部的 `trace` 和有效的 `result`
- WHEN API 转发并保存结果
- THEN 公开事件流中只有有效的 `stage` 和 `result`
- AND 阶段记录只包含有效的 `stage`，问答结果正常保存

### Requirement: Agent 流结束异常

API SHALL 采用 Agent 第一条有效的 `result` 或 `error`，并忽略其后的事件。两种事件的数据须为 JSON 对象，`result` 须含布尔型 `success`，`error` 须含字符串 `error`。如果先收到有效的 Agent `error`，或 Agent 流在有效结尾出现前中断或结束，API SHALL 发送 `reason_code=AGENT_UNAVAILABLE` 的友好 `error`。该轮尚未收敛且要求保存时，SHALL 按问答失败保存一次。显式取消与问答收尾的竞争按现有取消规则处理。

#### Scenario: Agent 正常关流但结果无效

- GIVEN 已开始流式响应，Agent 只提供了 `success` 为字符串的结果并结束
- WHEN API 完成本轮处理
- THEN 只发送一次 `AGENT_UNAVAILABLE` 错误结尾
- AND `persist=true` 时只保存一条失败应答，`persist=false` 时不保存消息

#### Scenario: 重复结尾或连接尾部异常

- GIVEN API 已收到有效 Agent `result` 并完成本轮结果处理
- WHEN Agent 再发过程事件、重复结尾或读流尾部发生连接异常
- THEN 网页只收到已确定的 `result` 作为结尾，且该轮只保存一次

### Requirement: 公开结尾事件无法发送

流开始后，公开结尾事件在转换、检查或编码时失败，或输出结束时没有结尾事件且连接仍可写，API SHALL 改为发送唯一的 `reason_code=STREAM_OUTPUT_FAILED` 的 `error`。事件 SHALL 使用预定的友好文案，且不可包含内部错误详情。该发送错误 SHALL NOT 改写已经保存的状态或触发第二次保存。

#### Scenario: 已保存的成功结果无法编码

- GIVEN 问答结果已经保存，准备发送的 `result.data` 含有不能编码的值
- WHEN API 尝试生成结尾事件
- THEN 网页收到一次合法的 `STREAM_OUTPUT_FAILED` 错误结尾
- AND 原成功结果仍可从历史查看，数据库没有新增失败应答

### Requirement: 网页只分发检查通过的事件

网页 SHALL 在运行时按接口定义检查收齐的每条 JSON 事件。未知或无效事件 SHALL 被跳过。收到第一条合法的 `result` 或 `error` 后，网页 SHALL 停止分发该请求的后续事件。未主动中止且连接关闭时仍无合法结尾，网页 SHALL 显示答案未完整返回。

#### Scenario: 空对象不能冒充结果

- GIVEN 网页收到 `{"type":"result","data":{}}`，随后收到合法的 `result`
- WHEN 网页处理这两条消息
- THEN 只把后一个合法结果交给页面，前一个结果不结束接收

#### Scenario: 一个网络分块内有多个结尾

- GIVEN 一个网络分块同时包含一条合法的结尾事件和后续的其他事件
- WHEN 网页处理该分块
- THEN 只分发到第一条合法结尾，后续事件不触发页面更新

#### Scenario: 跨网络分块的事件及心跳

- GIVEN 中文字符、换行或空行分隔符跨网络分块，且消息之间有心跳注释
- WHEN 网页读取流
- THEN 按 SSE 规则恢复每条完整的数据消息，注释不触发页面更新
