# chat-suggestions spec delta(047-qa-retrieval-governance)

## MODIFIED Requirements

### Requirement: 缓存生命周期

建议缓存 SHALL 以租户+数据源为唯一维度,无时限(至失效事件为止)。数据源
连接信息(host/port/database/username/password/default_schema)任一变更或
数据源被删除,SHALL 使该源全部租户的建议缓存失效,后续访问不得返回旧建议。
**表结构知识发生写变更(上传/编辑/删除/fetch-metadata 对账/reindex)SHALL
同样级联失效该数据源全部租户的建议缓存,失效与知识写在同一业务事务**
(047:知识收录收窄与治理会使既有建议失效为「撞墙题」——保留表收编后
default 源存量建议仍推荐用户记忆问题 3/5 实证;范式同 datasource 级联
先例)。同一租户同一数据源的并发首次访问 SHALL 至多产生一次生成且落库
幂等(后到者获得已落库结果,不重复生成)。

#### Scenario: 连接信息变更后失效

- GIVEN 某数据源已存在建议缓存
- WHEN 管理员变更其连接信息(如改指另一数据库)
- THEN 该源各租户缓存失效,后续访问按新结构重新生成或收敛空清单,不返回旧建议

#### Scenario: 删除后失效

- GIVEN 某数据源已存在建议缓存
- WHEN 管理员删除该数据源
- THEN 该源缓存一并失效,不留孤儿缓存行

#### Scenario: 知识治理后建议重新生成(047)

- GIVEN default 源存量建议含已被治理排除的问题(如保留表问题)
- WHEN 对 default 源执行知识写操作(fetch-metadata 对账清偿)
- THEN 存量建议缓存被删除
- AND 下次访问以新知识状态重新生成并落缓存

#### Scenario: 并发首访只生成一次

- GIVEN 同租户同数据源缓存为空
- WHEN 多个请求并发首次访问
- THEN 至多执行一次生成,各请求最终获得一致的已落库清单
