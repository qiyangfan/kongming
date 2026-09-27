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

### Requirement: 控制面保留表拒读

执行路由 SHALL 拒绝任何引用控制面保留表的语句(verdict=BLOCKED,落审计),
与语句其余部分的合法性无关。保留表按表名末段判定(小写折叠语义):
前缀 `sys_`、`chat_`、`skill_`,及精确名 `alembic_version`、`user_identities`、
`user_memory`(022 用户长期记忆表,2026-08-29 039 评审上报补收编:归属列
为 user_id 非 create_by,data_scope 行级过滤不构成防线,022 建表时漏同步
收编曾构成 default 源可读全库记忆的开放通路)、`user_prefs`(059 闭合偏好
表,与 user_memory 同归属列形态,同一理由收编)。
rag 知识两表(table_descriptions/query_history,含全租户问句/成功 SQL/表结构)
SHALL 按**表引用的显式 schema 限定名**拒:schema 为 `rag` 的表引用即拒
(search_path 管不了 `rag.query_history` 形态的显式限定名,009 评审实证);
裸名形态不进末段名保留清单——rag 不在任何执行路径 search_path 内,裸名
落 public 解析到不存在的表(执行错误,无泄漏通路),且 query_history 等
通用名可能是业务库真实表名,末段名保留会误伤。
判定 SHALL 不限定 schema 生效——裸表名经 search_path 可解析到 public
控制表,是最危险形态;CTE 别名命中保留名 SHALL 同样拒绝(遮蔽形态
`WITH sys_user AS (SELECT FROM public.sys_user) SELECT FROM sys_user` 曾实证
绕过:PG 语义中 CTE 名只遮蔽裸名,体内限定名引用仍解析到真实表);
非保留 CTE 名引用与表函数 SHALL NOT 误伤。
保留前缀平台级生效:租户 schema 由平台开通,SHALL NOT 出现同名表。

**清单单一权威源(067)**:上述保留前缀、保留精确名、保留 schema、只读根语句
类型与危险函数清单 SHALL 只存在**一处定义**,控制面防火墙、Agent 面生成侧预检
与可插拔安全引擎 SHALL 引用同一来源;SHALL NOT 存在需靠注释或人工纪律维持一致的
平行拷贝。新增敏感表 SHALL 为单点变更,且变更即刻对所有判定面同时生效。
本域该清单 SHALL 为完整枚举的唯一出处;其他能力域仅陈述"某表为何敏感"与其拒读
场景,SHALL NOT 各自重复声明清单成员。

**切主前置(067)**:安全引擎由并行判定升为权威判定之前,清单一致性校验 SHALL
通过。并行判定期的"判定不一致计数归零"SHALL NOT 被当作清单一致的证据——
该计数仅在实际查询触达差异表时才产生,评估集与自然流量均不保证覆盖。

背景(005 评审实证):search_path 含 public 且只读角色对 public 全表有
SELECT 时,LLM 可猜中 `sys_user` 等表名直读口令哈希/数据源密文/他人对话;
data_scope 对无 create_by 列的表安全降级不过滤,不构成防线。009 背景:
rag 两表灌入全租户真实问句与 SQL 后,显式限定名读取曾是开放通路。
045 背景:user_memory 归属列 user_id 不被 data_scope 归属列识别
(create_by),SELF/DEPT 域安全降级不过滤,default 源限定名/裸名引用
曾构成可读全库用户记忆的开放通路(跨租户)——收编后任何 data_scope
(含 ALL)一律拒读;记忆的正当通路=召回注入(memory_hint)与
/memory API,均不经执行路由。067 背景:`skill_` 与 `user_prefs` 随 056/059
搭车收编时只改了控制面一处常量,可插拔引擎侧清单窄一档且长期未被并行判定
计数暴露(无查询触达即无信号),构成"切主即停止拒读"的潜在开放通路。

#### Scenario: 裸名引用保留表拦截
- GIVEN 语句 `SELECT username, password_hash FROM sys_user`(裸名)
- WHEN 到达执行路由
- THEN verdict=BLOCKED(reason 含 RESERVED_TABLE),不落业务查询,落审计

#### Scenario: 精确名 skill 与偏好表拦截(067 收编)
- GIVEN 语句 `SELECT * FROM skill_pack`、`SELECT glossary FROM public.skill_pack`
  或 `SELECT * FROM user_prefs`
- WHEN 到达执行路由(请求方 data_scope 为任意档)
- THEN verdict=BLOCKED,落审计

#### Scenario: 清单单一来源被断言(067)
- GIVEN 防火墙、Agent 面预检与安全引擎三方读取的保留表清单
- WHEN 执行一致性校验
- THEN 三方引用的即同一处定义(而非内容恰好相等),校验为绿
- AND 在第二处新增一份清单字面值时,该校验 SHALL 失败

#### Scenario: 切主前置校验生效(067)
- GIVEN 并行判定期不一致计数为零,但引擎侧清单成员少于权威清单
- WHEN 尝试将引擎升为权威判定
- THEN 升主被阻止(前置校验未通过),SHALL NOT 因"计数归零"而放行

#### Scenario: user_memory 任何 data_scope 一律拦截
- GIVEN 语句 `SELECT content FROM user_memory`、`SELECT * FROM
  public.user_memory`、`WITH m AS (SELECT content FROM public.user_memory)
  SELECT * FROM m` 或 JOIN 夹带形态
- WHEN 到达执行路由(请求方 data_scope 为 SELF / DEPT / ALL 任意档)
- THEN verdict=BLOCKED(reason 含 RESERVED_TABLE),落审计
- AND 业务表普通名查询 SHALL NOT 误伤

### Requirement: 危险函数拒用

执行路由 SHALL 拒绝调用危险函数的语句(verdict=BLOCKED,落审计),即使语句
形态是合法 SELECT。拒用清单(按归一化函数名小写比对):服务器文件/目录
读写(pg_read_file、pg_read_binary_file、pg_ls_dir、pg_ls_logdir、
pg_ls_waldir、pg_stat_file、lo_import、lo_export)、运行时配置改写
(set_config)、资源滥用(pg_sleep)、外部连接(dblink 系)。
常规只读函数(聚合/日期/current_setting/generate_series 等)SHALL NOT 误伤。

背景(R3 侧报实证):Select 根 + 无保留表的纯函数调用可穿透全部既有防御层;
执行角色权限足够时 pg_read_file 可读服务器任意文件。

#### Scenario: 文件读函数拦截
- GIVEN 语句 `SELECT pg_read_file('/etc/passwd')`
- WHEN 到达执行路由
- THEN verdict=BLOCKED(reason 含 FORBIDDEN_FUNC),落审计

#### Scenario: 嵌套位置同样拦截
- GIVEN 危险函数出现在子查询/WHERE 条件等嵌套位置
- WHEN 到达执行路由
- THEN verdict=BLOCKED

#### Scenario: 常规函数放行
- GIVEN 语句含聚合/日期/COALESCE 等常规只读函数
- WHEN 到达执行路由
- THEN 正常放行执行
