## MODIFIED Requirements

### Requirement: 网关配额切面

OpenAI 兼容端点 POST /v1/chat/completions SHALL 在 Bot 执行前经过
配额切面(developer-portal-billing spec「用量配额」「Bot 维度配额」):
租户级与 Bot 级双路判定,任一超额 429 QUOTA_EXCEEDED(拒绝维度
文案带 scope 前缀)不落运行;预警经响应头标记;api-key 撤销/无效
401 语义不变。Bot 执行完成后,本次调用的 LLM 用量 MUST 在响应
返回客户端前入账(bot 链路请求内同步采集,见 multi-model-routing
spec)——配额判定的当月用量包含此前全部已应答调用。

#### Scenario: 超额网关拒

- GIVEN api-key 所属租户当月超限
- WHEN 调用 /v1/chat/completions
- THEN 429 QUOTA_EXCEEDED,不执行 Bot,落审计

#### Scenario: Bot 级超限网关拒

- GIVEN 当前 api-key 绑定 Bot 命中 BOT 级配额且已超限
- WHEN 调用
- THEN 429 QUOTA_EXCEEDED: BOT:<维度>,不执行 Bot,落审计

#### Scenario: 未配置配额零回归

- GIVEN 租户无配额策略且未绑套餐
- WHEN 调用
- THEN 无配额判定,行为与切面引入前一致

#### Scenario: 用量请求内入账

- GIVEN 一次正常网关调用(多次 LLM 调用)
- WHEN 响应返回客户端
- THEN 本次全部用量已落 sys_llm_usage(含 bot_id 归属),紧随其后的
  新请求配额判定可计及本次
