# knowledge spec delta(042-fetch-metadata-reconcile)

## ADDED Requirements

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

#### Scenario: 扫描集外自动来源孤儿被对账删除

- GIVEN default 源存在扫描范围之外的自动来源登记(如旧形态 tenant_sakila.staff,AI_GENERATED)
- WHEN 对 default 源执行 fetch-metadata(扫描集=[tenant_default, public])
- THEN tenant_sakila.staff 登记行被删除,removed 计数含该行
- AND 审计 detail 记 removed;检索缓存被失效

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
