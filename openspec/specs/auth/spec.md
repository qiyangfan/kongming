# auth 能力域

## Purpose

孔明的唯一人类身份来自 Keycloak OIDC `sub`;本地只保存授权资料(`sys_user.id=sub`)、
角色/租户/权限,以及本地签发的 access/refresh。账户密码、注册、改密与本地社交
provider 均已删除(ADR-0010)。

## Requirements

### Requirement: 唯一登录入口

孔明 SHALL 只提供 Keycloak OIDC 登录;未配置 Keycloak 时登录入口不出现,MUST NOT 提供任何
本地账密、注册、改密或其它 OAuth provider。

#### Scenario: 本地端点不存在

- WHEN 调用 `/auth/login` 或 `/auth/password/change`
- THEN 返回 404/405

#### Scenario: 只显示已配置 provider

- WHEN 登录页读取 `/auth/options`
- THEN 仅返回已配置的 `keycloak`;`hufu` 不再出现

### Requirement: subject 主体键

首次 Keycloak 登录 SHALL 以 `id_token.sub` 作为 `sys_user.id` 建档;MUST NOT 生成新的本地
用户 ID,也 MUST NOT 按 email/username 合并既有账号。

#### Scenario: 首登建档

- GIVEN 某 Keycloak sub 首次回调
- WHEN id_token 验签通过
- THEN 创建 `sys_user(id=sub)`、DEVELOPER 角色与 default 租户
- AND `user_identities(provider=KEYCLOAK, external_id=sub)` 唯一

#### Scenario: 重复登录幂等

- WHEN 同一 sub 再次登录
- THEN 复用同一 `sys_user` 行,不新增账号

### Requirement: OIDC 回调安全

回调 MUST 校验 state 单次、PKCE S256、nonce、iss/aud/exp 与 JWKS 签名;错误不得
回显 token/code/secret。登录发起 MUST 按客户端 IP 限流,超限返回 429。

#### Scenario: 登录发起限流
- GIVEN 同一 IP 超过每分钟上限
- WHEN 再次请求 `/auth/{provider}/start`
- THEN 返回 429,不写 state

#### Scenario: state 重放
- WHEN 同一 state 再次使用
- THEN 拒绝

#### Scenario: 回调必须绑定发起浏览器
- GIVEN 发起登录时已下发 state cookie
- WHEN 回调缺少或不匹配该 cookie
- THEN 拒绝,不建立会话

### Requirement: 本地令牌与 refresh

登录成功后 SHALL 签发本地 RS256 access/refresh;refresh SHALL 轮换并在超窗重放时
吊销全家族;refresh token SHALL NOT 落浏览器可读存储,只经 HttpOnly cookie 往返;
登出 SHALL 吊销当前 refresh 家族,并在 Keycloak 可用时返回统一登出地址。

#### Scenario: 旧 refresh 重放

- GIVEN R1 已轮换出 R2
- WHEN R1 超出宽限再次提交
- THEN 家族吊销,刷新返回 401

#### Scenario: 统一登出

- WHEN 已登录用户退出
- THEN 本地 refresh 家族吊销,cookie 清除,返回 Keycloak end_session 地址供前端跳转

#### Scenario: 登出不因权威故障失败

- GIVEN Keycloak 发现文档不可达
- WHEN 用户退出
- THEN 本地 refresh 家族仍吊销,响应 200,redirect_url 为空由前端回落登录页

### Requirement: 授权留本地

`/auth/me` SHALL 按 subject 实时返回本地角色、权限点与租户;token MUST NOT 携带产品
授权 claim;禁用/软删账号 SHALL 在 me/refresh 处被拒绝。

#### Scenario: 禁用账号
- WHEN 已登录账号被禁用
- THEN 后续 refresh 与 me 均拒绝

### Requirement: 认证 Cookie 覆盖 API 前缀

登录发起与回调下发的 state cookie、登录成功后下发的 refresh cookie SHALL 在浏览器
经 `/api` 前缀访问认证端点时被携带;清除这些 cookie 的指令 SHALL 使用与下发一致的
作用路径。该要求不改变 cookie 名、有效期、HttpOnly、SameSite 与 Secure 策略。

#### Scenario: 经 API 前缀完成回调与刷新

- GIVEN 浏览器经 `/api` 前缀发起登录并在 `/api/auth/keycloak/callback` 提交 state
- WHEN 服务端下发 state 与 refresh cookie
- THEN 后续 `/api/auth/refresh` 请求携带 refresh cookie

#### Scenario: 直连后端路径不受影响

- GIVEN 非浏览器客户端直接请求 `/auth/*`
- WHEN 携带同一 cookie
- THEN 认证流程照常接受该 cookie
