# datasource 能力域

## Purpose

动态数据源管理:超管注册外部 **PostgreSQL** 业务库(PG-only,方言能力储备见 ADR-001 附注),凭证 AES-GCM 加密落库、DTO 不回传敏感字段,支持测试连接、元数据抓取生成知识,RAG 与查询链路按 datasourceKey 分维度动态路由,无需重启接入新业务库。

## Requirements

### Requirement: 数据源注册与凭证加密

超管可注册 PostgreSQL 业务库(PG-only,方言能力储备见 ADR-001 附注),密码落库前必须加密;管理端点要求权限点 system:datasource:manage(仅 SUPER_ADMIN);返回 DTO 不含密码密文。
数据源字段:datasource_key/name/host/port/database/username/password/default_schema/租户归属。name SHALL 用于选项与列表展示;default_schema SHALL 为动态路径 search_path 首段,且 SHALL 通过 PG 标识符合法性校验(未加引号小写折叠语义,`^[a-z_][a-z0-9_]{0,62}$`),防 search_path 注入。
datasource_key 传输层 SHALL 匹配 `^[a-z][a-z0-9_]{1,63}$`;保留字 `default` SHALL NOT 被注册——default 为内置默认路径标识(走平台配置连接串),不登记为 sys_datasource 行。
管理端点(page/create/update/enable/disable/delete/test)SHALL 为全局视角:X-Tenant-Id 对其无语义(与身份治理同一惯例);创建数据源时租户归属 SHALL 由超管显式指定,SHALL NOT 取自客户端隐式上下文;page/编辑/启停/删除不限当前租户。选项接口(options)为登录即可的用户面接口,不在管理端点之列。
datasource_key 与租户归属创建后 SHALL NOT 可修改(009 起 008 已兑现的强制随本 delta 挪位至注册 requirement 立约)。

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
- WHEN 访问任一管理端点(page/create/update/enable/disable/delete/test;options 为登录即可的只读选项接口,不在此列)
- THEN 返回 403

#### Scenario: 创建时租户归属显式指定
- GIVEN 超管创建数据源
- WHEN 提交表单
- THEN 租户归属取表单显式选择的租户(服务端校验该租户存在且启用),不取 X-Tenant-Id 等隐式上下文
- AND 列表/编辑/启停/删除操作不受当前租户上下文限制(全局视角)

#### Scenario: 保留字与非法 key 拒绝
- GIVEN 注册或编辑请求
- WHEN datasource_key 为 `default`、含大写/连字符/空白等不匹配 pattern 的形态
- THEN 拒绝(400),不落库

#### Scenario: default_schema 注入防护
- GIVEN 注册或编辑请求
- WHEN default_schema 含引号/分号/空白等非法标识符字符
- THEN 拒绝(400),不落库

#### Scenario: datasource_key 与租户归属不可修改
- GIVEN 数据源已创建
- WHEN 尝试修改 datasource_key 或租户归属
- THEN 拒绝(key 是知识/连接池/前端切换器的全局标识,改名会使旧 key 知识静默失效;租户归属是安全字段)
- AND 换 key/换归属走新建+删除

### Requirement: 测试连接

测试连接用传入凭证直连尝试(连接超时 3s),成功返回 ok;失败返回原因(不暴露密码);不落库、不建连接池、不落审计。编辑已存在数据源时密码留空 SHALL 以库存密文解密补位(密文不回显);密文不可解(密钥轮换/损坏)时 SHALL 按「凭证不可用」类失败返回,不暴露密文与密钥细节。

#### Scenario: 测试连接成功
- GIVEN 传入有效连接信息
- WHEN 触发测试连接
- THEN 3s 超时内尝试直连,成功返回 ok

#### Scenario: 测试连接失败不暴露密码
- GIVEN 传入无效连接信息
- WHEN 测试连接
- THEN 返回失败原因,不暴露密码,不落库不建池

#### Scenario: 编辑态密码留空补位
- GIVEN 编辑已存在数据源,密码字段留空
- WHEN 触发测试连接
- THEN 服务端取库存密文解密补位后直连,全程不回显密码/密文
- AND 密文不可解时返回「凭证不可用」类失败原因,不暴露敏感细节

### Requirement: 动态路由与查询

查询/EXPLAIN 请求可携带 datasourceKey:为空走默认路径(零回归);非空时严格校验 fail-closed(存在+启用+归属当前租户),以动态数据源执行,防御层与默认路径一致;连接池随数据源变更失效(定向粒度与触发时点见「数据源启停、删除与连接池生命周期」)。对非法 key 的拒绝 SHALL 同形(不区分不存在/禁用/越属,防 key 探测)。

