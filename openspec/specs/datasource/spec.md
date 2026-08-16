# datasource 能力域

## Purpose

动态数据源管理:超管注册外部 **PostgreSQL** 业务库(PG-only,方言能力储备见 ADR-001 附注),凭证 AES-GCM 加密落库、DTO 不回传敏感字段,支持测试连接、元数据抓取生成知识,RAG 与查询链路按 datasourceKey 分维度动态路由,无需重启接入新业务库。

## Requirements

### Requirement: 数据源注册与凭证加密

超管可注册 PostgreSQL 业务库(datasource_key/name/host/port/database/username/password/default_schema/tenant),密码落库前必须加密;管理端点要求权限点 system:datasource:manage(仅 SUPER_ADMIN);返回 DTO 不含密码密文。

#### Scenario: 注册数据源并加密密码
- GIVEN 超管持有 system:datasource:manage
- WHEN 注册数据源(含连接信息与密码)
- THEN 密码经 AES-GCM 加密后落库,永不存明文
- AND 密钥经环境变量注入(base64 16/32 字节),prod 必须配置
- AND 返回 DTO 不含任何敏感字段

#### Scenario: datasource_key 唯一性
- GIVEN 已存在 datasource_key=sakila 的启用记录
- WHEN 再次注册同 key
- THEN 拒绝;key 在启用范围内全局唯一(含禁用记录校验)

#### Scenario: 非超管访问管理端点被拒
- GIVEN 非 SUPER_ADMIN 用户
- WHEN 访问任一管理端点(page/create/update/delete/test/fetch-metadata)
- THEN 返回 403

### Requirement: 测试连接

测试连接用传入凭证直连尝试(连接超时 3s),成功返回 ok;失败返回原因(不暴露密码);不落库、不建连接池。

#### Scenario: 测试连接成功
- GIVEN 传入有效连接信息
- WHEN 触发测试连接
- THEN 3s 超时内尝试直连,成功返回 ok

#### Scenario: 测试连接失败不暴露密码
- GIVEN 传入无效连接信息
- WHEN 测试连接
- THEN 返回失败原因,不暴露密码,不落库不建池

### Requirement: 动态路由与查询

查询/EXPLAIN 请求可携带 datasourceKey:为空走默认路径(零回归);非空时严格校验 fail-closed(存在+启用+归属当前租户),以动态数据源执行,防御层与默认路径一致;连接池随数据源变更失效。

#### Scenario: datasourceKey 为空走默认路径
- GIVEN 查询请求未携带 datasourceKey
- WHEN 执行查询
- THEN 走默认数据源 + 租户 schema 路由,行为不变

#### Scenario: datasourceKey 非空严格校验 fail-closed
- GIVEN 请求携带 datasourceKey
- WHEN 校验
- THEN 校验存在、启用、且归属当前租户;任一不满足拒绝(不静默回退默认路径)

#### Scenario: 动态数据源执行防御层一致
- GIVEN datasourceKey 校验通过
- WHEN 执行 SQL
- THEN data_scope 改写 + SQL 防火墙校验 + search_path 设置(default_schema, public)+ 结果集行数上限(默认 1000)
- AND search_path 设置与查询在同一事务内(事务级,不污染连接池)
- AND 与默认路径防御层完全一致

#### Scenario: 数据源变更后连接池失效
- GIVEN 数据源被删除/禁用/连接信息变更
- WHEN 后续查询到达
- THEN 对应连接池关闭失效,后续按校验规则重新校验并拒绝

#### Scenario: 动态连接池只读配置
- GIVEN 动态数据源建立连接池
- THEN 连接级只读开启,池大小受限(默认 5)

#### Scenario: 禁用数据源后再查被拒
- GIVEN 数据源被禁用
- WHEN 再次查询该数据源
- THEN fail-closed 拒绝

### Requirement: 元数据抓取(introspection)

`POST /system/datasource/{key}/fetch-metadata` 连接目标库查 information_schema 自动发现表与列,自动生成知识条目并 embedding;本端点是超管管理操作,允许跨租户;datasource_key 与租户归属创建后不可修改。

#### Scenario: 抓取元数据并沉淀知识
- GIVEN 超管已登录
- WHEN 触发抓取元数据
- THEN 连接目标库查 information_schema(限 default_schema 的 BASE TABLE,排除系统 schema)
- AND 自动发现表与列(含注释),每张表生成知识条目(tenant 取数据源所属租户,datasource_key 维度)并 embedding
- AND 重复抓取幂等(upsert),返回发现表数与清单

#### Scenario: datasource_key 与租户归属不可修改
- GIVEN 数据源已创建
- WHEN 尝试修改 datasource_key 或租户归属
- THEN 拒绝(key 是知识/连接池/前端切换器的全局标识,改名会使旧 key 知识静默失效;租户归属是安全字段)
- AND 换 key/换归属走新建+删除

#### Scenario: 超管跨租户管理
- GIVEN 超管管理操作
- WHEN 调用抓取元数据
- THEN 允许跨租户管理,不要求「数据源租户==当前租户」

### Requirement: RAG datasource 维度

知识条目以 (tenant, datasource_key, schema_name, table_name) 唯一;三路召回、query_history、审计均按 tenant + datasource_key 双过滤;Agent 回调透传 datasourceKey(标识符非凭证);知识上传与查询入口对 datasourceKey 做存在+启用+归属校验。

#### Scenario: 知识与历史双过滤隔离
- GIVEN 多租户多数据源的知识与历史 SQL
- WHEN 三路召回(向量/全文/历史)
- THEN 均按 tenant + datasource_key 双过滤
- AND A 库历史 SQL 不得注入 B 库查询的提示(防跨数据源 SQL 污染)

#### Scenario: 审计记录 datasourceKey
- GIVEN 查询携带 datasourceKey
- WHEN 审计落库
- THEN 审计行含 datasource_key,可溯源实际执行库(NULL=默认路径)

#### Scenario: Agent 回调透传 datasourceKey
- GIVEN Agent retrieve/execute 节点回调控制面
- THEN datasourceKey 随回调透传(标识符非凭证,可透传),为空时控制面按默认处理

#### Scenario: 查询入口 fail-fast 预检
- GIVEN 查询入口
- WHEN 携带 datasourceKey
- THEN 预检存在+启用+归属当前租户,fail-fast;伪造/越权 key 直接拒绝,不进入 LLM 链路

### Requirement: 管理 UI

数据源管理页提供分页列表与增删改查、测试连接、抓取元数据;数据源选项接口返回当前租户启用数据源(不含连接信息);对话页提供数据源切换器。

#### Scenario: 管理页全功能
- GIVEN 超管访问数据源管理页
- THEN 分页列表 + 新增/编辑弹窗 + 测试连接 + 抓取元数据 + 删除
- AND 编辑时密码留空表示不修改;查库确认密码非明文

#### Scenario: 数据源选项接口
- GIVEN 任何登录用户(无需 manage 权限)
- WHEN 拉取选项
- THEN 返回当前租户启用数据源的 [{datasourceKey, name}],不含连接信息

#### Scenario: 对话页数据源切换器
- GIVEN 对话页租户切换器旁
- THEN 提供数据源切换器;选中 key 随查询请求发送
- AND 切换租户时重置为该租户数据源列表首个
