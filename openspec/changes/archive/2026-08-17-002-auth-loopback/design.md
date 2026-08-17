# 002 设计:认证闭环

## Context

现状(见 proposal.md - Why):`apps/api/kongming/auth/` 只有 tokens/password/github 三件工具与
501 桩路由;api 进程尚无任何 DB/Redis 接线(无 engine、无 session 依赖);baseline 0001 已建
identity 全表但 `sys_user.auth_source/external_id` 与 ADR-006 的多身份设计冲突;
web 端登录页存在但未接线(`api.post` 无 token 存取,`/auth/github/callback` 前端路由是空壳)。
约束:宪法 Article II 分层与领域零框架依赖;ADR-004 baseline 不改、演进走新迁移;
ADR-005 契约以 api 导出为唯一事实源;安全规则类测试先行(config.yaml rules)。

## Goals / Non-Goals

**Goals:**

- auth 域达到"真用户可登录、令牌生命周期完整、联邦可插拔"的可交付状态;
  行为与 `specs/auth/spec.md` 逐条对应,安全语义全部有测试锁死。
- 建立 api 的 DB/Redis 基础设施范式(session/client 依赖注入),供后续所有域复用。
- 启动种子一次到位(角色/权限点/默认租户/超管),空库即可登录。

**Non-Goals:**

- `require_perm` 权限点拦截依赖:002 无任何受权限点保护的业务端点,机制随首个消费者
  (RBAC 管理面变更)交付;本变更只交付权限点注册表与查询(`/auth/me`)。
- simple-icons 包引入:仅 2 个 provider 时按铁律例外条款内联官方 SVG;003 五家齐发时上包。
- HttpOnly cookie 化 refresh token:见 Risks,留作后续强化,本变更维持 body 传参形态。

## Decisions

### D1 域包结构(宪法 Article II 落地)

```
kongming/
  db.py                 engine + async_session + get_session 依赖(新)
  redis_client.py       Redis 单例 + get_redis 依赖(新)
  auth/
    models.py           User/Role/Tenant/Identity/NormalizedProfile 纯 dataclass + 枚举
    ports.py            全部 Protocol:UserRepo/RoleRepo/TenantRepo/IdentityRepo/OperLogRepo
                        + RefreshFamilyStore/LoginAttemptStore/OAuthStateStore(纯 typing)
    repository.py       SQLAlchemy 实现(唯一 import sqlalchemy 的 auth 文件)
    stores.py           Redis 实现(唯一 import redis 的 auth 文件)
    service.py          用例:login/refresh/logout/change_password/me/oauth_start/
                        oauth_callback(account_link)/seed —— 只依赖 ports,零框架
    providers/base.py   OAuthProvider 抽象 + 注册表 + NormalizedProfile 归一化契约
    providers/github.py providers/google.py   声明式插件(endpoint/scope/pkce/profile 映射)
    seed.py             权限点注册表 + 幂等播种
    router.py           FastAPI 适配层(薄):解析请求 → 调 service → 映射异常到状态码
    tokens.py password.py  沿用(tokens 扩展 refresh 家族声明)
```

service.py 不 import fastapi/sqlalchemy/redis(领域纯度);router.py 不做规则。
异常用领域错误类型(`LoginFailed/AccountDisabled/AccountLocked/TokenInvalid/...`),
router 统一映射 401/403/404/429/400。

### D2 refresh 家族指纹(ADR-003"复用检测即吊销全家"落地)

refresh JWT 增 `fid`(家族 UUID)+ `jti` 声明;Redis 三态:

| Key | 值 | 语义 |
|---|---|---|
| `auth:rt:{jti}` | fid(EX=refresh TTL) | 活跃票 |
| `auth:rt:x:{jti}` | fid(EX=原剩余 TTL) | 已轮换墓碑(复用检测依据) |
| `auth:rfam:bad:{fid}` | 1(EX=refresh TTL) | 家族吊销标记 |
| `auth:rfams:{uid}` | fid 集合 | 用户的家族索引(改密/全端登出用) |

轮换 = 校验家族未吊销 → `DEL auth:rt:{jti}` 命中则立墓碑、签发同 fid 新对;
未命中查墓碑,命中 = 复用 → 置家族吊销标记并 401;两皆无 = 过期/伪造 → 401。
logout/改密凭 fid(或 uid 索引)置吊销标记。access 不维护黑名单(15min 自然过期,ADR-003 既定)。
备选:单表 DB 存家族(否决——高频读写,kv 语义天然契合 Redis,且 ADR-003 已指名 Redis)。

### D3 登录锁定与防枚举

- 失败计数 `auth:fail:{username}`(INCR,首次失败设 EX 15min),≥5 置 `auth:lock:{username}`
  EX 900;锁定期一切尝试 429(含正确密码,与旧系统 037 强化语义一致);成功登录 DEL 计数。
- 用户名不存在也走同一计数键(按输入字符串),且用预置 dummy argon2id 哈希执行一次
  verify 拉平时耗——存在性不可由响应文案/时延/锁定行为推断。
- 禁用/软删账号:密码校验通过后再判状态 → 403(上游既有契约),不计失败数。

### D4 连接器框架(ADR-006 首发切片)

provider 插件 = 声明式数据 + 一个 profile 映射函数:`key/display_name/auth_url/token_url/
scope/pkce/configured(settings)/normalize(profile_json)→NormalizedProfile`。
注册表 `dict[str, OAuthProvider]`;路由 `/auth/{provider}/start|callback` 全部通用,
未知/未配置 provider 一律 404;`GET /auth/options` 返回已配置清单(前端按钮数据驱动)。
- state:`auth:ostate:{state}` → JSON{provider, code_verifier?},EX 600s,回调 `GETDEL`
  (一次性,Redis 8 原生支持);PKCE 按家声明(google=True,github=False)。
