## MODIFIED Requirements

### Requirement: 令牌签发与 refresh 轮换

未启用 IdP provider 时,孔明必须 (MUST) 在本地完成认证并本地签发 RS256 令牌,本地 refresh 家族轮换/重用检测保持现状(standalone 默认)。启用 IdP provider 时,联邦回调必须 (MUST) 以 IdP JWKS 验 id_token(RS256,iss/aud/exp/nonce);验过后仍由本地签发会话令牌,不以 IdP 令牌替代本地会话。孔明 API 必须 (MUST) 只认本地会话令牌,不得 (MUST NOT) 直接采信 IdP access token。两轨并存,任一轨失效不得 (MUST NOT) 阻断另一轨。不得 (MUST NOT) 以共享对称密钥验签。

#### Scenario: 本地轨行为不变

- **GIVEN** 未配置 IdP provider
- **WHEN** 用户以本地账密登录并访问受保护接口
- **THEN** 本地签发/验签与 refresh 轮换行为与变更前一致

#### Scenario: 正常轮换

- **GIVEN** 用户持有本地签发的有效 refresh 令牌
- **WHEN** 调用刷新
- **THEN** 返回全新令牌对,旧 refresh 即刻失效

#### Scenario: 旧票复用检测吊销家族

- **GIVEN** refresh 令牌 R1 已轮换出 R2(R1 已失效)
- **WHEN** 攻击者持 R1 再次刷新
- **THEN** 返回 401,该家族全部 refresh 令牌被吊销

#### Scenario: 登出吊销

- **GIVEN** 用户已登录
- **WHEN** 调用登出(携带 refresh 令牌)
- **THEN** 该 refresh 所属家族被吊销,之后任何刷新返回 401

#### Scenario: 类型混用拒绝

- **GIVEN** 一枚 refresh 令牌
- **WHEN** 当作 access 令牌访问受保护接口(或反之)
- **THEN** 返回 401(令牌类型声明不匹配)

#### Scenario: 验 id_token

- **GIVEN** IdP 签发的 id_token
- **WHEN** 联邦回调验签
- **THEN** 以 JWKS 公钥 + iss/aud/exp/nonce 校验通过;无效/过期/aud 或 nonce 不符拒绝 401

#### Scenario: IdP access token 不作孔明凭据

- **GIVEN** 仅持 IdP 签发的 access token
- **WHEN** 调用孔明受保护接口
- **THEN** 返回 401(孔明 API 只认本地会话令牌)

#### Scenario: IdP 不可达回落本地

- **GIVEN** IdP 权威不可达
- **WHEN** 用户以本地账密登录
- **THEN** 认证成功,联邦轨故障不阻断本地轨

### Requirement: 联邦登录(连接器形态)

孔明 SHALL 在IdP provider **已配置**时经授权码 + PKCE 联邦到IdP(OIDC),回调按 IdP 发现文档取 JWKS 验 id_token(iss/aud/nonce);未配置时该 provider 不注册、不出现在登录清单,孔明按本地认证运行。OAuth token SHALL 只在后端存活,前端经手 code 永不经手 token。
`GET /auth/options` 只返回已配置的 provider 清单(本地认证 + 已配置的外部/IdP provider);`/auth/{provider}/start` 返回授权地址并下发一次性 state(有时效,用后即毁);`/auth/{provider}/callback` 凭 code+state 完成交换、验 id_token 与影子映射;未注册或未配置的 provider SHALL 一律 404。

#### Scenario: RP 登录

- **WHEN** IdP provider 已配置且用户经孔明发起登录
- **THEN** 重定向到IdP认证,后端以 code + PKCE 换 token

#### Scenario: 跨域 SSO

- **GIVEN** 用户已在IdP域认证且孔明已配置IdP provider
- **WHEN** 经孔明登录
- **THEN** 经 IdP 会话无感 SSO

#### Scenario: options 只含已配置 provider

- **GIVEN** IdP provider 已配置,本地认证可用,某外部 provider 未配置
- **WHEN** 查询 /auth/options
- **THEN** 清单含本地认证与 IdP provider,不含未配置的 provider

#### Scenario: start 下发一次性 state

- **GIVEN** IdP provider 已配置
- **WHEN** 调用 start
- **THEN** 返回授权地址,URL 携带 state 与 PKCE code_challenge

#### Scenario: state 一次性校验

- **GIVEN** 一个已使用过的 state(或已过期、或伪造)
- **WHEN** 携带该 state 回调
- **THEN** 拒绝(401),不交换 code

