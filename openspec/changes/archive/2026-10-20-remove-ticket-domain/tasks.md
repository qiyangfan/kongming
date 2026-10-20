# Tasks

## 1. 退役 ticket 运行时能力

- [x] 1.1 盘点目标部署中的四项 `KM_TICKET_*`/`KM_TOOLGATEWAY_TICKET_QUOTA` 配置与实际工具调用方，不记录凭证值；验证发布清单明确标出会失效的部署和工具名。
- [x] 1.2 先为遗留 `ticket_search`/`ticket_get`/`ticket_create` 直接调用及待确认 `ticket_create` 增加零出站回归测试；在当前实现上运行目标测试并确认其按预期失败。
- [x] 1.3 移除 ticket 专属配置与启动校验、工具注册/分发、网关执行器/策略器、客户端和配额，并将必要的拒绝/零出站回归保留在合适的测试文件；验证 API 相关目标测试通过，websearch 与通用 HTTP 连接器测试不回归。
- [x] 1.4 删除 ticket mock 服务和过时测试夹具，清理 `.env.example`、部署说明及 `docs/HANDOFF.md` 中的现行配置/操作步骤；用 `rg` 确认运行手册不再指导启用 ticket，同时保留必要的历史决策/审计说明。

## 2. 清理 ticket RBAC 数据

- [x] 2.1 先增加权限退役数据迁移测试，覆盖两项 ticket 权限与角色绑定被精确删除、无关权限/角色绑定及历史 `sys_tool_audit` 保留；在实现迁移前运行目标测试并确认失败。
- [x] 2.2 从权限种子移除 `tool:ticket:search` 与 `tool:ticket:manage`，新增精确范围 Alembic 数据迁移；验证升级只删除这两项及其绑定，保留其他权限和全部历史审计，并确认旧 API 实例退出后启动种子不会重新创建它们。
- [x] 2.3 更新 RBAC/发布说明，记录旧授权不映射到 `tool:http:call`、迁移前备份要求及恢复自定义角色绑定的回滚步骤；检查说明与实际迁移行为一致。

## 3. 集成验证

- [x] 3.1 运行 `make check`；验证 ticket 工具不再注册或注入，遗留直接调用和待确认操作均拒绝且零出站，并确认 websearch、HTTP 连接器、共享审计及工具确认流程保持正常。
- [x] 3.2 检查 `contracts/openapi.json` 与 Agent/API 回调契约无不必要变化，并核对 `tool-gateway` 主 spec 仅移除 ticket 专属行为、保留其他域及通用 HTTP 连接器要求。

## Workflow follow-up

- 在同时应用本变更与 `remove-siku-integration` 前，协调修正后者 `tool-gateway` delta 中把 ticket 写作剩余域的示例，再对两个变更分别执行严格 OpenSpec 校验。
- 完成实现和评审后归档本变更，并验证归档后的主 spec 与实际实现一致。
