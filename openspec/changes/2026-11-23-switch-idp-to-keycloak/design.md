# 设计:provider 改名与 Keycloak 配置切换

## 结论

复用现有标准 OIDC provider,**只改 provider key、配置字段与回调路径**,不重写、
不保留别名。Keycloak 作为外部 OP 接入;孔明仍本地签发会话令牌、本地保存授权。

## 备选比较

| 方案 | 结论 | 理由 |
| --- | --- | --- |
| 保持 `hufu` 名称,只把 issuer 指到 Keycloak | 否决 | 命名指向已删除系统;与 platform 登记的 `/auth/keycloak/callback` 不一致;运维误配风险高 |
| 重写 provider | 否决 | 现有实现已含 JWKS 缓存/轮换刷新、PKCE、nonce、state、登出联动与测试,重写无收益 |
| 改名 `keycloak` + 配置切换 | 采纳 | 改动局限在命名/配置;零真实用户,无迁移成本;与平台 IdP 命名一致 |

## 契约与边界

- 只消费标准 OIDC:发现文档、JWKS、授权码 + PKCE(S256)、token、`end_session`;
  claims 仅 `sub`、`preferred_username`、`name`、`email`、`email_verified`。
- `aud` = 本 RP `client_id`;`iss`/`nonce`/`exp` 强校验;id_token 不持久化。
- 主体:`sys_user.id = Keycloak sub`;`user_identities.provider='KEYCLOAK'`。
- 授权:角色/租户/`data_scope` 一律本地;token 中的同类 claim 忽略。
- 部署:confidential client,secret 仅环境变量;redirect 与
  `post_logout_redirect_uri` 必须在 client 上登记。

## 失败模式

| 场景 | 行为 |
| --- | --- |
| 未配置 Keycloak | provider 不注册、登录入口不出现;`/auth/keycloak/*` 404 |
| Keycloak 不可达 | 新登录失败并提示;已签发的本地 token 按 TTL 继续(本地验签) |
| 回调校验失败 | 拒绝建会话,不回显 token/code/secret |
| 登出时 Keycloak 不可达 | 本地 refresh 家族仍吊销,直接回落登录页 |
| 密钥轮换 | JWKS 命中新 `kid` 失败时强制刷新一次,避免登录失败到 TTL |

## 变更不做

- 不保留 `hufu` provider 兼容别名;dev 库 `HUFU` identity 行直接清理。
- 不引入 Keycloak 组件、不自托管 IdP;`openspec/config.yaml` 的「不引入 Keycloak」
  约束更新为「不内嵌/不自托管 IdP;平台 Keycloak 作为可选外部 OIDC provider」。
