# Kongming 工作区指引

全能 AI Agent 助手(数据问答与分析为首发王牌)。Python 双进程后端 + React SPA。
品牌基调见 docs/brand.md(色彩 token 单源 index.css)。
**动手前必读**:敏感区改动前先读对应文档(见文末"前置阅读"),不要凭直觉改架构。

## 目录

```
apps/api/     控制面:FastAPI,持全部凭证(RBAC/租户/SQL 防火墙/审计/Alembic)
apps/agent/   Agent 面:LangGraph,零凭证,SQL 执行 100% 回调 api /internal/*
apps/web/     React 19 SPA:Vite + Tailwind v4;API 类型由生成获得,禁手写
infra/        docker-compose(pgvector/pg17 + redis + api + agent)、Dockerfile、pg-init
contracts/    openapi.json(入库,CI 漂移门禁)
docs/adr/     架构决策记录(为什么)
openspec/     spec-driven 工作流:config.yaml 硬约束 + constitution.md 宪法 + specs/ 行为契约
```

## 常用命令

```bash
make check          # 全量质量门禁 = api/agent 各自 ruff+mypy+pytest + web tsc+eslint
make contracts      # 导出 OpenAPI → 生成前端类型;改了 api 路由/模型后必跑并提交产物
make up / down      # compose 起/停 postgres redis
# apps/api:  uv sync && uv run alembic upgrade head && uv run uvicorn kongming.main:app --port 8000
# apps/agent: uv sync && uv run uvicorn kongming_agent.main:app --port 8001
# apps/web:  pnpm install && pnpm dev   (typecheck= tsc -b;lint= eslint)
```

## 架构铁律(违反即返工)

1. **信任边界(ADR-002,宪法 Article IV)**:apps/agent 不得出现任何数据库配置、
   凭证环境变量或 DB 驱动 import(pyproject import-linter 已禁 asyncpg/sqlalchemy/redis)。
   合并 api/agent 进程、给 agent 镜像注入 DB 变量,均属禁止项。
2. **域包分层**:新能力域按 `router(适配)→ service(用例)→ models(领域)→ repository(基础设施)`
   组织;领域代码(models/service 内规则)零框架依赖——不 import fastapi/sqlalchemy/redis,
   持久化用 `typing.Protocol` 接口反转。
3. **契约(ADR-005)**:web 与 agent 的类型一律生成(`pnpm contracts:gen`),手写 API 类型违规;
   `contracts/openapi.json` 必须与代码同步提交。
4. **Schema(ADR-004)**:新表/改表走新的 Alembic 迁移;已规划能力(有 spec)的列一次建全。
   约定:UUID v7 主键、状态列 VARCHAR + CHECK(禁 SMALLINT 魔数/PG ENUM)、
   timestamptz、软删仅 identity 域。
5. **技术栈锁定(docs/tech-stack.md)**:换库/升 major 需先写 ADR。不引入:
   Keycloak、Next.js/SSR、微服务中间件、消息队列、Litestar。
6. **前端样式铁律(docs/tech-stack.md「前端样式铁律」节)**:基础交互组件一律
   shadcn/ui 禁手搓;Tailwind 原子类为唯一样式语言(禁新建 css/内联 style);
   色值/圆角/字体只经 `@theme` 设计 token 引用,禁硬编码;图标用 lucide-react 禁 emoji;
   图表主题色板从 token 派生;品牌表现层(渐变/空态/动效)豁免但用色仍走 token。

## 工作流与约定

- 功能开发走 openspec 流程(`/opsx:propose` → apply → archive),提案必须有 Non-goals;
  涉信任边界/认证/schema/契约的变更须引用对应 ADR。
- 安全规则类(SQL 防火墙/RBAC/租户)测试先行:先写失败测试再实现。
- 文档、ADR、代码注释一律**简体中文**。
- `uv.lock` 与 `pnpm-lock.yaml` 必须入库(锁文件不进 .gitignore)。
- 仓库已 git 版本化(main 分支);git 写操作(add/commit/push 等)仍需用户逐次明确
  指令,只读查询(status/diff/log)允许。文档不写提交哈希、不谈提交次数/形态——
  git 层细节以 git log 为唯一事实源;纯决策/文档类小变更攒批随功能提交搭车,不单独成提交。
- `.env` 从 `.env.example` 复制;compose 对关键变量 `${VAR:?}` 强校验,缺失拒启是刻意的。

## 上游参考项目

前身:`/Users/solvela/nl2sql-data-platform`(Java COLA 双栈,38 变更验证过;全栈容器
可能仍在运行——**绝不触碰其容器/卷/网络**)。

| 场景 | 可参考 | 不可照抄 |
|---|---|---|
| RAG 三路召回 | 召回策略、RRF 融合、缓存失效逻辑 | Java 实现、双语言 HTTP 回调链 |
| SQL 防火墙 | 规则集与拦截场景(评估用例尤佳) | AST 库差异(JSqlParser→sqlglot) |
| 评估体系 | eval yaml 结构、Execution Match 匹配算法、gold 编写规范 | 目录结构 |
| Agent 链路 | LangGraph 8 节点流程、prompt 设计、selfheal 分类 | 回调方式 |
| 反馈闭环 | 状态机语义、精修闭环流程 | schema 旧约定(雪花 ID/SMALLINT/Flyway) |

纪律:参考**逻辑**不复制**代码**(禁止 import 上游);行为冲突时以 Kongming 的
specs/baseline 为准;上游 specs 取材指引见 docs/roadmap.md。

## 前置阅读(按改动区域)

| 改动区域 | 先读 |
|---|---|
| 认证/登录/用户 | docs/adr/003(自建认证,不用 Keycloak)+ 006(联邦连接器) |
| api↔agent 交互、部署单元 | docs/adr/002(信任边界) |
| 数据库表结构 | docs/adr/004 + apps/api/alembic/versions/0001_baseline.py(演进走新迁移) |
| 前后端契约/加接口 | docs/adr/005 + make contracts |
| 任何"要不要引入 X" | docs/adr/001(含否决清单)+ openspec/constitution.md |
