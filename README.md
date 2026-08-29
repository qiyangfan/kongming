# Kongming · 孔明

> 万事,问孔明。
> 全能 AI Agent 助手:数据问答与分析(NL → SQL → 洞察)是首发王牌场景。
> 品牌:[docs/brand.md](docs/brand.md) · Crafted by Solvela(漆阳帆)

[![Tech](https://img.shields.io/badge/stack-Python%20%C3%972%20%2B%20React-8f2d2f)](docs/tech-stack.md)

## 界面速览

### 问数:自然语言 → SQL → 图表 → 洞察

问一句人话,得到带图表的结果:每条 SQL 全透明、可编辑再执行,支持柱状/折线/面积/散点切换、CSV/Excel 导出,并附洞察解读与统计摘要;答不上来时如实说明,不会编造数据。

![问数结果:问题、柱状图、结果表](docs/screenshots/chat-answer-top.png)

| 空态即入口:按租户与数据源生成建议问题 | 过程全透明:SQL 可编辑、可重新执行 |
|---|---|
| ![对话空态与建议问题](docs/screenshots/chat-empty.png) | ![SQL 透明可编辑再执行](docs/screenshots/chat-sql.png) |

### 知识与护栏:让 Agent 答得准、行得正

| 知识库:表业务术语 + 文档上传,双路增强 | 护栏:注入拦截 / PII 脱敏 / 输出防护 | 审计:每次执行留痕,判定与耗时可查 |
|---|---|---|
| ![知识库](docs/screenshots/admin-rag.png) | ![护栏规则](docs/screenshots/admin-guardrails.png) | ![SQL 审计](docs/screenshots/admin-audit.png) |

### 运营与开放:用量可算、调度可靠

| 模型用量:调用 / Token / 延迟按任务拆解 | 触发器:档位式定时调度或 webhook,预览未来执行 | 登录:本地账密 + GitHub / Google / 钉钉 / 飞书 |
|---|---|---|
| ![模型用量](docs/screenshots/admin-usage.png) | ![触发器调度档位](docs/screenshots/admin-trigger-dialog.png) | ![登录页](docs/screenshots/login.png) |

## 架构一页纸

```mermaid
flowchart LR
    web["React SPA · apps/web"] -->|"JWT"| api
    api["apps/api · 控制面(持凭证)<br/>FastAPI · RBAC · SQL 防火墙<br/>审计 · 数据源注册"]
    agent["apps/agent · Agent 面<br/>零凭证<br/>LangGraph · RAG · selfheal"]
    api -->|"内部令牌"| agent
    agent -->|"回调 /internal/sql/execute<br/>(唯一执行路径)"| api
    api --> pg[("PostgreSQL 17 + pgvector")]
    api --> redis[("Redis 8")]
    agent --> llm["LLM(通义 / DeepSeek 可切)"]

    classDef proc fill:#faf3ee,stroke:#8f2d2f,stroke-width:1.2px,color:#3d2b28
    classDef ext fill:#f5f2ec,stroke:#8a8577,color:#3d2b28
    class web,api,agent proc
    class pg,redis,llm ext
```

**信任边界 = 进程边界**(ADR-002):Agent 进程里不存在任何数据库凭证;
登录页是前端普通路由(ADR-003),不再是 Keycloak/PatternFly 的镜像定制;
契约由 OpenAPI 生成 + CI 漂移门禁锁定(ADR-005)。

决策记录:[docs/adr/](docs/adr/) | 技术栈宪法:[docs/tech-stack.md](docs/tech-stack.md)

## 快速开始

```bash
cp .env.example .env          # 填 KM_DB_PASSWORD / KM_INTERNAL_TOKEN / KM_AGENT_LLM_API_KEY
mise install                  # python 3.13 + node 24(或自备)

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
