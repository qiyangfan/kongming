# auth 能力域

## Purpose

认证与令牌生命周期:账密登录(锁定/禁用/首登改密)、JWT 签发与 refresh 轮换吊销、
当前用户信息、自助改密、联邦登录(连接器架构 ADR-006,OAuth2 家族 provider 可插拔)。
授权模型(角色/权限点/数据范围)归 rbac 域;本域只管"你是谁"与令牌的发放、轮换、作废。

## ADDED Requirements

### Requirement: 账密登录

系统 SHALL 提供用户名+密码登录,凭证校验通过签发令牌对(access + refresh)。
用户名不存在与密码错误 SHALL 返回无差别的 401(不泄露账号是否存在);
连续失败按账号计数,达到 5 次 SHALL 锁定 15 分钟,锁定期间含正确密码在内的一切尝试均被拒;
登录成功 SHALL 清零失败计数并刷新最近登录时间。

#### Scenario: 正常登录
- GIVEN 存在 ACTIVE 状态的本地账号,密码已 argon2id 哈希落库
- WHEN 提交正确的用户名与密码
- THEN 校验通过,返回令牌对(access_token / refresh_token / token_type=Bearer)
- AND 失败计数清零,last_login_at 刷新

#### Scenario: 凭证错误无差别拒绝
- GIVEN 用户名不存在,或存在但密码错误
- WHEN 提交登录
- THEN 两种情形均返回 401,响应文案无差别
- AND 不签发任何令牌;密码错误时失败计数 +1

#### Scenario: 连续失败锁定
- GIVEN 某账号已连续失败 4 次
- WHEN 第 5 次失败
- THEN 账号锁定 15 分钟
- AND 锁定期间任何登录尝试(含正确密码)返回 429 并提示锁定时长
- AND 锁定到期后自动解除

#### Scenario: 禁用账号登录被拒
- GIVEN 账号 status=DISABLED 或已软删
- WHEN 提交登录(密码正确)
- THEN 返回 403,提示账号已禁用
- AND 不签发令牌,不计入失败锁定计数

#### Scenario: 首登强制改密标志
- GIVEN 账号 password_changed_at 为空(种子缺省密码或管理员重置后)
- WHEN 登录成功
- THEN 响应携带 must_change_password=true
- AND 前端据此前置跳转改密流程

### Requirement: 令牌签发与 refresh 轮换

access 令牌 SHALL 短期有效,refresh 令牌 SHALL 长期有效并支持轮换:
每次刷新 SHALL 签发全新令牌对并使旧 refresh 立即失效;
已失效 refresh 的再次使用 SHALL 视为泄漏信号,吊销该令牌所属的整个家族
(同家族全部 refresh 立即不可用;已签发的 access 待其自然过期,不维护黑名单)。

#### Scenario: 正常轮换
- GIVEN 用户持有有效 refresh 令牌
- WHEN 调用刷新
- THEN 返回全新令牌对
- AND 旧 refresh 令牌即刻失效

#### Scenario: 旧票复用检测吊销家族
- GIVEN refresh 令牌 R1 已轮换出 R2(R1 已失效)
- WHEN 攻击者持 R1 再次刷新
- THEN 返回 401
- AND 该家族全部 refresh 令牌(含 R2)被吊销,用户须重新登录

#### Scenario: 登出吊销
- GIVEN 用户已登录
- WHEN 调用登出(携带 refresh 令牌)
- THEN 该 refresh 所属家族被吊销
- AND 之后用该家族任何 refresh 刷新均返回 401

#### Scenario: 类型混用拒绝
- GIVEN 一枚 refresh 令牌
- WHEN 当作 access 令牌访问受保护接口(或反之)
- THEN 返回 401(令牌类型声明不匹配)

### Requirement: 当前用户信息

系统 SHALL 提供当前用户查询,凭有效 access 令牌返回:
用户标识、用户名、昵称、角色键列表、权限点列表(角色并集、去重)、
可切换租户清单、must_change_password 标志。

#### Scenario: 返回真实角色与权限点
- GIVEN 用户经 sys_user_role 绑定角色,角色经 sys_role_permission 绑定权限点
- WHEN 查询当前用户信息
- THEN 返回真实角色键列表与权限点并集(无任何角色绑定时为空列表,查询本身成功)

#### Scenario: 返回租户清单
- GIVEN 用户经 sys_user_tenant 绑定租户
- WHEN 查询当前用户信息
- THEN 返回可切换租户清单(tenant_key / tenant_name),供前端租户切换器使用

#### Scenario: 禁用用户的既有令牌被拒
- GIVEN 用户令牌仍在有效期内,但账号已被禁用或软删
- WHEN 查询当前用户信息
- THEN 返回 401(账号状态按请求实时核查,不缓存于令牌)

