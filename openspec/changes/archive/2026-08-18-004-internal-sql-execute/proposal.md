# 004 /internal/sql/execute 执行路由真实化

## Why

控制面内部通道目前只有防火墙桩:校验通过即返回空结果,TODO 挂着
「连接池执行 + data_scope 注入 + 审计落库」。这是 NL2SQL 链路的咽喉——
agent 面零凭证(ADR-002),所有 SQL 执行 100% 收口于此;本变更把这条路由
做成真,纵深防御(防火墙 → 租户路由 → data_scope 行级改写 → 只读连接)
与审计完整性(成功/拦截/失败三类全落库)在此合龙。datascope 是最深最易错
的域,spec 十条测试清单先行(宪法 Article III)。

## What Changes

- **迁移 0003(ADR-004)**:`sys_dept` 部门表(UUID v7/parent_id 自引用/tenant_id FK,
  VARCHAR+CHECK,timestamptz)+ `sys_user.dept_id` 列与索引——rbac spec「部门管理」
  的存储底座,baseline 当初「dept_id 弃」是缓建非否决,本变更补齐。
- **datascope 域包**(router 无关,service/models 零框架依赖):多角色 data_scope
  取最宽松(宽松序 ALL > CUSTOM > DEPT_AND_BELOW > DEPT > SELF,与上游数值序一致);
  ALL 不改写;SELF/DEPT/DEPT_AND_BELOW 经列存在性探测(information_schema,
  schema 限定 tenant_<key>+public 防跨 schema 同名误判)决定改写或安全降级
  (降级落审计告警);CUSTOM 与未知值 fail-closed 拒绝;DEPT_AND_BELOW 递归 CTE
  查子部门。改写 = 谓词 AND 进最外层查询 WHERE(AST 级,列限定主表别名;
  spec「包子查询」字面经集成测试证伪——原查询不投影过滤列时外层不可见,
  delta 修正措辞),值一律绑定参数(按探测到的列类型绑定 uuid/text,
  非字符串拼接),改写后再过一次防火墙。
- **执行路由真实化**:`POST /internal/sql/execute` 全链路——内部令牌守门(已有
  middleware)→ 防火墙初检(BLOCKED 落审计)→ 租户解析(tenant_key → 租户行,
  未知/禁用 fail-closed)→ 用户-租户绑定复校(防 agent 侧 confused deputy;
  user_id 缺省的系统调用豁免)→ data_scope 改写 → 防火墙复检 → 默认数据源
  只读连接池执行(连接级只读,池上限 5,与控制面写库引擎物理分离)→
  事务内 SET LOCAL search_path = tenant_<key>, public → 行数上限截断
  (默认 1000)→ 审计落库。响应携带 verdict(PASS/BLOCKED/ERROR)/reason/
  columns/rows/row_count/duration_ms,ERROR 时 reason 含 DB 错误摘要
  (供 agent selfheal 喂回 LLM)。
- **审计落库**(audit spec「查询审计落库」):PASS/BLOCKED/ERROR 三类全落
  sys_sql_audit,trace_id 原样贯穿,请求模型新增 question 字段;
  审计写入失败不影响主流程响应(告警日志),但执行结果不落审计视为严重事故
  打 error 级日志。
- **契约**:请求/响应模型落定即跑 `make contracts`,openapi.json 同步提交(ADR-005)。

## Capabilities

### New Capabilities

- `sql-execution`: 受控 SQL 执行路由的行为契约——内部通道请求/响应模型、
  租户解析与用户-租户绑定复校的 fail-closed 语义、只读连接池与事务级
  search_path、行数上限、执行错误语义(verdict=ERROR 供 selfheal)、
  审计三类落库与 trace_id 贯穿。

### Modified Capabilities

- `datascope`: 改写机制措辞修正——「最外层包子查询加 WHERE」改为「最外层查询
  WHERE AST 注入(列限定主表别名)」。集成测试实证:原查询不投影过滤列时
  包子查询方案外层不可见、执行必错;安全语义(降级/fail-closed/绑定参数/
  复检)不变。

## Non-goals

- 不做动态数据源注册/管理端点与按 sys_datasource 行的动态池(仅默认数据源
  default 起步;datasource spec 管理面留待后续变更,池管理器预留 key 维度)。
- 不做 agent 面 LLM 接线与回调调用方(下一棒);本变更只保证路由契约可被回调。
- 不做审计/操作日志的查询接口与行级可见性(audit spec 后半,随管理面变更交付);
  不做 sys_oper_log 自动记录切面。
- 不做知识/RAG、fetch-metadata、前端任何页面。
- 不动防火墙规则本身(nl2sql spec 已立约,现有实现已覆盖);不改 auth 域。
- 不引入新依赖:改写用已在库的 sqlglot,执行用已在库的 SQLAlchemy/asyncpg。

## Impact

- **schema**:迁移 0003(sys_dept + sys_user.dept_id),纯加法,不动存量表结构。
- **代码(apps/api)**:新增 `kongming/datascope/`(models/service/rewrite/repository)、
  `kongming/audit/`(writer)、`kongming/internal/` 内执行编排与只读引擎;
  `internal/router.py` 请求/响应模型落定。
- **契约**:openapi.json + 前端类型重生成(内部模型进入契约面)。
- **测试**:datascope 十条清单(宪法强制)+ 执行路由全栈集成测试
  (真实 PG:租户 schema 造数、绑定复校、降级告警、审计断言)。
- **ADR 引用**:ADR-002(信任边界:执行收口控制面)、ADR-004(迁移 0003 规范)、
  ADR-005(契约管线)。
