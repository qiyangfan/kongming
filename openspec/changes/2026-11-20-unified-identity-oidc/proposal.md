# 统一身份:孔明可选联邦独立 IdP(镜像)

> 镜像统一身份契约源(独立 IdP 项目,HANDOFF 2026-11-20 第 8 条);木牛同名镜像(console 可选联邦)。协调登记见 `longzhong/HANDOFF.md`。
> 引用 ADR-002(信任边界=进程边界)、ADR-003(本地令牌/RS256)、ADR-006(联邦连接器)、ADR-007(身份未统一→可选合流)。

## Why

身份碎片化:锦官 `users`、孔明 `sys_user`+`user_identities`、木牛 `admin_users` 三套独立。身份权威定为**独立 IdP 项目**(可独立发布,形态对齐木牛;三仓只做 RP,插拔=配置),锦官不再承担 IdP。孔明 ADR-007 明文「租户身份与锦官用户身份未统一」。

但**解耦热插拔**是硬约束:孔明必须能作为一个独立产品发布运行,联邦 IdP 是**可选增强**而非硬依赖。孔明现有认证已较成熟(RS256 + refresh 家族轮换重用检测 + argon2id + OAuth 5 家 + account-link),这些必须作为 standalone 默认**保留**;IdP 只是 ADR-006 连接器清单里**新增的一个 provider**——启用即 SSO + 身份关联,关闭即回落本地,与 GitHub/Google 同级可插拔。dev 实测存量 sys_user 6(全内部号、email 全 NULL)、user_identities 0、OAuth 绑定 0 → 启用联邦零合并负担。

## What Changes

- 新增「统一身份 IdP」作为**可选** provider(复用 ADR-006 联邦连接器):标准 OIDC 授权码 + PKCE;回调按发现文档取 JWKS 验 id_token(RS256 + iss/aud/exp/nonce),主体取标准 `sub`;验过后仍由本地签发会话令牌,孔明 API 不直接采信 IdP access token。
- 本地账密、自助改密、OAuth 5 家、本地 RS256 签发、refresh 家族管理**全部保留**为 standalone 默认;未启用 IdP provider 时登录清单与行为与变更前一致。
- 发布与互联边界:IdP 为独立项目(虎符 `longzhong/hufu`,单二进制/自带存储);孔明 standalone 发布零依赖 IdP;内部互联只经标准 OIDC(发现文档/JWKS/授权码端点 + `sub`/profile claims),不 import IdP 代码、不共享库/DB、不依赖任何私有端点。
- 影子映射:IdP `sub` → 本地影子 `sys_user`(`idp_subject` 列);联邦路径首登 JIT 建影子档案 + 默认 DEVELOPER 角色 + default 租户(本地授权动作保留)。
- 联邦登出联动:启用 IdP provider 时,本地登出(先吊销本地 refresh 家族)后经虎符 `end_session_endpoint` 结束虎符会话,避免下次登录被 SSO 直接带回;跳转不携带任何令牌;未启用时登出行为不变。
- 当前用户信息(me)在当前凭据轨验签后返回本地角色/权限点/租户(授权留本地)。
- `require_perm` 192 处、rbac/multitenant/datascope 域、billing/portal、agent 零凭证边界:零改动。

## Capabilities

### Modified Capabilities

- `auth`:新增可选 IdP OIDC provider、联邦令牌验签、影子映射;本地认证面保留。

(守 one-domain 规则:rbac / multitenant / developer-portal-billing 不动。)

## Non-goals

- **不下线本地认证面**:不迁移登录入口,不废本地账密/自助改密,不上收 OAuth 5 家。
- 不上收 RBAC/租户/data_scope(留本地,ADR-002 授权边界)。
- 不动 billing 产品轨自有牌价(ADR-007 裁决);合流后对账改从身份权威取数属后续演进。
- 不做 back-channel logout / 全局 SLO 协议族,仅 RP-Initiated Logout;不持久化 IdP id_token(登出不携带 `id_token_hint`)。
- 不动 bot `sys_api_key`(机器身份)、agent 零凭证边界(ADR-002)、SQL 防火墙/内部令牌执行链。
- 不新增能力域;不另立 SSO 服务(IdP 是独立项目,孔明只做 RP)。

## 影响与确认

- 契约源:虎符 `oidc-authority` spec 定义 issuer/JWKS/claims(`sub` 为稳定 UUID);孔明只消费标准契约,与提供方冲突时以提供方为准。
- 对外契约:新增 `GET /auth/logout/sso`(增量;`POST /auth/logout` 保持 204),web 登出改整页导航;虎符客户端登记需允许登出回跳地址(联调前置,其 spec 尚未列该字段)。
- 用户可见变化:启用 IdP provider 时登录清单多一项入口;未启用时与变更前完全一致。零真实用户。
- ADR-006 更新:provider 清单新增 IdP(可选,只消费标准 OIDC 契约);ADR-007 更新:身份权威改归虎符,「锦官统一身份」演进表述改为「可选联邦待实施」;ADR-003 本地令牌体制不变,联邦回调验 IdP id_token,本地会话仍本地签发。
- spec deltas 归档前 sync 进 specs/。