#### Scenario: 未认证拒绝
- GIVEN 无令牌或令牌无效/过期
- WHEN 查询当前用户信息
- THEN 返回 401

### Requirement: 自助改密

已认证用户 SHALL 可凭当前密码自助改密:
新密码 SHALL 以 argon2id 哈希落库并刷新 password_changed_at;
改密成功 SHALL 吊销该用户全部 refresh 家族(其他设备强制重新登录)。

#### Scenario: 正常改密
- GIVEN 已认证用户,提供正确的当前密码
- WHEN 提交新密码
- THEN 新密码 argon2id 哈希落库,password_changed_at 刷新
- AND must_change_password 标志消除,该用户全部 refresh 家族被吊销

#### Scenario: 当前密码错误
- GIVEN 已认证用户
- WHEN 提交的当前密码错误
- THEN 返回 400,密码不变

#### Scenario: 无本地密码的联邦账号
- GIVEN 纯联邦账号(password_hash 为空)
- WHEN 尝试自助改密
- THEN 返回 400 并提示该账号无本地密码

### Requirement: 联邦登录(连接器形态)

系统 SHALL 以 provider 可插拔形态提供 OAuth2 联邦登录:
`GET /auth/options` 只返回已配置(凭证齐备)的 provider 清单,前端按钮数据驱动;
`/auth/{provider}/start` 返回授权地址并下发一次性 state(有时效,用后即毁);
`/auth/{provider}/callback` 凭 code+state 完成交换并签发本平台令牌对,
OAuth access token SHALL 只在后端存活(前端经手 code,永不经手 token);
未注册或未配置的 provider SHALL 一律 404。

#### Scenario: options 只含已配置 provider
- GIVEN GitHub 凭证已配置、Google 未配置
- WHEN 查询 /auth/options
- THEN 清单含 GitHub、不含 Google
- AND 前端据此渲染/隐藏对应登录按钮

#### Scenario: start 下发一次性 state
- GIVEN 某已配置 provider
- WHEN 调用 start
- THEN 返回授权地址,URL 携带 state 参数
- AND 该 state 存于服务端并带时效;对声明 PKCE 的 provider,URL 另携带 code_challenge

#### Scenario: state 一次性校验
- GIVEN 一个已使用过的 state(或已过期、或伪造)
- WHEN 携带该 state 回调
- THEN 拒绝(401),不交换 code

#### Scenario: 未配置 provider 不可达
- GIVEN 某 provider 未配置凭证或根本未注册
- WHEN 调用其 start 或 callback
- THEN 返回 404

#### Scenario: 回调成功签发令牌
- GIVEN 有效 code 与未使用 state
- WHEN 回调完成
- THEN 后端以 code 换 token、拉取 profile、完成账号关联
- AND 返回本平台令牌对,响应不含 OAuth access token

### Requirement: 联邦账号关联与自动建号

联邦 profile 归一化为 `{external_id, username, display_name, email?, email_verified?, avatar_url?}` 后,
SHALL 按序关联:按 (provider, external_id) 命中既有身份绑定直接登录;
未命中且 email 已被联邦方验证、且与既有本地账号 email 一致时,绑定该身份到既有账号后登录;
均未命中 SHALL 自动建号并绑定默认角色与默认租户。
同一用户 SHALL 可绑定多路联邦身份。

#### Scenario: 身份命中直接登录
- GIVEN (provider, external_id) 已有身份绑定
- WHEN 联邦回调完成
- THEN 直接以绑定用户登录,不重复建号

#### Scenario: 已验证 email 关联既有账号
- GIVEN 本地账号 email=a@b.c,联邦 profile 的 email=a@b.c 且联邦方标记已验证
- WHEN 该 provider 首次回调
- THEN 新身份绑定到该既有账号(不新建用户),以其登录

#### Scenario: 未验证 email 不关联
- GIVEN 联邦 profile 的 email 未被联邦方验证
- WHEN 回调
- THEN 不按 email 关联,走自动建号(防邮箱伪造劫持)

#### Scenario: 自动建号
- GIVEN 身份与 email 均未命中
- WHEN 回调完成
- THEN 自动创建用户(用户名冲突时生成不重复的用户名)、绑定默认角色与默认租户
- AND 操作日志记录注册事件;后续权限点校验、租户白名单与本地账密用户无差异

#### Scenario: 多身份绑定
- GIVEN 用户已绑定 GitHub 身份
- WHEN 同 email(已验证)的 Google 身份回调
- THEN Google 身份绑定到同一用户,两路联邦后续均可登录该账号

#### Scenario: 联邦账号被禁用
- GIVEN 联邦账号已建号后被禁用
- WHEN 再次经联邦登录
- THEN 返回 403,不签发令牌
