# Tasks

## 0. 独立前端

- [ ] 0.0 抽 `packages/ui`/`packages/api-client` + pnpm workspace。验证：两 app 依赖共享包；无相对跨 app import/复制业务逻辑。
- [ ] 0.1 新建 `apps/admin` SPA（本地 `127.0.0.1:5174`）。验证：独立 artifact；不进入用户 web bundle。
- [ ] 0.2 `apps/api` admin auth `/admin-auth/keycloak/*` + `KM_ADMIN_*` 配置。验证：`kongming-admin-web` 回调/刷新/登出；不复用 `kongming-web`。
- [ ] 0.3 迁移 admin 页面/共享 UI/API 类型；`apps/web` 移除 admin 路由。验证：无重复页面/逻辑。
- [ ] 0.4 同源 `/api` 反代 + CSP。验证：不开放 CORS；`connect-src 'self'`；admin origin 可写、旧 origin 拒绝。
- [ ] 0.5 旧 `/admin/*` 308 去前缀到 `${admin_origin}`；`/admin` 到根。验证：深链可达；无 404/双前端。
- [ ] 0.6 `apps/api` admin 会话。验证：access 5m；refresh 30m；`km_admin_refresh` HttpOnly/SameSite=Lax/生产 Secure；无 remember me。
- [ ] 0.7 CI `apps/admin` lint/typecheck/test/build/e2e。验证：独立 job；artifact 可部署。

## 1. 控制台

- [ ] 1.1 服务域配置页（用户只读/角色/部门/团队/数据源/RAG/语义/工具/MCP/连接器/工作流/触发器/Bot/本地护栏）。验证：服务 admin API 写入、审计。
- [ ] 1.2 运行时页/只读投影。验证：共享控制只读；billing 移除/跳转锦官。
- [ ] 1.3 本地命令 + 跨服务命令状态。验证：本地不受控面影响；pending/acked/failed。
- [ ] 1.4 服务 admin API 仅本地写；服务只读 API 供锦官。验证：锦官无写入口；本地台写校验/落库/审计。

## 2. 权限与门禁

- [ ] 2.1 服务域配置权限/共享控制只读/审计。验证：非授权 403；共享控制写拒绝。
- [ ] 2.2 375/768/1280 × 亮暗。验证：无溢出。

## 3. 验收与独立运行

- [ ] 3.1 权限负向与跨租户用例。验证：未授权 401/403，跨租户 404/不可见。
- [ ] 3.2 审计断言。验证：每次写操作有审计记录；普通列表只读不写审计；敏感详情/导出按审计策略。
- [ ] 3.3 独立运行/降级 smoke。验证：停掉管理面或其他仓，本服务核心流程不受影响；管理面停服仅局部不可用。
- [ ] 3.4 契约与生成客户端。验证：OpenAPI/生成物无漂移；跨仓契约测试必跑（X-01）。
- [ ] 3.5 Greenfield 重置：删除/禁用旧控制数据与旧 spec 需求。验证：无兼容/双轨分支，旧端点/旧表不存在或只读历史。

## Workflow follow-up

- 归档前跑 `openspec validate --strict`；实现完成后按仓库门禁收口。
- 本 change 与 longzhong 聚合层 `docs/CONTROL-PLANE-PROJECT.md` 及 `docs/CONTROL-PLANE-BOUNDARY.md` 的对应 WP/边界表保持一致。