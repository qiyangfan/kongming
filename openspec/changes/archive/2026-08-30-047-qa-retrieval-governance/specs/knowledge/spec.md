# knowledge spec delta(047-qa-retrieval-governance)

## MODIFIED Requirements

### Requirement: 扫描对账(登记=现实)

fetch-metadata 收口时 SHALL 执行对账:该 (租户, 数据源) 下
`source_type` 属自动来源(INTROSPECTION/AI_GENERATED)且
(schema_name, table_name) 不在本次扫描集内的登记行 SHALL 删除
(删除与 upsert 同事务,审计 detail 记删除数);扫描集外的人工/反馈行
(MANUAL_UPLOAD/MANUAL_EDIT/FEEDBACK_REFINED)SHALL 保留并告警
(不可逆资产交人工裁决);内省失败(连接/凭证异常)SHALL NOT 触发对账。
对账删除后 SHALL 经既有钩子精确失效该 (租户, 数据源) 检索缓存。
响应体 SHALL 含 `removed` 字段(本次对账删除行数)。
**登记优先级判定与生效别名预读 SHALL 以 (schema_name, table_name)
二元键定位既有行**——同一数据源范围内跨 schema 同名表不得互相遮蔽。
**扫描集收录范围 SHALL 限基表(`table_type = 'BASE TABLE'`):视图
SHALL NOT 进入表结构知识**(047:视图是基表派生物,行扇出使聚合失真
——film_list AVG 被库存份数加权实证;视图样例 SQL 挤占召回 top-K 使
维度表缺位实证;基表可完整表达视图可表达的一切查询,收录即引入歧义)。
存量视图/保留表自动来源登记由本对账自动清偿,无需专门清理程序。

#### Scenario: 扫描集外自动来源孤儿被对账删除
- GIVEN default 源存在扫描范围之外的自动来源登记(如旧形态 tenant_sakila.staff,AI_GENERATED)
- WHEN 对 default 源执行 fetch-metadata(扫描集=[tenant_default, public])
- THEN tenant_sakila.staff 登记行被删除,removed 计数含该行
- AND 审计 detail 记 removed;检索缓存被失效

#### Scenario: 存量视图登记被对账清偿
- GIVEN 知识库存在视图登记行(如 tenant_sakila.film_list,AI_GENERATED)
- WHEN 对该数据源执行 fetch-metadata(扫描集只含基表)
- THEN 该视图登记行被删除(自动来源孤儿),removed 计数含该行
- AND 召回与生成上下文不再出现视图条目

#### Scenario: 扫描集外人工条目保留并告警
- GIVEN 扫描范围之外存在 MANUAL_EDIT 登记行
- WHEN 执行 fetch-metadata
- THEN 该行保留(source_type 与内容不变),服务端日志告警其数量

#### Scenario: 跨 schema 同名表不互相遮蔽
- GIVEN tenant_default.goods 为 MANUAL_EDIT、public.goods 为 AI_GENERATED(同租户同数据源)
- WHEN 执行 fetch-metadata
- THEN 优先级判定各自按 (schema, table) 定位:tenant_default.goods 整行跳过保护
- AND public.goods 按自动来源正常更新

#### Scenario: 内省失败不触发对账
- GIVEN 目标数据源连接失败
- WHEN fetch-metadata 执行
- THEN 请求失败,既有登记行全部保留(不误删)

## ADDED Requirements

### Requirement: 知识写路径级联失效建议缓存

知识写路径(上传/编辑/删除/fetch-metadata/reindex)SHALL 在失效检索
缓存的同时级联失效该数据源的建议问题缓存(与 ctx 缓存失效同点、同一
业务事务,039 datasource 级联先例同范式)——表结构知识变化(收录收窄/
对账清偿/人工治理)若不失效建议缓存,存量建议会持续推荐已被治理排除
的问题(047 实证:保留表收编后 default 源建议仍推荐撞墙题 3/5)。

#### Scenario: fetch-metadata 对账清偿后建议缓存同步失效
- GIVEN default 源存在存量建议缓存(含已被治理排除的问题)
- WHEN 对 default 源执行 fetch-metadata 触发对账删除
- THEN 该数据源的建议缓存被同事务删除
- AND 下次访问按新知识状态重新生成建议
