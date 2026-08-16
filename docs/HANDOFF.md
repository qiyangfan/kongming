# HANDOFF — Kongming 交接文档

> 最后更新:2026-08-16(001-foundation 归档后)。清空上下文/新会话先读本文与 `AGENTS.md`,
> 动敏感区前按 AGENTS.md 前置阅读表读对应 ADR。**本文行文只记录"代码里看不出来"的事实。**

## 项目一句话

企业级 Agent 数据平台(自然语言 → SQL → 执行 → 洞察),Python 双进程 + React SPA。
前身 `/Users/solvela/nl2sql-data-platform`(Java COLA,38 变更,仍在运行,**不动它**)。
知识资产已平移(41 域 spec → 本仓库 15 域 + roadmap 登记 22 域)。

## 环境事实(新会话最易踩的坑)

1. **本机是 podman,不是 docker**:`docker` 只是交互 shell 的 alias,非交互命令里
   `docker` 不存在——一律用 `podman`。
2. **标准端口**:api **8000** / agent **8001** / web dev **5173** /
   postgres **5432** / redis **6379**(均可配置,见「命令速查」)。本机同时存在旧项目
   (nl2sql-data-platform)容器,用户已宣布将停用;停用前**绝不触碰其容器**,
   若其占用上述端口则 Kongming 服务暂缓启动,待其释放。
3. **本地环境冷启动**(幂等,任何"连不上/端口不对"先跑这个):
   ```bash
   podman rm -f kongming-pg kongming-redis 2>/dev/null || true   # 清理旧容器
   podman run -d --name kongming-pg \
     -e POSTGRES_DB=kongming -e POSTGRES_USER=kongming -e POSTGRES_PASSWORD=kongming \
     -v $PWD/infra/pg-init:/docker-entrypoint-initdb.d:ro \
     -p 5432:5432 docker.io/pgvector/pgvector:pg17
   podman run -d --name kongming-redis -p 6379:6379 docker.io/library/redis:8
   cd apps/api && uv run alembic upgrade head
   ```
4. 三处 `.env` 已配好(不入库):根(占位密钥)、`apps/api/.env`(默认端口连接串 +
   `KM_INTERNAL_TOKEN=dev-internal-token`)、`apps/agent/.env`(同内部令牌;LLM key=dummy)。
5. 可能有三个后台 dev 进程在跑(api/agent/web),日志 `/tmp/km-{api,agent,web}.log`;
   重启前先清理(如 `pkill -f "uvicorn kongming"`、`pkill -f vite`)。
6. 本地 dev 流程:两个 Python 服务直接 `uv run uvicorn ...`(不走 compose);compose 文件
   是给完整部署用的,本机首次跑需 `podman compose` 适配(未实测)。

## 当前状态(截至奠基根提交 + 001 归档,单提交仓库)

- git:已 `init -b main`,单根提交(代码+治理+本归档+本文档一次成型);**无远程、未 push**(待用户指令)。
- openspec:001-foundation 已归档(本批文档未提交,工作区有未跟踪变更)。
- 质量:双侧 ruff/mypy strict/import-linter/pytest(12 用例)+ web tsc/eslint/build 全绿;
  CI 四 job 就绪(未在真实 runner 上跑过——push 后首验)。
- 全链路已实测跑通(healthz / 防火墙拦截 / 内部通道 / 登录页 / 代理链路)。
- 防火墙已可用且被测试锁死(AST 只读白名单,拦截 DROP/数据修改 CTE/多语句)。

## 已定死的决策(不再议,引用即终)

| 主题 | 结论 | 出处 |
|---|---|---|
| 技术栈 | Python 3.13 双进程(api 持凭证 / agent 零凭证)+ React 19 SPA;不引入 Keycloak/Next.js/微服务/消息队列/Litestar | ADR-001 |
| 信任边界 | 进程边界构造级;agent 禁 DB 凭证(env/驱动双禁);合并进程=违宪 | ADR-002 + 宪法 Article IV |
| 认证 | 自建(argon2id + PyJWT RS256 + authlib);登录页=前端路由;未来企业 SSO 走"引入 IdP 作下游"回退路径 | ADR-003 |
| Schema | baseline 制;UUID v7 / VARCHAR+CHECK / timestamptz;已规划能力列一次建全 | ADR-004 |
| 契约 | FastAPI OpenAPI 唯一事实源;前端类型生成禁手写;CI 漂移 diff | ADR-005 |
| 分层 | 域包内 router→service→models→repository;domain 零框架依赖 | 宪法 Article II |

## 下一步(路线图第一梯队,按序)

1. **认证闭环**(建议下一个 openspec 变更 `002-auth-loopback`):
   user store 接入(SQLAlchemy repository + Protocol)、`/auth/login` 真实实现、
   种子数据(rbac spec「种子数据」节)、**auth spec 与实现同步编写**(B 层唯一留白)、
   web 登录页接线 + token 存取。登录通了才能打通一切管理面。
2. `/internal/sql/execute` 执行路由:数据源连接池(默认 default 库即可起步)+
   审计落库 + data_scope 改写接入(datascope spec 测试清单 10 条先行)。
3. Agent LLM 接线(understand/generate/summarize)+ 回调 api 走通 → 第一条端到端问答。
4. 前端主布局(会话侧栏 + 对话区 + RBAC 菜单守卫)。

## 已知 TODO(代码内标注,非缺陷)

`auth/router.py` login/github_callback 501(user store);`internal/router.py` 执行路由;
agent graph 各节点 LLM;`web/App.tsx` 主布局;compose 的 podman 适配未实测。

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
