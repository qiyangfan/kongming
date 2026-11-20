# 设计:孔明可选联邦独立 IdP(镜像)

> 实现选择归 ADR(kongming 宪法);本件只记决策映射与 ADR 更新点。

## 决策映射

- **可选 provider**:复用既有联邦连接器架构(ADR-006,provider 可插拔),把独立 IdP 作为一个**可选 OIDC provider** 接入,与 GitHub/Google 同级;启用/停用只经配置(issuer、client_id、client_secret),无代码分支;未配置即不出现在 `GET /auth/options`,孔明按本地认证 standalone 运行。授权码 + PKCE,token 只在后端存活。库选型(authlib 等)属 ADR-003 更新范畴,不入 spec。
- **本地认证保留**:本地账密/自助改密/OAuth 5 家/本地 RS256 签发/refresh 家族管理维持现状,是 standalone 默认路径;联邦只新增一条 provider 轨,不删除任何本地路径。
- **令牌验签**:本地会话仍本地 RS256 自签自验;联邦回调按 IdP 发现文档取 JWKS,验 id_token(RS256、iss/aud/exp/nonce)与 `sub`,验过后仍签发本地会话令牌。API 面只认本地会话令牌:不把 IdP access token 当孔明凭据,避免每个请求引入第二套验签栈,关闭 provider 时也不必处理存量联邦票(ADR-003 补充:联邦 issuer=IdP,本地 issuer 不变)。
- **主体绑定**:以 OIDC 标准 `sub`(虎符契约:稳定 UUID)作为权威主体,映射本地影子列。
- **影子映射**:`sys_user.id` UUID v7 被 20+ 表 FK 引用,不可重键 → 保留本地 `sys_user` 作影子档案,加映射列指向 IdP subject。核对 ADR-004 baseline 是否需新增列(只增迁移)。本地资产(chat_session 等)经影子档案归属,不 orphan。
- **JIT 建档案**:联邦路径首登见新 `sub` 建影子档案 + 默认 DEVELOPER + default 租户,是本地授权动作(保留),不上收。
- **授权留本地**:rbac/multitenant/datascope 域与 require_perm 192 处零改;me 返回本地角色/权限/租户。
- **登出联动(RP-Initiated Logout)**:新增 `GET /auth/logout/sso`(增量端点,`POST /auth/logout` 保持 204)。web 完成本地登出(家族已吊销)后整页导航到此端点;端点从发现文档取 `end_session_endpoint`,带 client_id 与登出回跳地址(登录页,新增配置)拼 302;未配置、发现文档不可用或缺端点时直接 302 登录页。不持久化 IdP id_token,跳转不带 `id_token_hint` 等任何令牌。
- **关闭回落**:provider 从配置移除后,联邦轨消失,登录清单回落本地,无残留依赖。

## 发布与互联边界

- **契约源**:虎符 `oidc-authority` spec 定义 issuer、发现文档、JWKS、授权码 + PKCE、`sub`(稳定 UUID)与 profile claims;孔明不定义私有 claim/端点,契约冲突以提供方为准。
- **消费面**:发现文档、JWKS、authorization、token、`end_session_endpoint`(可选 userinfo);claims 只用 `sub`、`preferred_username`、`name`、`email`、`email_verified`;不消费任何角色/租户 claim(授权留本地)。
- **隔离**:不 import IdP 代码、不共享库/DB/签名密钥;provider 未配置时对 IdP 零出站依赖。
- **发布形态**:孔明无 IdP 亦可独立部署(本地认证全保留);虎符独立单二进制(自带存储)发布,同一 issuer 可同时服务平台内三仓与外部 RP,互联无需代码改动。

## ADR 更新点

- ADR-006:联邦连接器 provider 清单新增「统一身份 IdP」(可选);只消费标准 OIDC 契约。
- ADR-007:身份权威改归虎符,「锦官统一身份」演进表述改为「可选联邦待实施」;对账从身份权威取数列为后续演进。
- ADR-003:本地令牌体制不变;补充联邦回调验 IdP id_token、本地会话仍本地签发的表述。

## 待验证

- 生产存量(当前似仅 dev 一套)。
- authlib OIDC RP 能力核实:发现文档解析、JWKS 验 id_token、`nonce` 校验(起草时核 pyproject)。
- 虎符 issuer/JWKS 与客户端注册交付(联调前置)。
- 虎符客户端登记是否支持登出回跳地址白名单(`post_logout_redirect_uri`)——其 spec 未列该字段,联调前需补。
