# ADR-003: 放弃 Keycloak,自建认证——登录页即前端路由

日期: 2026-08-16
状态: 已接受

## 背景

前系统用 Keycloak 26 统一认证,为让登录页与产品风格一致,维护了一套
自定义主题:459 行手写 CSS **覆盖 PatternFly 5**(`--pf-v5-*` 变量 + 类选择器),
外加 FreeMarker 双语 messages。问题:

- 登录页样式是对"另一个 UI 框架"的镜像复刻,产品前端每次改版都要双份维护;
- Keycloak 大版本升级伴随 PatternFlow/主题结构变动,定制随时碎裂;
- Keycloak 是全栈中最重的运维件(JVM、realm 导出格式随版本漂移),
  而我们实际只用了它的:账密登录 + GitHub 联邦 + JWT 签发。

## 决策

> **自建认证,登录页是 `apps/web` 的普通路由(`/login`)**,
> 与主应用共用同一套 shadcn/ui + Tailwind 设计 token + i18n,零镜像。

技术要点:

| 项 | 方案 |
|---|---|
| 密码哈希 | argon2id(argon2-cffi) |
| 令牌 | PyJWT,RS256 签发;access 15min + refresh 7d(轮换,复用检测即吊销全家) |
| 公钥 | api 暴露 `/auth/jwks`(为未来多验证方预留) |
| GitHub 联邦 | 标准 OAuth2 Authorization Code 流,authlib,`syncMode=IMPORT` 语义自管(首次登录建号) |
| 会话管理 | 无状态 JWT + Redis 存 refresh token 家族指纹(吊销用) |

## 明确接受的代价

- 失去现成 OIDC IdP(给其他应用做 SSO)、SAML/LDAP 企业联邦、现成用户管理界面。
- 本产品是单产品平台,上述能力当前用不上;用户管理后台本就在产品路线图内。

## 回退路径(不推翻本 ADR 的前提下)

若未来需要企业 SSO / 多应用 SSO:引入标准 IdP(Logto 或 Keycloak)作为
**可选**身份源,自建 auth 域降级为该 IdP 的下游/网关层;JWT 签发权可平移。
auth 模块因此必须保持独立包边界(`kongming/auth/`),不与业务域交织。
