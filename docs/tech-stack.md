# Kongming 技术栈锁定(宪法)

> 任何偏离需新增 ADR 并修订本文件。版本升级(同 major)允许,换库需 ADR。

## 运行时

| 组件 | 选型 | 锁定版本 |
|---|---|---|
| Python | CPython | 3.13(uv 管理) |
| Node | LTS | 22(pnpm 管理) |
| 数据库 | PostgreSQL + pgvector | 17 / 0.8 |
| 缓存 | Redis | 8 |

## apps/api — 控制面(持凭证)

| 维度 | 选型 |
|---|---|
| Web | FastAPI + Pydantic v2 |
| ORM | SQLAlchemy 2.0 async + asyncpg |
| 迁移 | Alembic(baseline 制,见 ADR-004) |
| 认证 | 自建:argon2-cffi + PyJWT(RS256)+ authlib(GitHub OAuth),见 ADR-003 |
| SQL 治理 | sqlglot(AST 防火墙 + 方言能力储备) |
| 限流 | 按需(nginx limit_req 起步,不预设库) |
| 可观测 | structlog(JSON)+ prometheus-client + OpenTelemetry(按需启用) |

## apps/agent — Agent 面(零凭证)

| 维度 | 选型 |
|---|---|
| 编排 | LangGraph |
| LLM SDK | openai 兼容客户端(通义/DeepSeek 可切) |
| 服务 | FastAPI(仅内网端口,内部令牌认证) |
| 纪律 | 进程内禁止任何 DB 凭证(ADR-002),import-linter 守护 |

## apps/web — React SPA

| 维度 | 选型 |
|---|---|
| 框架 | React 19 + TypeScript(strict) |
| 构建 | Vite(纯 SPA,无 SSR——ADR-001) |
| 样式 | Tailwind CSS v4 |
| 组件 | shadcn/ui(Radix 无头 + 代码自有) |
| 数据 | TanStack Query(服务端状态)+ Zustand(客户端状态) |
| 路由 | React Router v7 |
| 图表 | ECharts(echarts-for-react) |
| 契约 | openapi-typescript 生成,禁手写 API 类型(ADR-005) |

## 工程纪律

- Python:ruff(lint+format)+ mypy strict + pytest;uv 锁定
- Web:eslint + prettier + vitest;pnpm 锁定
- 提交前:`make check` 全绿
- CI 四门禁:api 测试 / agent 测试 / web 构建lint / 契约漂移 diff