#### Scenario: 未配置 provider 不可达

- **GIVEN** IdP provider 未配置或根本未注册
- **WHEN** 调用其 start 或 callback
- **THEN** 返回 404

#### Scenario: 回调成功签发令牌

- **GIVEN** 有效 code 与未使用 state
- **WHEN** 回调完成
- **THEN** 后端以 code 换 token、验 id_token 并完成影子映射,签发本地会话令牌
- **AND** 响应不含 OAuth access token(令牌不经手前端)

### Requirement: 联邦账号关联与自动建号

联邦身份 SHALL 以 OIDC `sub`(IdP 稳定 UUID)映射本地影子 `sys_user`(`idp_subject`);首次见新 `sub` SHALL JIT 建影子档案并绑默认角色与默认租户(本地授权动作)。同一 `sub` SHALL 恒映射同一本地档案。
权威 profile 归一化为 `{external_id, username, display_name, email?, email_verified?, avatar_url?}` 后,email 已由权威验证且与既有本地账号一致时 SHALL 关联该账号;均未命中 SHALL 自动建号。同一用户 SHALL 可绑定多路联邦身份。

#### Scenario: `sub` 命中影子档案

- **GIVEN** idp_subject 已映射
- **WHEN** 联邦回调完成
- **THEN** 以既有影子档案登录,不重复建号

#### Scenario: 首登 JIT 建档案

- **GIVEN** 新 `sub` 未映射
- **WHEN** 首次登录
- **THEN** 建影子档案 + 默认 DEVELOPER 角色 + default 租户,落注册审计

#### Scenario: 身份命中直接登录

- **GIVEN** (provider, external_id) 已有身份绑定
- **WHEN** 联邦回调完成
- **THEN** 直接以绑定用户登录,不重复建号

#### Scenario: 已验证 email 关联既有账号

- **GIVEN** 本地账号 email=a@b.c,权威 profile 的 email=a@b.c 且权威方标记已验证
- **WHEN** 该 provider 首次回调
- **THEN** 新身份绑定到该既有账号(不新建用户),以其登录

#### Scenario: 未验证 email 不关联

- **GIVEN** 联邦 profile 的 email 未被权威方验证
- **WHEN** 回调
- **THEN** 不按 email 关联,走自动建号(防邮箱伪造劫持)

#### Scenario: 自动建号

- **GIVEN** 身份与 email 均未命中
- **WHEN** 回调完成
- **THEN** 自动创建影子档案(用户名冲突时生成不重复的用户名)、绑定默认角色与默认租户
- **AND** 操作日志记录注册事件;后续权限点校验、租户白名单与本地账号无差异

#### Scenario: 多身份绑定

- **GIVEN** 用户已绑定一路联邦身份
- **WHEN** 同 email(已验证)的另一路身份回调
- **THEN** 该身份绑定到同一用户,后续任一路均可登录该账号

#### Scenario: 联邦账号被禁用

- **GIVEN** 联邦账号已建号后被禁用
- **WHEN** 再次经联邦登录
- **THEN** 返回 403,不签发令牌

### Requirement: 当前用户信息

系统 SHALL 在当前凭据轨验签后返回本地用户标识/用户名/昵称/角色键列表/权限点列表/可切换租户清单/must_change 标志;授权数据来自本地 rbac/multitenant,不依赖IdP role claim。账号状态 SHALL 按请求实时核查。

#### Scenario: 返回真实角色与权限点

- **GIVEN** 用户经本地 sys_user_role 绑定角色,角色经 sys_role_permission 绑定权限点
- **WHEN** 查询当前用户信息
- **THEN** 返回本地真实角色键列表与权限点并集(无任何角色绑定时为空列表,查询本身成功)

#### Scenario: 禁用用户的既有令牌被拒

- **GIVEN** 令牌仍在有效期内,但本地影子档案已被禁用或软删
- **WHEN** 查询当前用户信息
- **THEN** 返回 401(本地账号状态按请求实时核查,不缓存于令牌)

#### Scenario: 返回租户清单

- **GIVEN** 用户经 sys_user_tenant 绑定租户
- **WHEN** 查询当前用户信息
- **THEN** 返回可切换租户清单(tenant_key / tenant_name),供前端租户切换器使用

#### Scenario: 未认证拒绝

- **GIVEN** 无令牌或令牌无效/过期
- **WHEN** 查询当前用户信息
- **THEN** 返回 401

## ADDED Requirements

### Requirement: 可选联邦与 standalone 隔离

