# HANDOFF — Kongming 交接文档

> 最后更新:2026-08-17(002 认证闭环落地并归档:真用户可登录,令牌生命周期完整,联邦连接器首发)。清空上下文/新会话先读本文与 `AGENTS.md`,
> 动敏感区前按 AGENTS.md 前置阅读表读对应 ADR。**本文行文只记录"代码里看不出来"的事实。**

## 项目一句话

全能 AI Agent 助手(数据问答/分析为首发王牌),Python 双进程 + React SPA。品牌 docs/brand.md。
前身 `/Users/solvela/nl2sql-data-platform`(Java COLA,38 变更,仍在运行,**不动它**)。
知识资产已平移(41 域 spec → 本仓库 15 域 + roadmap 登记 22 域)。

## 环境事实(新会话最易踩的坑)

1. **本机是 podman,不是 docker**:`docker` 只是交互 shell 的 alias,非交互命令里
   `docker` 不存在——一律用 `podman`。
2. **标准端口**:api **8000** / agent **8001** / web dev **5173**(见下注)/
   postgres **5432** / redis **6379**(均可配置,见「命令速查」)。本机同时存在旧项目
   (nl2sql-data-platform)容器与 dev 进程,用户已宣布将停用;停用前**绝不触碰其容器/进程**。
   **注(2026-08-17 实测):旧项目 vite 正占用 5173,Kongming web dev 自动落到 5174**;
   同理旧项目容器若占用上述端口则 Kongming 服务暂缓启动,待其释放。
3. **本地环境冷启动**(幂等,任何"连不上/端口不对"先跑这个):
   ```bash
   make up                                 # podman compose 起 postgres+redis(默认端口)
   cd apps/api && uv run alembic upgrade head
   ```
   pg 数据持久化在 compose 卷 `kongming_pg_data`;删卷/重置属破坏性操作,须用户明确授权。
4. 三处 `.env` 已配好(不入库):根(占位密钥)、`apps/api/.env`(默认端口连接串 +
   `KM_INTERNAL_TOKEN=dev-internal-token`)、`apps/agent/.env`(同内部令牌;LLM key=dummy)。
5. 可能有三个后台 dev 进程在跑(api/agent/web),日志 `/tmp/km-{api,agent,web}.log`;
   重启前先清理(如 `pkill -f "uvicorn kongming"`、`pkill -f vite`)。
6. 本地 dev 流程:基础设施(pg/redis)走 `make up`(podman compose 委托 docker-compose 二进制,
   已实测);两个 Python 服务日常 dev 直接 `uv run uvicorn ...`(compose 的 api/agent
   服务留给完整部署用)。

## 当前状态(截至 002 认证闭环归档)

- git:main 分支;**无远程、未 push**(待用户指令)。
- openspec:001-foundation、002-auth-loopback 均已归档;`openspec/specs/auth/` 已立约。
- 认证(ADR-003/006 落地):账密登录(argon2id + 失败 5 次锁 15min + 禁用/软删拒登 +
  首登强制改密)、refresh 家族轮换与复用吊销(Redis 三态)、logout、/auth/me 真实
  角色/权限点/租户、自助改密(吊销全家族)、连接器框架 + GitHub/Google 插件
  (state 一次性 + PKCE 按家声明 + verified-email 关联 + 自动建号绑 DEVELOPER/default 租户)。
  **轮换判定已原子化(Lua GETDEL+立墓碑单脚本)**:并发同票刷新仅一个胜者,败者按
  spec「复用即吊销家族」处理——多标签页毫秒级同票竞刷会触发重新登录,属契约既定语义;
  前端 bootstrap 已登录态短路,路由重挂载不再重复轮换(防误触发)。
- 启动幂等种子(lifespan):角色 SUPER_ADMIN/DEVELOPER、权限点注册表(system:user|role:manage)、
  默认租户 default、超管 admin(KM_ADMIN_INITIAL_PASSWORD 缺省 admin123 强制首登改密)。
