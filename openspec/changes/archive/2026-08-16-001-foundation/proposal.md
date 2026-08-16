# 001: Kongming 骨架奠基

## Why

nl2sql-data-platform(前身)经 38 个变更演进为成熟平台,但其技术底座
(Java COLA + Python 双语言三单元 + Keycloak)存在结构性成本:
跨语言契约税、Keycloak 主题镜像维护、50 个迁移中 15 个补丁的历史熵增。
经多轮对抗评审定案(ADR-001,55/45):以 Python 双进程 + React SPA 从零重建,
继承已验证的产品知识(41 域行为蓝图),不继承实现债务。

## What Changes

- 新仓库 Kongming(`/Users/solvela/kongming`),三服务骨架:
  - `apps/api` 控制面(FastAPI,持凭证,自建认证,sqlglot 防火墙,内部受控通道)
  - `apps/agent` Agent 面(LangGraph 8 节点,零凭证进程,ADR-002 构造级保证)
  - `apps/web` React 19 SPA(Vite + Tailwind v4,登录页即普通路由)
- Alembic 单一 baseline(15 表,前系统补丁链最终态整合,ADR-004 新约定:
  UUID v7 / VARCHAR+CHECK / created_at 族)
- 治理体系:宪法六条 + ADR-001~005 + tech-stack 锁定 + AGENTS.md
- openspec 15 域行为契约平移入库(A 层 12 + B 层 3,实现绑定剥离,
  枚举对齐新 baseline;auth 域刻意不平移,待与实现同步编写)
- CI 四门禁(ruff/mypy/import-linter/pytest ×2、web tsc/eslint/build、契约漂移 diff)
- 全链路实测跑通,12 测试用例,契约管线激活

## Non-goals

- 不迁移前系统数据(schema 全新设计,数据如需导出走独立 ETL)
- 不实现业务功能(user store/查询执行路由/LLM 接线均为标注 TODO 的占位)
- 不引入微服务/消息队列/Next.js(宪法与 ADR-001 否决清单)
- 不平移远期 22 域 spec(roadmap 登记,开工再立)
- 不建远程仓库、不 push(待用户指令)
