# rag spec delta(095-relation-aware-recall)

## ADDED Requirements

### Requirement: 关系感知召回扩展

召回主结果(三路 RRF 融合 + 047 继承路合并)确定后,系统 SHALL 按该租户+数据源内
**已声明**的表关系做**一跳扩展**:凡与任一已召回表存在关系行(introspection 与
manual 两类同权)的表,补入召回集。扩展 SHALL NOT 猜测关系——关系行不存在即
零扩展(与语义层「无 FK 库不猜测」同口径)。扩展 SHALL 缺省开启,并 SHALL 可经
配置整项关停。

扩展表 SHALL 去重(已在召回集内的表不重复补入)、SHALL 受可配上限于约(缺省 2)
约束、SHALL 追加在 schema_hint 的**尾部**;扩展条目 SHALL 与主召回条目同形态
(`schema.table(alias)(列…)`),SHALL NOT 新增段落类型或标记(Agent 侧解析与 080
预算截断逻辑零改;预算超限时按既有尾部丢弃方向先裁扩展,主召回不受影响)。

扩展生效后,召回响应内的 relations_hint 收窄基准 SHALL 同步扩展为
「主召回 ∪ 扩展」集合——扩展表 SHALL 携带其 JOIN path 一并注入(不带路径的
扩展表对生成无增益,只是稀释)。

trace SHALL 记录本轮扩展的表与经由关系(SHALL NOT 静默);召回试跑端点 SHALL
透出扩展明细(补入的表、经哪条关系补入、扩展后表集合),零副作用纪律
(不改配置/不落库/不触发缓存失效)不变。

关系查询失败或扩展端口未注入时,SHALL 降级为无扩展并继续召回主链路(与既有
语义段降级同口径),SHALL NOT 阻断问答、SHALL NOT 以错误终态返回。

**缓存失效(正确性要求)**:召回输出自此依赖关系数据,故关系写路径
(introspection upsert、手工关系增删、数据源删除级联清理)SHALL 在提交后失效
该租户+数据源的全部检索上下文缓存;未失效时,关系改动在缓存 TTL 窗口内
不可见(用户感知为「配了却没变化」的静默失效)。

#### Scenario: 关联表被扩展补入

- GIVEN 召回集含 `sakila.customer`,且存在声明关系 `sakila.customer → sakila.rental`
- WHEN 问句命中 customer 但未提及 rental,扩展开启
- THEN `sakila.rental` 补入召回集,追加在 schema_hint 尾部,relations_hint
  携带该 JOIN path

#### Scenario: 无关系库零扩展

- GIVEN 业务库无任何外键约束且无手工关系,introspection 未产生关系行
- WHEN 任意召回
- THEN 扩展集为空,输出与关停形态逐字一致(不猜测关系)

#### Scenario: 上限与去重

- GIVEN 某召回表声明了 5 条关系,扩展上限为 2
- WHEN 扩展执行
- THEN 仅补入 2 张未在召回集内的表;已在召回集内的关系目标不占额度、不重复追加

#### Scenario: 扩展表带 JOIN path

- GIVEN 扩展补入了 `sakila.payment`
- WHEN 召回响应组装
- THEN relations_hint 覆盖 `sakila.payment` 与召回表的 JOIN path(收窄基准含
  扩展表),而非仅主召回表两两

#### Scenario: 整项关停零回归

- GIVEN 扩展经配置关停
- WHEN 与开启前同问句召回
- THEN schema_hint / relations_hint / 响应信封逐字一致(零行为变更)

#### Scenario: 关系写后召回即时生效

- GIVEN 已缓存某问句的检索上下文(含上一次无扩展的结果)
- WHEN 管理员手工新增一条涉及该召回表的关系
- THEN 该租户+数据源的检索上下文缓存被失效,下一轮同问句召回立即反映新关系
  (不等待 TTL 过期)

#### Scenario: 试跑透出扩展明细

- GIVEN 管理员持有 system:rag:manage,某问句会扩展 1 张表
- WHEN 调用召回试跑端点
- THEN 返回扩展表名、经由关系、扩展后表集合;试跑前后缓存/配置/审计零写入

#### Scenario: 扩展失败不阻断

- GIVEN 关系查询抛异常(如关系表尚未迁移)
- WHEN 召回执行
- THEN 按无扩展完成召回并告警,问答链路不中断、不以错误终态返回
