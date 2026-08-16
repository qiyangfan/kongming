# 项目宪法 (Project Constitution) — Kongming

> 不可破坏的核心原则。与 `config.yaml` 的关系:config.yaml 是"硬约束清单"
> (注入每次 AI 请求),本文件是"为什么"的阐释。冲突时以 config.yaml 的
> 清单为准、本文件的意图为准绳。修改任何条款须同步更新对应 ADR(`docs/adr/`)。
>
> 前身:nl2sql-data-platform 宪法(38 个变更验证过的纪律,本文为其 Python 化迁移;
> 旧文中的 Java/COLA 语境条款已重写,意图不变)。

---

## Article I — 演进式架构 (Evolutionary Architecture)

**规则**:模块化单体起步。`apps/api` 与 `apps/agent` 两个进程的存在理由是
**信任边界(ADR-002),不是微服务化**;api 内部按域分包(chat/auth/datasource…),
不预设服务拆分,不引入注册中心/网关/分布式事务。

**为什么**:微服务的价值是"团队并行 + 独立部署",单人推进时价值为零,
却要每天付分布式调试的成本。拆分是一次性成本,且域包边界即未来拆分线。
注意与旧项目的差异:旧项目"双进程"是语言边界(Java+Python)附带的安全收益;
本项目双进程是**刻意设计的安全构造**,微服务化与否与之无关。

**反模式**:第一周就上 5 个服务 + 网关;或反过来,以"简化部署"为由合并
api/agent 进程(见 Article IV)。

## Article II — 领域纯净 (Domain-Pure)

**规则**:领域逻辑(models + service 内的规则)零框架依赖。SQLAlchemy、FastAPI、
Redis、LangGraph 的类型与异常不得出现在领域代码里。领域需要的持久化/外部能力
用 `typing.Protocol` 接口表达,实现在 repository/基础设施侧,import-linter 守护。

**为什么**:领域规则(权限判定、SQL 防火墙、租户隔离)是核心资产。耦合框架
就无法脱离容器做快速确定性单测——安全规则恰恰需要高频、快、确定的测试。
这是旧宪法 Article II 的 Python 化:Spring 换成了 SQLAlchemy/FastAPI,依赖倒置
的手段从 Gateway 接口换成了 Protocol + FastAPI Depends。

**反模式**:在防火墙/权限规则函数签名里出现 `sqlalchemy.orm.Session`;
为了"少写一个 Protocol"让 domain 直接 import infra。

## Article III — 测试先行 (Test-First, Non-Negotiable)

**规则**:安全规则类(SQL 防火墙、RBAC、租户、data_scope)必须先写失败测试(红)
再实现(绿)。分层依赖方向用 import-linter 契约锁定,进 CI。

**为什么**:AI 辅助编码时代,测试是"规格的可执行表达"——测试不过即实现偏离
spec。旧项目 81 个 Java 测试 + ArchUnit 七条守护是这套纪律的实证,本项目的
对应物是 pytest + import-linter 契约(已写入两个 pyproject)。

## Article IV — 信任边界不可降级 (Trust Boundary,构造级)

**规则**:`apps/agent` 进程中不存在任何数据库凭证(环境变量/配置文件/密钥挂载
均禁止),SQL 执行与 RAG 检索 100% 经控制面 `/internal/*` 回调。凭证隔离靠
**进程边界**这一构造级保证;import-linter 只是纵深的一层,不是边界本身。

**为什么**:进程边界 = 凭证物理不可达,prompt 注入打穿到 RCE 的泄露面也只有
最小权限内部令牌;lint 边界 = 约定级保证,动态 import / os.environ 可绕过。
产品路线图含云沙箱(执行模型产出物),"模型产出物不可信"是默认威胁模型。
详见 ADR-002(本条为该 ADR 的宪法化)。

**反模式**:以"减少一个部署单元"为由合并进程;给 agent 镜像注入"临时调试用"
DB 环境变量。

## Article V — 契约单一来源 (Single Source of Contract)

**规则**:三方(api/agent/web)契约唯一来源是 `apps/api` 的 OpenAPI schema。
前端与 agent 的类型一律生成(`contracts:gen`),手写 API 类型视为违规;
`contracts/openapi.json` 入库,CI 漂移门禁(`git diff --exit-code`)。

**为什么**:契约漂移是双进程 + SPA 结构下的头号隐性债(旧项目曾为之专项还债,
038)。生成管线把"可购买的能力"自动化,几小时落地、永久生效。详见 ADR-005。

## Article VI — Schema 纪律 (Baseline-Only)

**规则**:schema 变更走 Alembic 迁移;已规划能力(有 spec 的)建表时一次到位,
不留追补。状态列 VARCHAR + CHECK(禁 SMALLINT 魔数与 PG ENUM),主键 UUID v7,
软删仅 identity 域。

**为什么**:旧项目 50 个迁移中 15 个是补丁,同域连补三次(guardrail V38→V44)
是真实教训。详见 ADR-004。

---

## 治理三层(速查)

| 层 | 文件 | 回答 | 变更方式 |
|---|---|---|---|
| 决策 | `docs/adr/` | 为什么这么做 | 新增 ADR,旧的不删只标状态 |
| 契约 | `openspec/specs/` | 系统应如何行为 | change 的 deltas sync 而入 |
| 提案 | `openspec/changes/` | 这一步做什么/不做什么 | propose → apply → archive |
