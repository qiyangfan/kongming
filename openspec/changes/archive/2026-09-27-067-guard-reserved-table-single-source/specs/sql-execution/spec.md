# sql-execution spec delta(067-guard-reserved-table-single-source)

## MODIFIED Requirements

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
