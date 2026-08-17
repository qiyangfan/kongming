# 002 认证闭环(auth loopback)

## Why

骨架期 `/auth/login` 与 GitHub 回调均为 501 桩,无任何真实用户可登录;没有认证闭环,
后续一切管理面与业务面(RBAC 管理、问答链路、审计归属)都无从谈起。
同时 auth 是 B 层唯一留白域(spec 刻意与实现同步编写,避免重蹈旧系统 Keycloak 契约漂移),
ADR-006 已将五路联邦收敛为连接器架构,本变更落地其首发切片(框架 + GitHub/Google)。

## What Changes

- **user store 接入**:auth 域按分层纪律补齐 models / service / repository(SQLAlchemy async),
  持久化以 `typing.Protocol` 反转,领域层零框架依赖(宪法 Article II)。
- **账密登录真实实现**:argon2id 校验、禁用账号拒绝、连续失败 5 次锁定 15 分钟(Redis 计数)、
  首登强制改密标志、登录成功刷新 last_login_at。
- **refresh 轮换与吊销**:refresh token 携带家族标识,Redis 存家族指纹;
  轮换即作废旧票,旧票复用检测命中即吊销全家族(ADR-003);logout 吊销当前家族。
- **`/auth/me` 真实化**:返回用户、角色键、权限点(三表联查去重)、租户清单。
- **改密端点**:`POST /auth/password/change`(已认证自助),改密后吊销全部 refresh 家族。
- **连接器框架(ADR-006 首发)**:通用 OAuth2 内核 + provider 插件注册表 + 通用路由
  `/auth/{provider}/start|callback` + `GET /auth/options`;state 一次性校验(Redis+TTL+用后删)、
  PKCE 按家声明、oauth token 永不经过浏览器(前端收 code,后端交换);
  首发 GitHub / Google 两个插件,未配置的 provider 不出现在 options。
- **profile 归一化 + account_link**:各 provider profile 归一到
  `{external_id, username, display_name, email?, avatar_url?}` 后统一查/建号:
  按 (provider, external_id) 命中直接登录;未命中按已验证 email 关联既有账号;
  均未命中自动建号(绑默认角色与默认租户),全程记录操作日志。
- **0002 Alembic 迁移(ADR-004/006)**:新增 `public.user_identities(user_id, provider, external_id)`
  多身份表;`sys_user.auth_source/external_id` 数据迁入后删列(单一事实源);baseline 不动。
- **启动幂等种子(rbac spec「种子数据」)**:内置角色 SUPER_ADMIN/DEVELOPER、权限点注册表
  (与代码声明同源)、默认租户、超管账号(`KM_ADMIN_INITIAL_PASSWORD`,缺省用不安全默认值并
  强制首登改密);重复启动幂等,不覆盖线上改动。
- **auth spec 同步编写**:B 层留白补齐,行为契约与本实现同源交付。
- **web 登录接线**:refresh token localStorage 持久化 + access token 内存态、路由守卫、
  `/auth/{provider}/callback` 前端路由(收 code 交后端)、登录按钮数据驱动(`/auth/options`)、
  最小占位首页(当前用户信息 + 退出),登录闭环可见。

## Capabilities

### New Capabilities

- `auth`: 认证域行为契约——账密登录(含锁定/禁用/首登改密)、令牌签发与 refresh 轮换吊销、
  当前用户信息、自助改密、联邦登录(连接器形态、state/PKCE 安全清单、账号关联与自动建号)。
  (启动种子的行为契约归 rbac spec「种子数据」既有要求承载,本域不重复立约。)

### Modified Capabilities

无。rbac「OAuth(GitHub)账号权限一致」按字面仍成立(GitHub 继续支持);多 provider 的
联邦行为契约整体由 auth spec 承载,rbac 措辞泛化留待 003 国内三家接入时一并处理
(严守"一个 change 一个能力域")。

## Non-goals

- 不做 RBAC 管理面 CRUD(用户/角色/权限点/部门管理接口)——后续变更;本变更只交付种子与
  权限点注册表机制。
- 不做企业微信/钉钉/飞书三家——ADR-006 实施节奏明确属 003。
- 不做前端主布局(会话侧栏/对话区/菜单守卫)——仅交付登录闭环必需的最小守卫与占位首页。
- 不做 `/internal/sql/execute` 执行路由、Agent LLM 接线、租户白名单每请求校验、
  data_scope 行级改写——各有 spec,属后续变更。
- 不做 SAML/LDAP、不引入任何 IdP(ADR-003 已否决;回退路径不变)。
- 不做 OTP 双因素、密码历史策略等强化项(远期等保域,开工时再立 spec)。

## Impact

- **代码**:`apps/api/kongming/auth/`(models/service/repository/providers/stores/seed 全新,
  router 重写)、新增 `kongming/db.py`(engine/session)与 Redis 客户端工厂、`main.py`
  lifespan 接入种子、`config.py` 新增种子/锁定/Google 配置项。
- **Schema**:新增 `alembic/versions/0002_user_identities.py`(ADR-004:baseline 不改,演进走新迁移)。
- **契约**:`contracts/openapi.json` 重导出 + 前端类型重生成(ADR-005)。
- **前端**:`apps/web`(api 客户端 token 管理、auth store、路由守卫、Login 页、
  provider callback 路由、占位首页)。
- **CI**:api 门禁 job 增加 postgres(pgvector 镜像)+ redis services(安全规则测试需要真实库)。
- **依赖**:不新增运行时库(asyncpg/sqlalchemy/redis/authlib 均已在锁文件);测试不引入新框架。
- **环境**:`.env.example` 增补 `KM_ADMIN_INITIAL_PASSWORD`、Google OAuth 两项。
- **ADR 引用**:002(信任边界,不变)/ 003(自建认证)/ 004(迁移纪律)/ 005(契约管线)/ 006(连接器架构)。
