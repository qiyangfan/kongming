# 023 semantic-layer:语义层(表关系与指标口径)

## Why

NL2SQL 的两个结构性短板:JOIN 靠 LLM 猜列名(无 FK 约束的业务库尤甚)、
业务术语无口径(「复购率」「客单价」每次生成现场发挥)。本变更落地语义层:
表关系(JOIN path)自动 introspection+手工维护、指标口径结构化管理,
注入召回/生成链路;生成 SQL 仍由 LLM 产出并走全量防御(sql_template
永不直接执行)。017 评估失败归因中 JOIN 类错误是本域的量化靶点。

## What Changes

- **表关系 introspection**:按数据源从 information_schema 抓外键/主键
  (referential_constraints JOIN key_column_usage),幂等 upsert
  (source=introspection);手工维护关系(source=manual)不被 introspection
  覆盖;数据源删除级联清理;仅存单数据源内表间关系。
- **指标口径管理**:metric_definitions(数据源+指标名唯一;sql_template
  仅作 prompt 文字注入,永不进执行路径——最终 SQL 仍由 LLM 生成并走
  防火墙/只读池全量防御)。
- **生成链路注入**:RAG 召回响应纯增 relations_hint(仅召回表集合内的
  两两 JOIN path,不灌全库关系)与 metric_hints(问句命中业务术语的
  启用指标,top-5);agent generate 的 user 提示拼接两段(系统模板资产
  零改动,009/022 同款形态);无数据时零注入零回归。
- **管理端点与 UI**:/system/semantic/* 关系与指标 CRUD + 触发
  introspection;权限点 system:semantic:manage(排序 17);
  /admin/semantic 第十子页(关系 Tab:manual 可编辑/introspection 只读;
  指标 Tab:sql_template 带「仅作 prompt 参考」提示)。

## Capabilities

### New Capabilities

- `semantic-layer`: 表关系 introspection 与维护、指标口径定义、生成链路
  注入边界、管理端点的行为契约。

### Modified Capabilities

- `rag`: 召回响应纯增语义段(relations_hint/metric_hints)的行为契约。
- `nl2sql-agent`: generate 提示拼接两段的契约(系统模板零改动,空零回归)。

## Non-goals

- 不做语义层自动学习(LLM 从成功 SQL 反推关系);不做指标执行引擎
  (sql_template 永不执行);不做跨数据源关系。
- 不做关系可视化图(列表形态;图随 UX 深化)。
- 不改 009 召回融合算法(RRF);注入段在召回响应的增量字段,不改既有
  schema_hint/history_sql 语义。

## Impact

- **代码**:api 新域包 kongming/semantic/(models/ports/service/repository/
  router);rag retrieve 响应组装两段;agent apiclient RagContext 扩字段+
  nodes/react 拼接;alembic 0012(rag.table_relations+rag.metric_definitions);
  种子权限点;web /admin/semantic 子页。
- **契约**:/system/semantic/* 端点 + /internal/rag/retrieve 响应纯增字段。
- **依赖**:零新依赖。
- **ADR 引用**:ADR-002(内省经 api 侧只读连接,agent 零凭证)、ADR-004、
  ADR-005。
- **安全**:introspection 走既有只读连接与数据源归属校验;sql_template
  永不执行的架构守护=代码评审+测试断言(无 execute 路径)。
