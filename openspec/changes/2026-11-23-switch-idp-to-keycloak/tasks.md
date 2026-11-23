# 任务:统一身份 provider 切换为 Keycloak

> 安全/认证类改动测试先行;每节以对应 `make check` 子集绿收口。

## 1. 失败测试

- [x] 1.1 `/auth/options` 仅返回 `keycloak`;`/auth/hufu/start|callback` 404
- [x] 1.2 回调建档 `user_identities.provider=KEYCLOAK`,`sys_user.id=id_token.sub`
- [x] 1.3 配置契约测试:`KM_KEYCLOAK_*` 缺失时 provider 不注册;旧 `KM_HUFU_*` 不再生效
- [x] 1.4 既有安全断言回归:state 单次、PKCE S256、nonce/iss/aud、禁止 email 合并、
      登出先吊销本地 refresh

## 2. 实现

- [x] 2.1 provider 类/注册键改名(`keycloak`),settings 字段改为 `keycloak_*`
- [x] 2.2 `ProviderKey`/`user_identities` 枚举改为 `KEYCLOAK`;清理 dev 库 `HUFU` 行
- [x] 2.3 默认 redirect 改 `/auth/keycloak/callback`;登出回跳同步
- [x] 2.4 更新受影响的 service/seed/测试 fixture;删除 hufu 命名残留

## 3. Web 与联调

- [x] 3.1 web 登录页 e2e mock 与路由改为 `keycloak`;UI 文案保持「统一身份」
- [x] 3.2 platform:确认 client `kongming-web` 的 redirect/post-logout 与 secret;
      不向 token 写入产品角色
- [x] 3.3 Keycloak 26.8.2 真实联调:登录 → `/auth/me` 的 user_id 等于 Keycloak sub;
      租户/角色/`data_scope` 仍来自本地;本地禁用后 refresh/me 拒绝
      实测:demo 登录 user_id=Keycloak sub,roles=[DEVELOPER],tenants=[default],perms=15;
      data_scope/禁用沿用既有域测试(本次未改业务授权)
- [x] 3.4 登出:本地 refresh 家族吊销,跳转 `end_session`;Keycloak 不可达时仍回落登录页
      实测:redirect_url 指向 Keycloak logout,旧 refresh 立即 401;断连分支由单测覆盖

## 4. 文档与门禁

- [x] 4.1 `openspec/config.yaml` 澄清「不引入 Keycloak 组件」为「不内嵌/不自托管 IdP」
- [x] 4.2 ADR-006 追加节、`docs/tech-stack.md`、README、`.env.example` 同步
- [x] 4.3 `openspec validate --all --strict` 通过;`make check` 全绿
      实测(2026-11-23,生成物入索引后):`make check` EXIT=0;strict 54/54,
      api-check、web-check、contracts-check、security-cov 全绿。
