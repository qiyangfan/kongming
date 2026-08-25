## Purpose

为 NL2SQL 提供表关系(JOIN path)与指标口径的结构化语义层:外键自动 introspection + 手工维护、指标口径集中管理并注入生成链路,补齐「JOIN 靠猜列名、术语无口径」短板;注入只作提示文字,最终 SQL 仍由 LLM 生成并走全量防御。

## Requirements

### Requirement: 外键/主键 introspection

系统 SHALL 按数据源经 information_schema 抓取表间外键关系与主键(幂等 upsert,source=introspection——重复抓取不产生重复行);无 FK 约束的业务库 SHALL NOT 猜测关系(留空,交手工补充);introspection 仅覆盖单数据源内表间关系;抓取须经数据源存在/启用/归属校验(与既有数据源管理同口径 fail-closed)。

#### Scenario: 幂等抓取

- GIVEN 某数据源已抓取过外键关系
- WHEN 再次触发 introspection
- THEN 关系行不重复(同 source-target 键 upsert),数量不变

#### Scenario: 无 FK 库不猜测

- GIVEN 业务库无外键约束
- WHEN introspection 执行
- THEN 不产生任何 introspection 关系行

### Requirement: 表关系存储与维护

表关系存 rag.table_relations(tenant+datasource+source_table+target_table+join_keys+source 标记);手工维护(source=manual)的记录 SHALL NOT 被 introspection 覆盖(upsert 守卫仅 introspection 来源);数据源删除 SHALL 级联清理其全部关系;管理端点提供手工增删(权限点 system:semantic:manage)。

#### Scenario: 手工关系不被覆盖

- GIVEN 存在一条 source=manual 的关系
- WHEN introspection 重抓且库内无该 FK
- THEN 手工关系保留,不被删除或改写

#### Scenario: 数据源删除级联

- GIVEN 某数据源有 N 条关系
- WHEN 该数据源被删除
- THEN 其全部关系与指标定义被级联清理

### Requirement: 指标口径定义

系统 SHALL 支持按数据源定义指标:metric_name(数据源内唯一)、sql_template(口径表达式/片段)、business_terms(业务别名/触发词)、enabled。sql_template SHALL NOT 被直接执行——仅作文字注入 prompt(最终 SQL 由 LLM 生成并走防火墙+只读池全量防御);管理端点不提供任何「执行模板」能力。

#### Scenario: 指标定义与唯一约束

- GIVEN 数据源已存在指标「复购率」
- WHEN 创建同名指标
- THEN 拒绝(唯一约束),或经编辑更新原定义

### Requirement: 生成链路注入

RAG 召回响应 SHALL 纯增:relations_hint(仅召回表集合内的两两 JOIN path 文字描述;召回为空则不携带)与 metric_hints(问句命中 business_terms/metric_name 的启用指标,top-5,含 sql_template 文字);Agent 面 generate 节点 SHALL 在用户提示中拼接两段(系统提示模板不变),并遵循「JOIN 优先依据 relations_hint、口径优先依据 metric_hints」的引导;无数据时零注入,行为与引入前逐字一致。

#### Scenario: 注入收窄到召回集合

- GIVEN 数据源有 20 张表的关系,本次召回 2 表
- WHEN 召回响应组装
- THEN relations_hint 只含该 2 表相关的关系,不灌全库

#### Scenario: 指标命中注入

- GIVEN 启用指标「复购率」含 sql_template
- WHEN 用户问「各品类复购率」
- THEN metric_hints 含该指标口径文字,generate 提示携带

### Requirement: 语义层管理与权限

管理端点(关系 CRUD/指标 CRUD/触发 introspection)SHALL 要求权限点 system:semantic:manage(仅 SUPER_ADMIN 种子覆盖),全部按 tenant+datasource 双过滤;非超管访问 SHALL 403 并落 perm_denied 审计;伪造 datasource_key SHALL 同形拒绝(不暴露存在性)。

#### Scenario: 非超管拒绝

- GIVEN 角色不含 system:semantic:manage
- WHEN 访问语义层管理端点
- THEN 返回 403
