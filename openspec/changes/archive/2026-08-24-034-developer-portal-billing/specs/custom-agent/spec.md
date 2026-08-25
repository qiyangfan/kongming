## ADDED Requirements

### Requirement: 网关配额切面

OpenAI 兼容端点 POST /v1/chat/completions SHALL 在 Bot 执行前经过
配额切面(developer-portal-billing spec「用量配额」):超额 429
QUOTA_EXCEEDED 不落运行;预警经响应头标记;api-key 撤销/无效
401 语义不变。

#### Scenario: 超额网关拒

- GIVEN api-key 所属租户当月超限
- WHEN 调用 /v1/chat/completions
- THEN 429 QUOTA_EXCEEDED,不执行 Bot,落审计

#### Scenario: 未配置配额零回归

- GIVEN 租户无配额策略且未绑套餐
- WHEN 调用
- THEN 无配额判定,行为与切面引入前一致
