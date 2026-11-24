# 聊天规格差异

## MODIFIED Requirements

### Requirement: 流式问答端点

系统 SHALL 提供 `POST /chat/query/stream`。认证、`chat:execute` 权限、`X-Tenant-Id` 租户白名单、数据源预检、会话、历史补全和用户消息保存 SHALL 与同步问答共用一套流程。响应 SHALL 为 SSE，带 `Cache-Control: no-cache`。API SHALL 按 `query-streaming` 规格检查和转发 Agent 过程事件。API SHALL 完成本轮的结果保存处理后发送 `result`，`result.data.message_id` 为已保存应答的标识，结果未保存时为 `null`。异常流 SHALL 按 `query-streaming` 规格处理。

#### Scenario: 流式问答全链

- GIVEN 已登录用户角色含 `chat:execute` 且租户已绑定
- WHEN 用户提交流式问答
- THEN 阶段事件逐个到达，通过校验的 `sql` 事件先于结尾
- AND `result.data` 与同步端点响应同构，`message_id` 引用该轮保存的应答
- AND 用户消息和应答正常保存

#### Scenario: 流开始前校验失败

- GIVEN 会话不存在或数据源非法或租户未绑定
- WHEN 用户提交流式问答
- THEN 返回常规 JSON 错误响应，与同步端点的响应状态码及 `detail` 一致