- 回调端点 `POST /auth/{provider}/callback`(body 传 code+state,与 refresh 同例防日志泄漏);
  前端 `/auth/{provider}/callback` 路由收 provider 回跳 query 后转 POST。
- account_link 单事务:identity 命中 → 用户;miss → email(verified)命中 → 绑定+登录;
  再 miss → 建用户(username 冲突加 `-{provider}`/序号后缀,bounded retry)+ 默认角色
  DEVELOPER + 默认租户 + identity + oper_log(注册/登录事件)。

### D5 0002 迁移(ADR-004/006)

新文件 `alembic/versions/0002_user_identities.py`,不动 0001:

```sql
CREATE TABLE public.user_identities(
  id UUID PK, user_id UUID NOT NULL REFERENCES sys_user(id),
  provider VARCHAR(16) NOT NULL CHECK (provider IN
    ('GITHUB','GOOGLE','WECOM','DINGTALK','FEISHU')),  -- 五家一次建全(ADR-004:已规划能力)
  external_id VARCHAR(128) NOT NULL, created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(provider, external_id), UNIQUE(user_id, provider));
-- 数据平移:INSERT ... SELECT id, auth_source, external_id FROM sys_user WHERE external_id IS NOT NULL
ALTER TABLE sys_user DROP COLUMN auth_source, DROP COLUMN external_id;  -- 单一事实源
```

downgrade 逆向(回加列 + 数据回迁 + 删表)。`UNIQUE(user_id, provider)` 保证一家一绑,
多绑语义由多行不同 provider 承载(spec「多身份绑定」)。

### D6 种子(D6a 注册表;D6b 幂等策略)

- 权限点注册表 = 代码常量清单(perm_code/perm_name/sort)。002 只声明 rbac spec 已立约的
  `system:user:manage`、`system:role:manage`;业务权限点随各自域变更增补同一清单。
- 幂等策略**只增不改**:角色/租户/权限点/超管/绑定按唯一键查缺补插;SUPER_ADMIN 的权限绑定
  增量补齐(新版新增权限点自动达超管),从不删改既有行(spec「不覆盖已改数据」)。
- 超管:`KM_ADMIN_USERNAME`(默认 admin)、`KM_ADMIN_INITIAL_PASSWORD`;缺省密码
  `admin123` 且 `password_changed_at=NULL`(强制首登改密,spec 明文);有 env 密码则视为已改密。
- 默认租户 `tenant_key='default'`;超管与联邦自动建号均绑此租户。
- 角色:data_scope/sql_scope —— SUPER_ADMIN=ALL/DML_DDL,DEVELOPER=SELF/SELECT_ONLY。

### D7 web 令牌存取与闭环路由

- refresh token 存 localStorage(跨刷新会话恢复),access token 仅存内存;启动时凭 refresh
  换发新对。api 客户端拦截 401 → 单飞(single-flight)刷新 → 重放原请求;刷新失败清态回登录页。
- zustand auth store:`status(loading/authed/guest)/user/permCodes/mustChangePassword`;
  路由守卫:未认证 → /login;must_change → /change-password;两者之外的 / 渲染占位首页
  (用户卡 + 退出按钮)。主布局仍是后续变更,本页只证闭环。
- 登录页按钮由 `/auth/options` 驱动;GitHub/Google 官方 SVG 内联(铁律例外条款)。

### D8 测试基建(安全测试先行的承载)

- conftest:独立库 `kongming_test`(会话级建库 + `alembic upgrade head` 程序化执行),
  用例级 TRUNCATE 隔离;Redis 用 db 15,用例级 FLUSHDB。
- provider 的 HTTP 交换(code→token→profile)在测试替身层 mock,OAuth 外呼不进测试。
- CI api job 增 services:postgres(pgvector 镜像,与 infra compose 同源)+ redis:8,
  注入 `KM_DATABASE_URL/KM_REDIS_URL` 指 service;测试即 CI 一等门禁。

## Risks / Trade-offs

- [auth 强依赖 Redis,宕机则登录/刷新/联邦全挂] → fail-closed 符合控制面定位;
  健康检查后续加 redis ping(本变更外)。
- [refresh 存 localStorage 有 XSS 暴露面] → 接受(旧系统同形态);前端 XSS 防线靠
  React 默认转义 + 后续 CSP;HttpOnly cookie 化列为后续强化(需跨站/代理配套)。
- [dev 兜底 JWT 密钥重启即失效,全员重新登录] → 既有 dev 行为,prod 走 fail-closed 必配密钥。
- [0002 删列不可逆] → downgrade 回加列;当前无生产数据,演进窗口安全。
- [联邦自动建号的 username 后缀策略可能撞名循环] → 确定性后缀 + 上限重试,超出报 500 并留审计。
- [种子只增不改,移除的权限点成行内死数据] → 接受(rbac spec 已声明运行时死数据无害);
  清理工具随 RBAC 管理面变更评估。

## Migration Plan

1. `make up`(pg/redis)→ `cd apps/api && uv run alembic upgrade head`(0001→0002)。
2. `make contracts` 重导出契约并重生成前端类型,随代码同提交(ADR-005)。
3. 部署顺序:api 先(迁移先行)→ web 后;回滚:`alembic downgrade 0001` + 回退代码。
4. `.env.example` 增补:`KM_ADMIN_USERNAME/KM_ADMIN_INITIAL_PASSWORD`、
   `KM_GOOGLE_CLIENT_ID/KM_GOOGLE_CLIENT_SECRET(/KM_GOOGLE_REDIRECT_URI)`。

## Open Questions

无。
