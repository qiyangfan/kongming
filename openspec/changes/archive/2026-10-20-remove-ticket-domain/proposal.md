# Proposal

## Why

当前 `ticket` 域实现的是仓库自定义的三端点工单协议，mock 服务是唯一可验证的对接对象，真实工单系统适配路径尚未验证。它又重复提供通用 HTTP 连接器已有的受控出站与写操作确认能力，继续维护专用协议、凭证、配额和权限点会让用户误以为系统已具备真实工单集成。

## What Changes

- **BREAKING**：退役 `ticket_search`、`ticket_get`、`ticket_create`，移除 ticket 专属端点/服务凭证配置、网关适配器、mock 服务、配额与权限点；旧部署变量不再生效。
- 清理数据库中仅属于 ticket 的 RBAC 权限点及角色绑定；保留历史工具审计记录，不将旧权限自动映射为通用 HTTP 调用权限。
- 保留通用 HTTP 连接器及其租户隔离、出口校验、凭证治理、写操作确认、审计和配额等共享机制；需要连接内部工单系统的部署须另行显式配置通用连接器。
- 从 `tool-gateway` 行为契约中移除 ticket 专属要求。无 ticket 配置时，遗留工具名的直接调用及待确认调用仍须拒绝，且不得产生出站请求。
- 不变更 Agent 信任边界或 REST/OpenAPI 契约；按 ADR-002 保持凭证仅在控制面，按 ADR-003 治理权限点；不新增数据库 schema（ADR-004）或 REST 契约（ADR-005）。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `tool-gateway`：移除 ticket 专属域注册、预置工具语义、授权/审计/配额要求；保留其余域和通用连接器行为。

## Impact

- 主要影响控制面配置、启动校验、工具注册与分发、网关适配、RBAC 种子/数据迁移、mock 服务及相关测试和接手文档。
- 使用旧 `KM_TICKET_*` 配置或 `tool:ticket:*` 权限点的部署将失去三项工单工具；历史 `sys_tool_audit` 数据保留。通用 HTTP 连接器权限更宽，迁移不得自动授予或转换旧授权。
- 需要清理的数据库数据仅限 `tool:ticket:search`、`tool:ticket:manage` 两个权限点及其角色绑定。回滚代码本身不能恢复已删的自定义角色绑定；部署回滚须恢复迁移前数据库备份，或由管理员按原授权清单重建绑定。
- `remove-siku-integration` 的在途 `tool-gateway` delta 仍把 ticket 作为剩余域示例；合并两个变更时须同步校正该示例，不能把它当成 ticket 能力继续保留的依据。
