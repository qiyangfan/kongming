# internal-meta spec delta(047-qa-retrieval-governance)

## MODIFIED Requirements

### Requirement: 租户 schema 实时内省

内省请求 SHALL 经内部通道携带 tenant_key,SHOULD 携带 datasource_key
(缺省 default)。datasource_key 为 default 时,系统 SHALL 返回该租户业务
schema(tenant_<tenant_key>)与 public 范围内用户**基表**的表名、表注释、
列名、列类型、列注释;非 default 时 SHALL 先校验数据源存在+启用+归属
tenant_key 对应租户(任一不满足 fail-closed 拒绝,拒绝同形不区分原因,
防 key 探测),通过后连接该数据源,内省范围 SHALL 限其 default_schema
单个 schema。控制面保留表(前缀 sys_/chat_ 及 alembic_version、
user_identities、user_memory,与执行路由保留表拒读同一判定)SHALL NOT
出现在结果中,且不占表数上限。**内省范围 SHALL 限基表
(`table_type = 'BASE TABLE'`),视图 SHALL NOT 出现在结果中**(047:与
knowledge 收录同口径——内省结果直连生成上下文(ReAct 内省回退)与
元数据呈现,视图行扇出与列缺失形态对两条消费面同样有毒)。tenant_key
未知或租户已禁用 SHALL fail-closed 拒绝。返回表数量 SHALL 有上限截断
(防提示文本膨胀),截断时 SHALL 显式标注 truncated 标志,不得静默漏表。

#### Scenario: 正常内省返回表结构
- GIVEN 租户 default 的 schema 内存在若干用户表(含注释)
- WHEN 内部调用携带 tenant_key=default 发起内省(不带 datasource_key)
- THEN 返回每张表的表名/表注释/列清单(列名+类型+注释)
- AND 覆盖 tenant_default 与 public 两个 schema 的用户基表

#### Scenario: 视图不进内省结果(047)
- GIVEN 扫描范围内存在业务视图(如便捷视图 film_list)
- WHEN 发起内省
- THEN 结果不含该视图(仅基表)
- AND 与 fetch-metadata 收录范围同口径(登记=现实,两通道不漂移)

#### Scenario: 动态数据源内省限 default_schema
- GIVEN 数据源 sakila 已启用且归属租户 default,其 default_schema 有业务表
- WHEN 内部调用携带 tenant_key=default + datasource_key=sakila
- THEN 经该数据源连接返回其 default_schema 内的用户表结构
- AND 不越出 default_schema 边界(其他 schema 表不出现)

#### Scenario: 非法 datasource_key 同形拒绝
- GIVEN datasource_key 不存在、已禁用、或归属其他租户
- WHEN 内省请求携带该 key
- THEN fail-closed 拒绝(400),三种原因响应同形(防 key 探测),不返回任何 schema 信息

#### Scenario: 未知或禁用租户拒绝
- GIVEN 请求携带不存在或已禁用的 tenant_key
- WHEN 内省请求到达
- THEN 拒绝(404),不返回任何 schema 信息

#### Scenario: 不越出租户边界
- GIVEN 其他租户 schema(tenant_xxx)或系统 schema(pg_catalog 等)存在表
- WHEN 内省 tenant_key=default(默认数据源)
- THEN 返回结果不含 tenant_default/public 以外的任何表

#### Scenario: 控制面保留表不进内省结果
- GIVEN public schema 存在控制面表(sys_user/chat_message/user_identities 等)
- WHEN 内省任意租户
- THEN 返回结果不含任何保留表(与执行路由的保留表拒读同一判定)
- AND 保留表不占表数上限

#### Scenario: 表数超限截断并标注
- GIVEN 租户 schema 内用户表数量超过内省上限
- WHEN 内省返回
- THEN 表清单截断到上限,且响应 truncated=true

#### Scenario: 内部通道鉴权
- GIVEN 调用方未携带内部令牌或令牌错误
- WHEN 请求内省端点
- THEN 返回 403,与内部面其余端点一致
