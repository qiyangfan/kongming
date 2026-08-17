# ADR-006: 联邦登录采用连接器架构(五路 OAuth 家族)

日期: 2026-08-16
状态: 已接受(002/003 变更的实施蓝图)

## 背景

需求集:GitHub、Google、企业微信、钉钉、飞书五种联邦登录——全部 OAuth 2.0 家族,
无 SAML/LDAP。候选:每家手写 adapter ×5 / 自托管 IdP / 连接器注册表架构。

## 决策

> **通用 OAuth2 内核(authlib)+ 每家一个声明式 provider 插件 + 通用路由
> `/auth/{provider}/start|callback` + 前端按钮数据驱动(`/auth/options`)。**

采纳 NextAuth/Auth0/Logto 共同收敛的 connector 形态:新增 provider = 一个小文件
+ 一条 `KM_AUTH_PROVIDERS` 配置,路由与前端零改动。

## 关键设计

1. **profile 归一化**:每个 provider 产出到
   `{external_id, username, display_name, email?, avatar_url?}` 后统一进入
   `account_link`(查/建号 → 签发 JWT),五家的协议差异止步于各自插件内。
2. **多身份表**:新增 `public.user_identities(user_id, provider, external_id)`,
   一个用户可绑定多路联邦;同 email(verified)已有账号自动关联。
   **schema 变更走 0002 Alembic 迁移**(现有 sys_user.auth_source/external_id
   数据迁入 identity 表;ADR-004 纪律:baseline 不改,演进走新迁移)。
3. **安全清单**:state 一次性校验(Redis + TTL + 用后删);PKCE 按家声明;
   token 永不经过浏览器(前端收 code,后端交换);每 provider 独立 client 凭证。
4. **"扫码"即 OAuth**:企微/钉钉/飞书扫码登录的授权页自带二维码形态,
   无需扫码专用协议栈或前端代码。
5. **品牌图标**:五家 logo 上 simple-icons,前端样式铁律第 4 条例外条款
   由"内联官方 mark"扩展为"多品牌图标用 simple-icons,单个仍可内联"。

## 已否决

| 方案 | 否决理由 |
|---|---|
| 手写 adapter ×5 | 五处重复 code→token→profile;新增家全套复制;前端硬编码按钮 |
| 自托管 IdP(Logto/Zitadel/Keycloak) | 五个 OAuth 联邦恰是 IdP 最不需要出场的场景;溢价(SAML/企业 IdP 兼容)用不上,代价(重组件+登录页不可控)全付——ADR-003 账本重演 |

## 实施节奏

- 002 认证闭环:连接器框架 + GitHub/Google 首发 + 账密核心 + 0002 迁移 + auth spec 补写
- 003 国内三家:企微(通讯录权限)/钉钉(app 签名 quirks)/飞书(预取 app_access_token)

## 与既有决策的关系

ADR-003(自建认证)不变,本 ADR 是其联邦部分的架构化扩展;
回退路径维持:SAML 类企业联邦需求成规模时,IdP 作为 auth 域下游引入,签发权可平移。
