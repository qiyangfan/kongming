# Proposal: 110-metric-visibility-data-scope

## Why

指标口径(`rag.metric_definitions` 的 sql_template/business_terms)是高价值提示词资产,也可能含敏感业务信息(如「VIP 客户复购率」的口径本身暴露客群规则)。当前注入仅按 tenant+datasource 过滤,租户内所有调用者可见全部口径。该治理项自 095 起登记为远期(docs/roadmap.md「指标(metric_definitions)可见性按 data_scope 过滤」),触发条件=多团队共享数据源上线,随 102(知识团队归属)/103(配额 user-team)落地已成立;owner 于 2026-10-12 裁决解禁(求职项目,有价值能力尽快实现,不设远期搁置)。

## What Changes

- `rag.metric_definitions` 纯增两列(新迁移,零数据回填):`visible_scope VARCHAR CHECK ('ALL','DEPT') NOT NULL DEFAULT 'ALL'`(可见范围档位)与 `dept_id UUID NULL`(DEPT 档的归属部门,FK sys_dept);缺省 ALL=存量行为零回归
- 管理端指标 CRUD(`MetricIn`/`MetricOut`)纯增两字段并做「DEPT 档必填 dept_id」校验;契约再生(ADR-005);管理列表/详情不做可见性过滤(管理者全量管理视角,与 102 管理面同口径)
- `/internal/rag/retrieve` 的语义 enrich 按调用者 data_scope 过滤注入面:调用者有效范围=其启用角色的 most_permissive;ALL=全量;DEPT/DEPT_AND_BELOW/CUSTOM=`visible_scope='ALL'` ∪ (`visible_scope='DEPT'` 且 `dept_id` ∈ 调用者部门集合);SELF=仅 ALL 档;无身份/用户不存在/无启用角色=仅 ALL 档
- CUSTOM 空部门集合按**读面语义**降级为「仅 ALL 档」(不 fail-closed、不阻断召回;与 083 执行面 fail-closed 的语义分层见 design)
- 判定下推仓储 SQL 单点(`list_metrics` 增可见性过滤参数),沿用 102「SQL 同判投影」纪律
- 新窄端口 `CallerDeptScopeResolver`(semantic 域零 datascope import,组合根注入适配器;实现复用 datascope repository 的 most_permissive / custom_dept_ids / dept_and_below)
- enrich 位于 ContextCache 之外(076 后地形),缓存键零改动

## Capabilities

### New Capabilities

(无——本变更是既有能力域的行为收窄,不引入新能力。)

### Modified Capabilities

- `semantic-layer`:两个 Requirement 修订——「指标口径定义」增可见范围字段的管理语义(visible_scope/dept_id、DEPT 档必填校验、管理面全量视角);「生成链路注入」增指标可见性子句(按调用者 data_scope 收窄注入面)及对应场景。076 的结构化命中派生语义不变,只是命中的候选集先过可见性

## Impact

- **apps/api**:新迁移 0053(纯增列);`semantic/{models,ports,repository,service}.py`、`semantic/router.py`(MetricIn/Out+校验+组合根适配器)、`rag/router.py`(enrich 调用点传 caller_user_id,一行);测试 `tests/test_semantic.py`/`test_metric_provenance.py` 新增用例
- **契约**:`contracts/openapi.json` 与前端类型纯增字段(make contracts 再生;`RetrieveOut` 形态不变,`RetrieveIn.user_id` 已由 102 提供——agent 侧零改动)
- **apps/web**:`SemanticPage` 指标表单增可见范围 Select(ALL/DEPT)+部门选择(DEPT 档)、列表纯增列(SHOULD)
- **无 schema 破坏**:缺省 ALL 两列,存量行零回填即零回归;无 agent 面改动
