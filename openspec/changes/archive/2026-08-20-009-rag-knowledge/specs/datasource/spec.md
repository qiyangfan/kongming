# datasource 能力域 delta(009)

## MODIFIED Requirements

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

## REMOVED Requirements

### Requirement: 元数据抓取(introspection)

**Reason**: 元数据抓取的行为契约随 rag/knowledge 域首次兑现而收编——端点定形为
`POST /system/rag/fetch-metadata`(租户视角 + system:rag:manage,经 Agent 面
润色),归 knowledge 域「LLM 润色元数据」requirement 立约;「datasource_key
与租户归属不可改」scenario 挪位至本域「数据源注册与凭证加密」requirement
(008 挂账清偿,强制已兑现)。

**Migration**: 原 `POST /system/datasource/{key}/fetch-metadata` 路径从未实现
(008 未兑现该端点),无存量调用方,零迁移成本。原「超管跨租户管理」scenario
的跨租户能力不减但操作形态变化:超管切换租户上下文(X-Tenant-Id)后即可对
该租户的数据源操作——注意租户白名单 SUPER_ADMIN 不豁免(chat spec 立约),
超管须先经用户管理面绑定目标租户(seed 仅绑默认租户)方可切换。