IdP provider 必须 (MUST) 为可选:未配置时孔明认证行为必须 (MUST) 与变更前一致;启用必须 (MUST) 仅新增 provider 与影子映射,不得 (MUST NOT) 移除本地账密/自助改密/OAuth/本地 refresh。provider 移除后必须 (MUST) 回落本地认证,无残留依赖。启用/停用必须 (MUST) 仅经配置,不得 (MUST NOT) 依赖 IdP 代码/库/数据库,也不得 (MUST NOT) 调用标准 OIDC 之外的私有端点。

#### Scenario: 未配置回落本地

- **GIVEN** IdP provider 未配置
- **WHEN** 用户登录
- **THEN** 登录清单与流程与变更前一致,不含IdP入口

#### Scenario: 关闭即回退

- **GIVEN** IdP provider 曾启用并已登录过联邦用户
- **WHEN** 移除 provider 配置
- **THEN** 登录清单回落本地,联邦会话按本地会话过期策略结束,不影响本地认证

#### Scenario: 仅消费标准 OIDC 端点

- **GIVEN** IdP provider 已配置
- **WHEN** 完成一次联邦登录
- **THEN** 对 IdP 的调用仅含发现文档/JWKS/授权码/token 标准端点,不含任何私有端点

### Requirement: 统一权威令牌验签

孔明联邦路径 SHALL 以 IdP JWKS 公钥验 id_token(RS256),校验 iss/aud/exp/nonce,并以 `sub` 为权威主体;孔明 API SHALL NOT 直接采信 IdP access token;不得 (SHALL NOT) 以共享对称密钥验签。

#### Scenario: aud 不符拒绝

- **WHEN** 联邦 id_token 的 aud 不等于本 RP 的 client_id
- **THEN** 拒绝 401

#### Scenario: nonce 不符拒绝

- **WHEN** 联邦 id_token 的 nonce 与本次授权下发的 nonce 不一致
- **THEN** 拒绝 401

### Requirement: 影子档案映射

孔明 SHALL 保留本地 `sys_user` 作影子档案(UUID 不可重键),以 `idp_subject` 列映射权威 `sub`;本地资产经影子档案归属,不得 (SHALL NOT) orphan。

#### Scenario: 资产不 orphan

- **GIVEN** 用户既有本地资产(chat_session 等)
- **WHEN** 经联邦登录
- **THEN** 经 `sub`→影子映射归属既有资产

### Requirement: 联邦登出联动(RP-Initiated Logout)

启用 IdP provider 时,孔明 SHALL 在本地登出(本地 refresh 家族已吊销)后,经虎符 `end_session_endpoint` 结束虎符会话,确保再次登录不被 SSO 直接带回。登出跳转 SHALL 由发现文档的 `end_session_endpoint`、已配置 client_id 与登出后回跳地址(孔明登录页)组成;不得 (SHALL NOT) 携带 id_token/access/refresh 等任何令牌,不得 (MUST NOT) 依赖私有端点。本地登出 SHALL NOT 以虎符可达为前提:发现文档不可用或缺失登出端点时不阻断本地登出,直接回落地登录页。未启用 IdP provider 时登出行为 SHALL 与变更前一致。

#### Scenario: 登出后结束虎符会话

- **GIVEN** IdP provider 已配置且用户已登录
- **WHEN** 用户登出
- **THEN** 本地 refresh 家族先被吊销,随后跳转虎符 `end_session_endpoint`(携带 client_id 与登出回跳地址)
- **AND** 虎符会话被清除

#### Scenario: 再次登录需重新认证

- **GIVEN** 用户已完成上述登出跳转
- **WHEN** 再次发起统一身份登录
- **THEN** 虎符要求重新输入凭据,不直接回跳授权码(SSO 带回被阻断)

#### Scenario: 跳转地址不含令牌

- **GIVEN** 登出跳转地址已生成
- **WHEN** 检查该地址参数
- **THEN** 不含 id_token/access_token/refresh_token 任一令牌

#### Scenario: 未启用 IdP 登出不变

- **GIVEN** IdP provider 未配置
- **WHEN** 用户登出
- **THEN** 本地 refresh 家族吊销行为与变更前一致,直接回落地登录页,无外跳

#### Scenario: 虎符不可达不阻断本地登出

- **GIVEN** 发现文档不可达或未声明 `end_session_endpoint`
- **WHEN** 用户登出
- **THEN** 本地 refresh 家族仍被吊销,直接回落地登录页,不报错
