# 053-sql-guard-engine 提案:SQL 安全管线单遍引擎接入

## Why

sqlfw 防火墙三道闸(`check` / `check_schema_scope` / `check_reserved_tables`)、datascope 行级改写、agent validate 节点各自独立调用 sqlglot 解析,一次执行链对同一条 SQL 最多 parse 五次(`apps/api/kongming/sqlfw/firewall.py`、`apps/api/kongming/datascope/rewrite.py`、`apps/agent/kongming_agent/graph/nodes.py`),全部为纯 Python。HANDOFF 已记录 sqlglot 深嵌套 SQL 抛 RecursionError 的教训——Python 递归栈上限使嵌套深度不可控。

2026-09 生态调研结论(详情见 design):

- sqlglotrs(Rust tokenizer 加速)已于 2026-02 弃用;官方继任 sqlglotc(mypyc 编译)提供约 5x 解析加速——「换解析器求单次速度」路线已被官方以零代码改动覆盖,**不再作为本变更动机**;
- sqlglotc 不解决 RecursionError(仍是递归 Python 逻辑),深嵌套鲁棒性缺口仍在;
- sqlglot 自带 lineage 模块存在已知质量缺陷(CTE #2515、歧义退化 Placeholder #2510、原地改写输入 #6112 等),血缘无法以可信方式引入现管线;
- Rust 侧无产品级 SQL 防火墙/血缘 crate;sqlparser-rs 官方定位为纯语法解析器,防火墙语义需全部自研。

本变更引入**单遍 SQL 安全引擎**(独立新仓,进程内计算库):一次解析同时产出防火墙判定、schema 作用域/保留表校验与行级改写所需结构,消除五次重复解析;以显式深度上限将深嵌套从不可控 RecursionError 变为受控 ERROR verdict。接入采用「行为等价对拍 + sqlglot 降级 fallback + 影子灰度」,切换期间防火墙行为契约零变更。

## What Changes

1. api 与 agent 两进程新增进程内计算依赖 `km-sqlguard`(外部独立仓 wheel,纯计算,无网络、无凭证、无 DB 访问),作为 sqlfw / datascope / validate 的统一解析底座。
2. **sqlfw 重构为薄适配层**:`check` / `check_schema_scope` / `check_reserved_tables` 共享单次解析结果,判定规则逐条一比一移植,verdict 三态(PASS/BLOCKED/ERROR)语义不变。
3. **datascope rewrite 复用同一解析结果**:AST 注入最外层 WHERE 的行为与参数化绑定不变(纵深防御链路不变)。
4. **agent validate 节点切换引擎**:本地 AST 预检语义不变(纯解析、无 IO),仍先于执行闸。
5. **降级 fallback 为新行为契约**:引擎加载失败或运行期异常时自动降级至内置解析路径(sqlglot 现行实现),服务不中断;活跃引擎计入审计日志。
6. **影子对拍灰度**:先影子模式(引擎与内置路径并行判定、只记录不一致不生效)再切主,对拍不一致即切主门禁失败。

## Non-goals

- **血缘 API 暴露**:引擎具备列级血缘能力,kongming 侧消费(管理端点/审计视图/影响分析)另立 change——本变更只动执行管线,一个 change 一个能力域。
- **sidecar 进程形态**:引擎必须为进程内库;独立进程会引入新的信任边界与凭证面,违反 ADR-002(agent 零凭证、信任边界=进程边界)。
- **替换 sqlglot**:sqlglot 永久保留为 fallback 与对拍基线,退役不在本变更范围。
- **方言扩展**:PG-only 维持(ADR-001);多方言属引擎仓自身演进,与 kongming 集成解耦。
- **以单次 parse 速度为验收目标**:官方 sqlglotc 已覆盖该诉求;本变更验收点是消除重复解析与深嵌套受控,不承诺超越 sqlglotc 的单次速度。

## Capabilities

### New Capabilities

(无——引擎接入不产生用户可见新行为;唯一新增行为「引擎降级」归属既有 sql-execution 域,见 Modified。)

### Modified Capabilities

- sql-execution:新增「SQL 安全引擎降级」Requirement——引擎不可用时系统 SHALL 无感降级至内置解析路径,判定语义与 verdict 三态不变。

## Impact

- **落点包**:`apps/api/kongming/sqlfw/`(薄适配层)、`apps/api/kongming/datascope/rewrite.py`、`apps/agent/kongming_agent/graph/nodes.py`(validate 节点)、两进程 pyproject 依赖声明。
- **信任边界不变**:引擎为进程内纯计算库,不持凭证、不触网,ADR-002 边界不受影响;import-linter 契约(agent 禁 asyncpg/sqlalchemy/psycopg/redis)不放宽。
- **无 schema 变更**:不涉及 Alembic baseline(ADR-004)。
- **无 OpenAPI 契约变更**:无新端点,verdict 三态与错误信封不变(ADR-005 管线零触发)。
- **外部依赖**:引擎独立新仓(km-sqlguard,暂定名)交付 v0.1 方为本变更开工 gate;引擎仓自身设计与测试独立演进,不占本 change 任务。
