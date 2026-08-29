# sql-execution spec delta(045-reserved-user-memory)

## MODIFIED Requirements

### Requirement: 控制面保留表拒读

执行路由 SHALL 拒绝任何引用控制面保留表的语句(verdict=BLOCKED,落审计),
与语句其余部分的合法性无关。保留表按表名末段判定(小写折叠语义):
前缀 `sys_`、`chat_`,及精确名 `alembic_version`、`user_identities`、
`user_memory`(022 用户长期记忆表,2026-08-29 039 评审上报补收编:归属列
为 user_id 非 create_by,data_scope 行级过滤不构成防线,022 建表时漏同步
收编曾构成 default 源可读全库记忆的开放通路)。
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

背景(005 评审实证):search_path 含 public 且只读角色对 public 全表有
SELECT 时,LLM 可猜中 `sys_user` 等表名直读口令哈希/数据源密文/他人对话;
data_scope 对无 create_by 列的表安全降级不过滤,不构成防线。009 背景:
rag 两表灌入全租户真实问句与 SQL 后,显式限定名读取曾是开放通路。
045 背景:user_memory 归属列 user_id 不被 data_scope 归属列识别
(create_by),SELF/DEPT 域安全降级不过滤,default 源限定名/裸名引用
曾构成可读全库用户记忆的开放通路(跨租户)——收编后任何 data_scope
(含 ALL)一律拒读;记忆的正当通路=召回注入(memory_hint)与
/memory API,均不经执行路由。

#### Scenario: 裸名引用保留表拦截
- GIVEN 语句 `SELECT username, password_hash FROM sys_user`(裸名)
- WHEN 到达执行路由
- THEN verdict=BLOCKED(reason 含 RESERVED_TABLE),不落业务查询,落审计

#### Scenario: 显式 public 限定同样拦截
- GIVEN 语句 `SELECT * FROM public.sys_user`
- WHEN 到达执行路由
- THEN verdict=BLOCKED,落审计

#### Scenario: user_memory 任何 data_scope 一律拦截
- GIVEN 语句 `SELECT content FROM user_memory`、`SELECT * FROM
  public.user_memory`、`WITH m AS (SELECT content FROM public.user_memory)
  SELECT * FROM m` 或 JOIN 夹带形态
- WHEN 到达执行路由(请求方 data_scope 为 SELF / DEPT / ALL 任意档)
- THEN verdict=BLOCKED(reason 含 RESERVED_TABLE),落审计
- AND 业务表普通名查询 SHALL NOT 误伤

#### Scenario: rag schema 限定名拦截
- GIVEN 语句 `SELECT question, sql_text FROM rag.query_history` 或
  `SELECT * FROM rag.table_descriptions`
- WHEN 到达执行路由
- THEN verdict=BLOCKED(reason 含 RESERVED_TABLE),不落业务查询,落审计
- AND 不区分租户:任何租户的执行请求同样拦截(rag 表为跨租户数据,行级
  过滤不构成防线)
- AND 业务库同名表(query_history 等通用名,显式非 rag schema 限定)SHALL
  NOT 误伤

#### Scenario: 系统调用不豁免
- GIVEN 请求不携带 user_id(系统调用)
- WHEN 语句引用保留表
- THEN 同样 BLOCKED(保留表判定与用户身份无关)

#### Scenario: CTE 名与业务表不误伤
- GIVEN 语句含非保留名 CTE(如 WITH top5 AS (SELECT ...) SELECT FROM top5)
  或纯业务表查询
- WHEN 到达执行路由
- THEN 正常放行执行

#### Scenario: CTE 遮蔽形态拦截
- GIVEN 语句以保留名作 CTE 别名(WITH sys_user AS (...)),或 CTE 体内
  用限定名引用保留表(WITH g AS (SELECT FROM public.sys_user) ...)
- WHEN 到达执行路由
- THEN verdict=BLOCKED(CTE 豁免不构成绕过面)
