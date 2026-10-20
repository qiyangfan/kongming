# Spec Delta: 工具网关

## ADDED Requirements

### Requirement: 已退役工单工具请求拒绝

系统 SHALL 拒绝对已退役工单工具的直接调用与待确认操作；拒绝时 MUST NOT 发出外部请求。

#### Scenario: 直接调用已退役工具

- **GIVEN** 请求仍携带 `ticket_search`、`ticket_get` 或 `ticket_create` 工具名
- **WHEN** 控制面收到该调用
- **THEN** 调用被拒绝并记录审计，且没有外部请求

#### Scenario: 确认已退役的创建操作

- **GIVEN** 存在待确认的 `ticket_create` 操作
- **WHEN** 用户提交确认
- **THEN** 操作被拒绝并标记为 `REJECTED`，且没有外部请求

## REMOVED Requirements

### Requirement: 内部系统连接器域注册与服务凭证

**Reason**: 该要求只定义基于仓库 mock 协议的 ticket 专属域；当前没有已验证的真实工单系统适配，专用端点、服务凭证及域注册行为随该能力退役。

**Migration**: 删除部署中的 `KM_TICKET_BASE_URL`、`KM_TICKET_API_KEY`、`KM_TICKET_TIMEOUT_MS` 与 `KM_TOOLGATEWAY_TICKET_QUOTA`；如仍需工单访问，由管理员显式配置通用 HTTP 连接器及其出口与凭证策略。不得自动复用旧部署级凭证。

### Requirement: 预置工具语义与响应标准化

**Reason**: 三个预置工具及工单卡字段映射只适用于 mock 定义的私有协议，且其通用 HTTP 出站与写确认基础能力已由通用连接器提供。

**Migration**: `ticket_search`、`ticket_get` 与 `ticket_create` 不再注册或注入；需要替代操作的租户须显式创建连接器并授权。遗留直接调用和待确认创建请求按“已退役工单工具请求拒绝”处理。

### Requirement: ticket 域授权、审计与配额

**Reason**: ticket 工具不再执行，专属 RBAC 权限点与独立配额失去行为主体；共享工具审计能力仍由其他域使用。

**Migration**: 删除 `tool:ticket:search`、`tool:ticket:manage` 权限点及其角色绑定，不迁移到 `tool:http:call`；保留既有工单审计记录，不删除或改写历史审计数据。
