# Proposal: 2026-11-26-add-kongming-runtime-console（孔明管理台（runtime-console））

## Why

共享控制迁到锦官后，kongming 仍需服务域配置、运行时诊断、本地命令与只读投影；当前 `/admin` 与用户聊天面同处 `apps/web`，同 origin/session/CSP，管理面安全边界不足。

## What Changes

- 拆为独立 `apps/admin` SPA/域名，使用独立 Keycloak client `kongming-admin-web`。
- 复用 `apps/api` 现有 `/admin/*` 服务 admin API，不拆第二套后端。
- 服务域配置页：用户（只读）、角色/权限、部门/团队、数据源/RAG/语义/工具/MCP/连接器/工作流/触发器/Bot 定义/本地护栏规则。
- 运行时页：健康/日志/任务/命令/审计/投影。
- 共享控制只读；billing 控制页移除/跳转锦官。
- 本地运行命令直接执行；跨服务命令显示控制面 pending/acked/failed。
- 服务 admin API 供 `apps/admin` 写；服务只读 API 供锦官只读视图；旧 `/admin` 重定向/退役。

## Non-goals

- 不编辑共享控制（租户/成员/粗角色/套餐/权益/价目/目录/共享策略包）。
- 不拆后端 admin API/不新增后端 admin 服务。
- 不直连锦官/服务 DB。
- 不做通用 SQL 控制台。
- 不复制锦官控制台或业务逻辑。

## Capabilities

### New Capabilities

- `runtime-console`：kongming 独立管理台 UI

### Modified Capabilities

无

## Impact

新增 `apps/admin`；`apps/web` 移除 admin 路由/页面；`apps/api` 保持 `/admin/*`，由 admin 宿主同源反代，不开放 CORS；fujie 增加 `kongming-admin-web` client；服务只读 API 供锦官。
