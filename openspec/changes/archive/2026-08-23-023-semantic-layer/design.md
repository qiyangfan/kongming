# 023 semantic-layer 设计

## Context

既有:009 rag 三路召回(/internal/rag/retrieve 返回 schema_hint+
history_sql);008 数据源 resolver(只读连接+归属校验);generate 拼接
段先例(009 历史 SQL 段、022 记忆段——系统模板零改动)。约束:ADR-004
新表、注入只作提示文字、零新依赖。

## Goals / Non-Goals

Goals:两表 + introspection + 手工维护 + 指标 + 注入两段 + 管理端点 + UI。
Non-goals 见 proposal。

## Decisions

### D1: 表结构(0012,rag schema)

rag.table_relations(id uuidv7, tenant_id, datasource_key, source_table,
target_table, join_keys JSONB[{left_col,right_col}], source VARCHAR(16)
CHECK(introspection/manual), created_at/updated_at;UNIQUE(tenant_id,
datasource_key, source_table, target_table));rag.metric_definitions(id,
tenant_id, datasource_key, metric_name, sql_template TEXT, business_terms
TEXT[], enabled BOOLEAN DEFAULT true, created_at/updated_at;UNIQUE
(tenant_id, datasource_key, metric_name))。数据源删除级联:在 datasource
域删除路径补删两表行(同租户同 key;009 挂账①知识条目不级联为既定,
本域级联是 spec 立约)。

### D2: introspection SQL(information_schema)

referential_constraints JOIN key_column_usage(kcu1 主表列)JOIN
constraint_column_usage 配对 + table_constraints 取主键;经 008
resolve_for_read 的只读连接(动态源)或控制面会话(default,内省目标
schema 集 {tenant_<key>, public})。upsert:ON CONFLICT(四元唯一)
DO UPDATE SET join_keys, updated_at WHERE source='introspection'
——manual 行不被覆盖(PG 部分守卫:冲突目标行 source 判定用
WHERE 子句在 DO UPDATE 上:... DO UPDATE SET ... WHERE
table_relations.source='introspection';manual 行冲突时 DO NOTHING 效果
由 WHERE 不成立天然达成——PG 语义:WHERE 假则该行不更新,跳过)。

### D3: 注入组装(rag service recall 响应扩展)

RagContext/RetrieveOut 纯增 relations_hint/metric_hints(str);组装逻辑
放 semantic 域 service(端口注入 rag router 组合根;rag.service 零框架
不 import semantic 仓储——RecallEnricher 端口:enrich(tenant_id,
datasource_key, question, recalled_tables) -> (relations_hint, metric_hints))。
relations:recalled_tables 两两组合查 table_relations(启用全部),格式
`schema.a JOIN schema.b ON a.x = b.y`;metric:问句 ILIKE 任一
business_terms/metric_name 的启用指标 top-5。

### D4: agent 拼接

apiclient RagContext 加两字段;nodes.retrieve 透传 state;generate 拼接
`表关系参考:\n{relations_hint}\n\n` + `指标口径参考:\n{metric_hints}\n\n`
位于 schema_hint 之后、history_part 之前;react 内核:rag_retrieve 工具
结果原样含字段(内核零改动——observe 截断不影响字段存在)。

### D5: 管理端点

GET/POST/DELETE /system/semantic/relations(+POST /system/semantic/
relations/introspect);GET/POST/PUT/DELETE /system/semantic/metrics;
全部 system:semantic:manage(排序 17)+ tenant 头复校+datasource 归属
校验(复用 008 checker);web /admin/semantic 双 Tab 页。

## Risks / Trade-offs

- [introspection 对执行库的查询开销] → information_schema 只读小查询,
  手动触发(管理面),非请求路径。
- [ON CONFLICT WHERE 守卫的 PG 语义边角] → 直证用例(manual 行重抓
  不被改写)。
- [注入段膨胀] → relations 仅召回集合两两 + metric top-5 + 截断。
- [sql_template 被当可执行] → 无端点无代码路径执行;评审断言 grep
  无 execute(sql_template) 调用。

## Migration Plan

0012 两表(零存量);种子权限点;滚动重启任意序(响应纯增,老 agent
忽略新字段);回滚=表留库无害。

## R1 评审修复记录

H1(多列 FK 笛卡尔积,实证错配):introspection SQL 改 referential_constraints
+ pk.ordinal_position = fk.position_in_unique_constraint 列序配对,补多列 FK
用例。H2(端点缺数据源校验+动态源张冠李戴):introspect 显式拒非 default
(v1 仅 default 源,同形 400);relations/metrics 写路径补存在+启用+归属
校验;L1 schema 集按解析租户反查。M1:手工 upsert 冲突翻转 source='manual'
(手工接管防还原)+RETURNING 真实行 id(修新增返回假 id)。M2:agent 回退
分支透传语义段;toolcall rag 适配器补 enrich(ReAct 主路径语义层生效)。
L2 死路径正则删除;L3 undefined_table 升 error 告警;L4 UI 三处
(introspection 只读禁删/指标启停 Switch/多 JOIN 键);L5 两 GET 端点
response_model(契约生成类型);L7 注释/design 措辞对齐。
**挂账**:L6(单表自引用 FK 不注入,spec「两两 JOIN path」未覆盖)、
L8(introspection 不清理库内已删 FK 的陈旧行,sync 语义随运营)。

## Open Questions

- O1:关系方向(左右表序)对 LLM 的引导影响——观察生成质量,随评估。
- O2:跨 schema 同名表的关系归属——v1 按召回表的 schema 限定;同名
  冲突随真实数据集出现再细化。