#### Scenario: datasourceKey 为空走默认路径
- GIVEN 查询请求未携带 datasourceKey
- WHEN 执行查询
- THEN 走默认数据源 + 租户 schema 路由,行为不变

#### Scenario: datasourceKey 非空严格校验 fail-closed
- GIVEN 请求携带 datasourceKey
- WHEN 校验
- THEN 校验存在、启用、且归属当前租户;任一不满足拒绝(不静默回退默认路径,不区分具体原因)

#### Scenario: 动态数据源执行防御层一致
- GIVEN datasourceKey 校验通过
- WHEN 执行 SQL
- THEN data_scope 改写 + SQL 防火墙校验 + search_path 设置(default_schema, public)+ 结果集行数上限(默认 1000)
- AND search_path 设置与查询在同一事务内(事务级,不污染连接池);default_schema 已经标识符合法性校验,不构成注入面
- AND 与默认路径防御层完全一致(保留表拒读、危险函数拒用等全部生效)

#### Scenario: 动态连接池只读配置
- GIVEN 动态数据源建立连接池
- THEN 连接级只读开启,池大小受限(默认 5)

#### Scenario: 数据源变更后连接池失效
- GIVEN 数据源被删除/禁用/连接信息变更
- WHEN 后续查询到达
- THEN 对应连接池已关闭失效(仅目标 key 定向失效,提交后生效),后续按校验规则重新校验并拒绝或按最新连接信息重建

#### Scenario: 禁用数据源后再查被拒
- GIVEN 数据源被禁用
- WHEN 再次查询该数据源
- THEN fail-closed 拒绝

### Requirement: 数据源启停、删除与连接池生命周期

数据源禁用 SHALL 立即使该 key 连接池定向失效,后续查询 fail-closed 拒绝;启用后池按最新连接信息惰性重建。连接信息(host/port/database/username/password/default_schema)任一变更 SHALL 使该 key 池定向失效;仅改 name 等展示字段 SHALL NOT 触碰连接池。删除为物理删除(无软删),删除后该 key 池关闭失效,后续查询按不存在拒绝。池失效 SHALL 在管理事务提交后执行(防建池快照与库内状态错位产生僵尸池),且 SHALL 仅影响目标 key,其余数据源池不受扰动。管理面全部写操作(create/update/delete/enable/disable)SHALL 落 sys_oper_log 审计并与业务变更同事务;测试连接为无状态操作,SHALL NOT 落审计、SHALL NOT 建池。

#### Scenario: 禁用即拒、启用恢复
- GIVEN 数据源 sakila 已启用且有存活连接池
- WHEN 超管禁用该数据源
- THEN 该 key 池被关闭,后续查询 fail-closed 拒绝
- WHEN 重新启用
- THEN 后续查询按最新连接信息重建池执行

#### Scenario: 连接信息变更触发池失效
- GIVEN 数据源 sakila 池已建立
- WHEN 超管修改其密码并保存
- THEN 保存提交后旧池关闭,下一查询用新凭证重建池
- AND 其他数据源的池不受影响

#### Scenario: 仅改展示字段不动池
- GIVEN 数据源 sakila 池已建立
- WHEN 仅修改 name 保存
- THEN 连接池不失效不重建

#### Scenario: 物理删除并回收
- GIVEN 数据源 sakila 存在
- WHEN 超管删除
- THEN 记录物理删除,池关闭,后续查询按不存在拒绝
- AND sys_oper_log 落 datasource.delete 审计

#### Scenario: 写操作审计同事务
- GIVEN 管理面任一写操作(create/update/delete/enable/disable)
- WHEN 业务变更提交
- THEN 审计行与业务行在同一事务落库;测试连接不落审计

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

数据源管理页 SHALL 提供分页列表与增删改查、测试连接(元数据抓取入口随 rag 域
落位知识库管理页,不在本页);数据源选项接口 SHALL 返回首位合成的 default 项
与当前租户启用数据源(均不含连接信息);对话页 SHALL 提供数据源切换器。

#### Scenario: 管理页全功能
- GIVEN 超管访问数据源管理页
- THEN 分页列表 + 新增/编辑弹窗 + 测试连接 + 删除
- AND 编辑时密码留空表示不修改;查库确认密码非明文

#### Scenario: 数据源选项接口
- GIVEN 任何登录用户(无需 manage 权限)
- WHEN 拉取选项
- THEN 返回 [{datasourceKey, name}] 清单:首位为合成的 default 项(内置默认路径标识,非 sys_datasource 行),其后为当前租户启用数据源;均不含连接信息

#### Scenario: 对话页数据源切换器
- GIVEN 对话页租户切换器旁
- THEN 提供数据源切换器;选中 key 随查询请求发送
- AND 切换租户时重置为该租户数据源列表首个
