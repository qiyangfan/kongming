# Kongming · 孔明

> 问计于数据,如问计于孔明。
> 企业级 Agent 数据平台:自然语言 → SQL → 执行 → 洞察。

[![Tech](https://img.shields.io/badge/stack-Python%20%C3%972%20%2B%20React-059669)](docs/tech-stack.md)

## 架构一页纸

```
                    ┌───────────────────────────┐
  React SPA ───────▶│  apps/api   控制面(持凭证) │
  (apps/web)  JWT   │  FastAPI · RBAC · SQL 防火墙│──── PostgreSQL 17 + pgvector
                    │  审计 · 数据源注册          │──── Redis 8
                    └──────────┬────────────────┘
                     内部令牌   │ ▲ 回调 /internal/sql/execute(唯一执行路径)
                    ┌──────────▼────────────────┐
                    │  apps/agent Agent 面(零凭证)│──▶ LLM(通义/DeepSeek 可切)
                    │  LangGraph · RAG · selfheal │
                    └───────────────────────────┘
```

**信任边界 = 进程边界**(ADR-002):Agent 进程里不存在任何数据库凭证;
登录页是前端普通路由(ADR-003),不再是 Keycloak/PatternFly 的镜像定制;
契约由 OpenAPI 生成 + CI 漂移门禁锁定(ADR-005)。

决策记录:[docs/adr/](docs/adr/) | 技术栈宪法:[docs/tech-stack.md](docs/tech-stack.md)

## 快速开始

```bash
cp .env.example .env          # 填 KM_DB_PASSWORD / KM_INTERNAL_TOKEN / KM_AGENT_LLM_API_KEY
mise install                  # python 3.13 + node 22(或自备)

# 基础设施
docker compose -f infra/docker-compose.yml up -d postgres redis

# 控制面(含 Alembic baseline 建库)
cd apps/api && uv sync && uv run alembic upgrade head
uv run uvicorn kongming.main:app --port 8000

# Agent 面(另开终端;注意它没有也不需要任何 DB 配置)
cd apps/agent && uv sync
uv run uvicorn kongming_agent.main:app --port 8001

# 前端(另开终端)
cd apps/web && pnpm install && pnpm dev   # http://localhost:5173
```

## 常用命令

```bash
make check        # 全部质量门禁(ruff + mypy + pytest ×2 + tsc + eslint)
make contracts    # 导出 OpenAPI → 生成前端类型(提交前必跑)
make up / down    # compose 基础设施
```

## 路线图(承接已验证的产品蓝图)

对话问答(RAG 三路召回 / 多轮指代消解)→ 反馈精修闭环 → 动态数据源 →
云沙箱分析(NL2Insight)→ 工具调用 / MCP → Agent 市场。
