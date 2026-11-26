# Design: 孔明管理台（runtime-console）

## Context

现有 `/admin` 页面混合了共享控制、服务域配置与运行时数据，并与用户聊天面同处 `apps/web`，同 origin/session/CSP。共享控制迁锦官后，服务域配置仍归 kongming；管理台应独立为 `apps/admin` 前端，但后端 admin API 仍归 `apps/api`。

## Goals / Non-Goals

**Goals:**
- 管理台独立构建/部署，独立域名/CSP/cookie/OIDC client。
- 服务域配置可管理，运行时诊断/本地命令可用。
- 共享控制只读；锦官通过服务只读 API/投影只读展示。
- 复用 apps/api `/admin/*` 与服务只读 API，不复制业务逻辑。

**Non-Goals:**
- 不拆后端 admin API/不新增后端 admin 服务。
- 不编辑共享控制。
- 不做通用 SQL。
- 不复制锦官控制台逻辑。

## Decisions

1. 新建 `apps/admin` SPA；独立 build/deploy/域名，独立 CSP/cookie。
2. 新增 Keycloak client `kongming-admin-web`；redirect/权限独立；不复用用户 `kongming-web` session。
3. 后端仍为 `apps/api` `/admin/*`；由 admin 宿主同源反代 `/api`，不开放 CORS；补 CSP/admin API audience；不拆第二套后端。
4. 抽 `packages/ui`（`@kongming/ui`）与 `packages/api-client`（`@kongming/api-client`）：共享设计系统原语、生成类型与 fetch/auth 适配器；`apps/web`/`apps/admin` 都依赖，不互相相对引用，不在包内放业务页面。
5. 页面按“服务域配置 / 运行时 / 共享控制只读”分组；用户页只读。
6. 服务域配置写入走服务 admin API；锦官只读视图走服务只读 API。
7. 本地运行命令直接执行；跨服务命令走控制面队列并显示 pending。
8. 服务/控制面不可用时显示 stale/pending；本地命令不受控面可用性影响。
9. billing 控制页移除/跳转锦官；本地只留用量/权益只读。
10. 旧 `apps/web` `/admin` 重定向到新管理台或退役；不保留双前端。

## 实施参数（v1）

| 项 | 值 |
| --- | --- |
| 本地 admin origin | `http://127.0.0.1:5174` |
| 生产 admin origin | 必须显式配置 `KM_ADMIN_PUBLIC_ORIGIN`（示例 `https://admin.kongming.example.com`） |
| 本地 API target | `http://127.0.0.1:8000` |
| Admin SPA 路由 | 根路径；`/users`、`/roles`、`/datasources` 等，不再带 `/admin` |
| API 访问 | `/api/*` 同源反代到 `apps/api`；不开放跨域 CORS |
| OIDC client | `kongming-admin-web`，confidential；scopes `openid profile email`；secret 只进 `.env` |
| 回调 | `${admin_origin}/auth/keycloak/callback` |
| 登出回跳 | `${admin_origin}/login` |
| Admin auth 端点 | 浏览器 `/api/admin-auth/keycloak/{start,callback,refresh,logout}`；后端同路径去 `/api` |
| Keycloak 粗角色 | `kongming-admin`（client role `kongming-admin-web`）；细权限仍本地 |
| 本地会话 | access 5m；admin refresh 30m（与 Keycloak idle 对齐）；`km_admin_refresh` HttpOnly、SameSite=Lax、Path=/、生产 Secure |
| CSP | `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'` |
| 旧入口 | `apps/web` `/admin/*` → 308 `${admin_origin}/{path}`；`/admin` → `${admin_origin}/` |
| 共享代码 | `packages/ui`/`packages/api-client`；pnpm workspace；两 app 依赖，不复制 |
| CI | `apps/admin` lint/typecheck/test/build/e2e；独立 artifact |

- 生产 origin 不写死仓库；DNS/TLS 由部署值提供，缺失即拒绝启动/构建。
- `kongming-admin-web` 与用户 `kongming-web` 分离；Cookie 按 host 隔离，名称再分离一层。
- 本地开发同样走 Vite `/api` 同源代理；不为 admin 打开 API CORS。

### 环境变量

| 变量 | 本地值/规则 |
| --- | --- |
| `KM_ADMIN_PUBLIC_ORIGIN` | `http://127.0.0.1:5174`；生产必填 |
| `KM_ADMIN_API_ORIGIN` | `http://127.0.0.1:8000`；由反代使用，不暴露给浏览器 |
| `KM_ADMIN_KEYCLOAK_CLIENT_ID` | `kongming-admin-web` |
| `KM_ADMIN_KEYCLOAK_CLIENT_SECRET` | `.env`/secret store，不入库 |
| `KM_ADMIN_KEYCLOAK_REDIRECT_URI` | `http://127.0.0.1:5174/auth/keycloak/callback`；生产 `${KM_ADMIN_PUBLIC_ORIGIN}/auth/keycloak/callback` |
| `KM_ADMIN_KEYCLOAK_POST_LOGOUT_REDIRECT_URI` | `http://127.0.0.1:5174/login`；生产 `${KM_ADMIN_PUBLIC_ORIGIN}/login` |
| `KM_ADMIN_KEYCLOAK_REQUIRED_ROLE` | `kongming-admin` |
| `KM_ADMIN_SESSION_TTL_SEC` | `1800`（30m）；不得大于 Keycloak idle |
| `KM_ADMIN_COOKIE_NAME` | `km_admin_refresh` |
| `VITE_ADMIN_ORIGIN` | 用户 web 旧 `/admin` 重定向目标；本地 `http://127.0.0.1:5174`，生产必填 |

## Risks / Trade-offs

- 与锦官控制台职责重叠 → 共享控制只读/服务域配置服务写。
- 只读视图与本地台 UI 漂移 → 服务只读 API + 生成客户端 + 契约测试。
- 独立前端引入同源反代/CSP/OIDC 配置复杂度 → 独立 client/e2e；不开放 CORS。
- 旧 `/admin` 书签/入口失效 → 重定向或明确退役。
- 误操作 → 确认/审计/权限。

## Migration Plan

greenfield：新建 `apps/admin`，迁移旧 `/admin` 页面/组件；`apps/web` 移除 admin 路由；后端 admin API/数据库不变；fujie 增加 `kongming-admin-web` client。
