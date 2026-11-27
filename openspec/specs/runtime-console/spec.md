# runtime-console Specification

## Purpose

孔明独立管理台：`apps/admin` 前端承载 kongming 服务域配置 + 运行时管理；共享控制只读；本地命令可用；后端仍 `apps/api` `/admin/*`，锦官只读展示服务只读 API/投影。

## Requirements

### Requirement: 独立管理前端

孔明管理台 SHALL 以独立 `apps/admin` SPA 构建/部署，使用独立域名、CSP、cookie 与 Keycloak client `kongming-admin-web`；SHALL 经同源 `/api` 反代调用现有 `apps/api` `/admin/*` 与 `/admin-auth/*`；SHALL 复用 `packages/ui`/`packages/api-client`，SHALL NOT 开放跨域 CORS、拆第二套 admin 后端、跨 app 相对引用或复制业务逻辑；旧 `apps/web` `/admin/*` SHALL 308 重定向或退役；admin API 契约/权限 SHALL 保持不变。

#### Scenario: 独立部署

- WHEN 构建管理台
- THEN 产出独立 `apps/admin` artifact，不进入用户 web bundle

#### Scenario: 独立登录

- WHEN 管理员登录管理台
- THEN 使用 `kongming-admin-web` 回调/会话，不复用用户 `kongming-web` 会话

#### Scenario: 后端不拆

- WHEN 管理台写配置
- THEN 仍经同源 `/api` 调用 `apps/api` `/admin/*`，无第二套后端/数据库

#### Scenario: 旧入口

- WHEN 访问旧 `apps/web` `/admin/users`
- THEN 308 到 `${admin_origin}/users`，不渲染旧管理页

#### Scenario: 会话隔离

- WHEN 管理员登录管理台
- THEN 使用 `km_admin_refresh`/`kongming-admin-web`，不复用 `km_refresh`/`kongming-web`

### Requirement: 服务管理台

孔明管理台 SHALL 提供服务域配置页（用户只读、角色/权限、部门/团队、数据源/RAG/语义/工具/MCP/连接器/工作流/触发器/Bot 定义/本地护栏规则）与运行时页（健康/日志/任务/命令/审计/投影）；共享控制 SHALL 只读；billing 控制页 SHALL 移除/跳转锦官；SHALL NOT 提供任意 SQL 入口；服务域配置写入 SHALL 经服务 admin API 本地完成，锦官不得代理写。

#### Scenario: 服务域配置

- WHEN ADMIN 编辑数据源/角色/工作流
- THEN 允许，服务落库并审计

#### Scenario: 共享控制只读

- WHEN 尝试编辑租户/套餐/价目
- THEN 无入口；服务端拒绝

#### Scenario: 管理域只读

- WHEN 锦官读取服务域配置
- THEN 通过服务只读 API/投影展示，不写、不落库

### Requirement: 管理域只读 API

服务 SHALL 提供只读 API/投影供管理域读取服务域配置与运行时摘要；SHALL 认证服务身份/管理员只读；SHALL NOT 返回凭据/token/密钥；SHALL NOT 提供写入口；服务宕机时管理域读 last-known 并标 stale。

#### Scenario: 只读读取

- WHEN 管理域以只读身份请求
- THEN 返回脱敏投影，带 observed_at/stale

#### Scenario: 敏感值不返回

- WHEN 读取含凭据配置
- THEN 脱敏，不返回原值

#### Scenario: 写拒绝

- WHEN 管理域尝试经只读 API 写
- THEN 无入口/405/403

### Requirement: 命令与回执

管理台 SHALL 支持本地运行命令（reindex/清缓存/取消任务）直接执行并审计；跨服务命令 SHALL 显示控制面 pending/acked/failed；命令 SHALL 幂等；控制面不可用 SHALL 不阻断本地命令。

#### Scenario: 本地命令

- WHEN 锦官不可用
- THEN 本地 reindex 仍可执行并审计

#### Scenario: 跨服务命令

- WHEN ADMIN 提交控制面命令
- THEN 显示 pending，完成后 acked/failed
