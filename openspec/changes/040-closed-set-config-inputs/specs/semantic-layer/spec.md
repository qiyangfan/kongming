# semantic-layer · Delta

## MODIFIED Requirements

### Requirement: 表关系存储与维护

表关系存 rag.table_relations(tenant+datasource+source_table+target_table+join_keys+source 标记);手工维护(source=manual)的记录 SHALL NOT 被 introspection 覆盖(upsert 守卫仅 introspection 来源);数据源删除 SHALL 级联清理其全部关系;管理端点提供手工增删(权限点 system:semantic:manage)。手工新增 SHALL 校验 source_table 与 target_table(『schema.table』形态)均属于该租户+数据源的既有知识条目,任一不存在即 400 `RELATION_TABLE_UNKNOWN`——写边界 fail-fast,防拼错表名产生永不命中召回的静默死配置;数据源存在/启用/归属校验维持既有口径。该校验属用例逻辑,SHALL 位于服务层(不经适配层直连仓储)。

#### Scenario: 手工关系不被覆盖

- GIVEN 存在一条 source=manual 的关系
- WHEN introspection 重扫且库内无该 FK
- THEN 手工关系保留,不被删除或改写

#### Scenario: 手工关系引用未知表被拒

- GIVEN 知识条目中不存在 `tenant_x.oders`(拼写缺 s)
- WHEN 手工新增以该表为 source 的关系
- THEN 返回 400 RELATION_TABLE_UNKNOWN,不落库

#### Scenario: 手工关系引用知识条目内表成功

- GIVEN 知识条目含 `tenant_x.orders` 与 `tenant_x.customers`
- WHEN 手工新增两表间关系(customer_id=id)
- THEN 落库 source=manual,召回命中两表时注入 relations_hint

#### Scenario: 数据源删除级联

- GIVEN 某数据源有 N 条关系
- WHEN 该数据源被删除
- THEN 其全部关系与指标定义被级联清理
