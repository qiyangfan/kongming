# Tasks

## 0. 独立前端

- [x] 0.0 抽 `packages/ui`/`packages/api-client` + pnpm workspace。验证：两 app 依赖共享包；无相对跨 app import/复制业务逻辑。
- [x] 0.1 新建 `apps/admin` SPA（本地 `127.0.0.1:5174`）。验证：独立 artifact；不进入用户 web bundle。
- [x] 0.2 `apps/api` admin auth `/admin-auth/keycloak/*` + `KM_ADMIN_*` 配置。验证：`kongming-admin-web` 回调/刷新/登出；不复用 `kongming-web`。
- [x] 0.3 迁移 admin 页面/共享 UI/API 类型；`apps/web` 移除 admin 路由。验证：无重复页面/逻辑。
- [x] 0.4 同源 `/api` 反代 + CSP。验证：不开放 CORS；`connect-src 'self'`；admin origin 可写、旧 origin 拒绝。
- [x] 0.5 旧 `/admin/*` 308 去前缀到 `${admin_origin}`；`/admin` 到根。验证：深链可达；无 404/双前端。
- [x] 0.6 `apps/api` admin 会话。验证：access 5m；refresh 30m；`km_admin_refresh` HttpOnly/SameSite=Lax/生产 Secure；无 remember me。
- [x] 0.7 CI `apps/admin` lint/typecheck/test/build/e2e。验证：独立 job；artifact 可部署。

## 1. 控制台

- [x] 1.1 服务域配置页（用户只读/角色/部门/团队/数据源/RAG/语义/工具/MCP/连接器/工作流/触发器/Bot/本地护栏）。验证：服务 admin API 写入、审计。
- [x] 1.2 运行时页/只读投影。验证：共享控制只读；billing 移除/跳转锦官。
- [x] 1.3 本地命令 + 跨服务命令状态。验证：本地不受控面影响；pending/acked/failed。
- [x] 1.4 服务 admin API 仅本地写；服务只读 API 供锦官。验证：锦官无写入口；本地台写校验/落库/审计。

## 2. 权限与门禁

- [x] 2.1 服务域配置权限/共享控制只读/审计。验证：非授权 403；共享控制写拒绝。
- [ ] 2.2 375/768/1280 × 亮暗。验证：无溢出。

## 3. 验收与独立运行

- [x] 3.1 权限负向与跨租户用例。验证：未授权 401/403,跨租户 404/不可见。
- [x] 3.2 审计断言。验证：每次写操作有审计记录；普通列表只读不写审计；敏感详情/导出按审计策略。
- [x] 3.3 独立运行/降级 smoke。验证：停掉管理面或其他仓,本服务核心流程不受影响；管理面停服仅局部不可用。
- [x] 3.4 契约与生成客户端。验证：OpenAPI/生成物无漂移；跨仓契约测试必跑（X-01）。
- [x] 3.5 Greenfield 重置：删除/禁用旧控制数据与旧 spec 需求。验证：无兼容/双轨分支,旧端点/旧表不存在或只读历史。


> 2026-11-27 实施备注:跨仓契约(X-01)与 fujie client/真实 Keycloak 联调未就绪,
> 对应任务保持未勾选并登记为阻塞;本仓机制与本地验证已完成。

本地证据:
- pnpm workspace(`apps/web`/`apps/admin`/`packages/ui`/`packages/api-client`);
  用户在 web、管理台在 admin,共享 UI/生成类型/请求与 auth 适配器,无跨 app 相对引用。
- `/admin-auth/keycloak/{start,callback,refresh,logout}`、`km_admin_refresh` 30m、
  `admin-auth/me`;`tests/test_adminauth.py` 覆盖 state 绑定、30m Max-Age、
  refresh 轮换、登出、用户面刷新拒绝管理台 cookie、角色闸 403。
- `infra/admin/nginx.conf`(同源 /api 前缀剥离、CSP connect-src 'self'、
  WebSocket/子协议)与 `infra/web/admin-redirect.conf`(308)有静态断言;
  `vite.config.ts` dev/preview 308 由 e2e 验证。真实反代部署未演练,标**待确认**。
- 管理台独立构建/CI job/artifact 已配置;GitHub Actions 账单仍可能阻塞 CI。
- 2.2 响应式与亮暗走查未完成自动截图/人工走查,标**待确认**(阻塞该条):
  改动页含 Login/Users/Control 与迁移页;需在 5174 + 亮暗两态逐档验收。
- fujie 尚未创建 `kongming-admin-web` client,真实 Keycloak 登录未联调,标**阻塞**;
  本仓以 fake provider 验证回调/会话语义。