- **dev 库现状**:冒烟后 admin 密码已被改为 `Kongming#2026`(非缺省;如需复位可删库重播或走改密接口)。
- schema:0001 baseline + 0002 user_identities(sys_user.auth_source/external_id 已删列收敛)。
- 质量:api ruff/mypy strict/import-linter/pytest 53(认证全栈集成测试,真实 PG+Redis)+
  agent 同门禁 6 + web tsc/eslint/build 全绿;CI api job 已带 pg/redis services(未在真实 runner 验证——push 后首验)。
- 契约:openapi.json 已含 /auth/* 全部端点,双出哈希一致;前端类型已重生成。
- 实测链路:curl 冒烟(登录→me→轮换→复用 401→改密→锁定 429→未配置联邦 404)+
  浏览器实测(登录页渲染→账密登录→首页占位→刷新会话恢复→登出)全通过。

## 已定死的决策(不再议,引用即终)

| 主题 | 结论 | 出处 |
|---|---|---|
| 技术栈 | Python 3.13 双进程(api 持凭证 / agent 零凭证)+ React 19 SPA;不引入 Keycloak/Next.js/微服务/消息队列/Litestar | ADR-001 |
| 信任边界 | 进程边界构造级;agent 禁 DB 凭证(env/驱动双禁);合并进程=违宪 | ADR-002 + 宪法 Article IV |
| 认证 | 自建(argon2id + PyJWT RS256 + authlib);登录页=前端路由;未来企业 SSO 走"引入 IdP 作下游"回退路径 | ADR-003 |
| Schema | baseline 制;UUID v7 / VARCHAR+CHECK / timestamptz;已规划能力列一次建全 | ADR-004 |
| 契约 | FastAPI OpenAPI 唯一事实源;前端类型生成禁手写;CI 漂移 diff | ADR-005 |
| 分层 | 域包内 router→service→models→repository;domain 零框架依赖 | 宪法 Article II |

## 下一步(路线图梯队,按序)

1. **003 国内三家联邦**(ADR-006 实施节奏):企微(通讯录权限)/钉钉(app 签名 quirks)/
   飞书(预取 app_access_token);顺手把 rbac spec「OAuth(GitHub)…」措辞泛化为 provider 无关,
   simple-icons 上包替代内联 SVG,补 favicon。
2. `/internal/sql/execute` 执行路由:数据源连接池(默认 default 库即可起步)+
   审计落库 + data_scope 改写接入(datascope spec 测试清单 10 条先行)。
3. Agent LLM 接线(understand/generate/summarize)+ 回调 api 走通 → 第一条端到端问答。
4. 前端主布局(会话侧栏 + 对话区 + RBAC 菜单守卫;require_perm 依赖随首个受保护端点交付)。
5. RBAC 管理面 CRUD(用户/角色/权限点/部门,spec 已立约)。

## 已知 TODO(代码内标注,非缺陷)

`internal/router.py` 执行路由;agent graph 各节点 LLM;web 主布局;favicon.ico 缺失(控制台 404,无害)。

## 命令速查

```bash
make check          # 全门禁(api/agent ruff+mypy+pytest + web tsc+eslint)
make contracts      # 导出 OpenAPI + 生成前端类型(改 api 后必跑)
# 服务: apps/api → uv run uvicorn kongming.main:app --port 8000
#       apps/agent → uv run uvicorn kongming_agent.main:app --port 8001
#       apps/web → pnpm dev(5173,proxy /api → 8000)
# 迁移: apps/api → uv run alembic upgrade head
# 冒烟: curl 127.0.0.1:8000/healthz;curl 127.0.0.1:8001/healthz
```

## 铁律提醒(全局,详见 ~/.zcode/AGENTS.md)

- git 写操作(add/commit/push)逐次等用户明确指令,只做字面授权那一步。
- 旧项目容器/卷/网络禁碰;破坏性操作前二次确认。
- 安全规则类改动测试先行;文档注释一律简体中文。
