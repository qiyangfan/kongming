# sql-execution 能力域

## Purpose

受控 SQL 执行路由:agent 面零凭证(ADR-002),全部 SQL 经控制面内部通道
`/internal/sql/execute` 收口执行——租户解析、行级 data_scope、只读连接、
行数上限与审计落库的行为契约在此立约。

## Requirements

### Requirement: 内部执行请求契约

执行请求 SHALL 携带 trace_id、tenant_key、sql_text;SHOULD 携带 user_id、
question、datasource_key(缺省 default)、limit(缺省 1000)。
tenant_key  SHALL 解析为已存在且启用的租户,否则 fail-closed 拒绝;
携带 user_id 时 SHALL 复校该用户与目标租户的绑定关系,未绑定拒绝
(防 agent 侧越权代理);user_id 缺省视为系统调用,豁免绑定复校与
data_scope 改写。

#### Scenario: 未知或禁用租户拒绝
- GIVEN 请求携带不存在或已禁用的 tenant_key
- WHEN 请求到达执行路由
- THEN 拒绝执行,不落业务查询,审计不落库(请求本身非法,无可审计主体)

#### Scenario: 用户未绑定目标租户拒绝
- GIVEN user_id 存在但该用户未绑定 tenant_key 对应租户
- WHEN 请求到达
- THEN 返回 403 拒绝,SQL 不执行

#### Scenario: 系统调用豁免
- GIVEN 请求不携带 user_id(系统级调用)
- WHEN 请求到达
- THEN 跳过绑定复校与 data_scope 改写,其余防御层不变

### Requirement: 默认数据源只读执行

datasource_key 为 default 时 SHALL 走默认数据源,经独立只读连接池执行
(连接级只读,池大小受限,与控制面写入通道分离);查询前 SHALL 在同一事务内
设置 search_path 为 tenant_<key> 与 public(事务级,连接归还池后不留痕);
结果行数 SHALL 按 limit 截断(默认 1000),响应含 columns/rows/row_count/
duration_ms。

#### Scenario: 只读查询成功执行
- GIVEN 已认证内部调用,SQL 通过全部防御层
- WHEN 执行
- THEN 在租户 schema 上下文执行并返回结果集,行数不超过 limit
- AND 连接级只读生效,任何写尝试被数据库层拒绝

#### Scenario: search_path 不污染连接池
- GIVEN 连接池复用同一物理连接
- WHEN 前一请求设置过事务级 search_path
- THEN 事务结束后连接恢复默认 search_path,下一请求不受污染

#### Scenario: 超 limit 截断
- GIVEN 查询实际命中 5000 行,limit=1000
- WHEN 执行返回
- THEN rows 恰为 1000 行,row_count=1000

### Requirement: 执行结果三态语义

响应 SHALL 以 verdict 三态区分结果:PASS(执行成功)、BLOCKED(防火墙或
安全策略拒绝,含 data_scope 不支持)、ERROR(执行期错误)。
BLOCKED 与 ERROR 时 rows 为空;ERROR 的 reason SHALL 含数据库错误摘要
(供 agent 自愈重试喂回),BLOCKED 的 reason SHALL 含拦截原因分类。

#### Scenario: 列名错误返回 ERROR
- GIVEN SQL 引用不存在的列
- WHEN 执行
- THEN verdict=ERROR,reason 含原始错误摘要(如 column does not exist),
  不暴露堆栈

#### Scenario: 写语句 BLOCKED 不重试语义可分
- GIVEN SQL 为 DELETE
- WHEN 经防火墙校验
- THEN verdict=BLOCKED 且 reason 可区分于 ERROR,调用方可据此放弃重试

### Requirement: 租户 schema 边界

执行请求中的 SQL SHALL 只引用当前租户 schema(tenant_<key>)、public 或未限定
表名;显式限定其他 schema 的表引用 SHALL 被拒绝(verdict=BLOCKED)并落审计——
search_path 只约束未限定名解析,显式限定可跨租户读,必须在执行前收口。

#### Scenario: 跨租户显式引用被拒
- GIVEN 当前请求租户为 A
- WHEN SQL 含 `FROM tenant_b.goods` 形式的显式 schema 限定
- THEN verdict=BLOCKED,reason 含 CROSS_SCHEMA,SQL 不执行,审计落库

#### Scenario: 本租户与 public 与未限定名放行
- GIVEN 当前请求租户为 A
- WHEN SQL 引用 tenant_a 表、public 表或未限定表名(经 search_path 解析)
- THEN schema 边界校验通过,正常进入后续防御层

### Requirement: 执行审计落库

每次进入执行的请求 SHALL 落一条审计:trace_id 原样贯穿,user_id、tenant_id、
datasource_key、question、sql_text(改写后实际执行形态)、verdict、
row_count、duration_ms 齐全;BLOCKED 行含 block_reason;审计写入失败
SHALL NOT 阻断查询响应,但 SHALL 以错误级日志暴露。

#### Scenario: 三类结果全落库
- GIVEN 分别产生 PASS、BLOCKED、ERROR 的三次执行
- WHEN 查询审计表
- THEN 三条记录齐全,verdict 各异,共享语义字段完整

#### Scenario: 审计记录实际执行 SQL
- GIVEN data_scope 改写了 SQL
- WHEN 审计落库
- THEN sql_text 为改写后形态(可完整回放实际执行内容)
