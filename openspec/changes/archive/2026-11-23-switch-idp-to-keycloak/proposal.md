# 提案:统一身份 provider 切换为 Keycloak

## Why

- 平台身份权威已收敛到 Keycloak(realm `longzhong`);虎符项目已删除,但孔明的
  OIDC provider 仍以 `hufu` 命名,spec/ADR/配置上下文也引用虎符,运行与文档都在指向
  一个不存在的系统。
- 孔明已有完整的标准 OIDC provider(`apps/api/kongming/auth/providers/hufu.py`):
  发现文档/JWKS 缓存、授权码 + PKCE(S256)、nonce/iss/aud/exp 验签、本地双 token、
  refresh 家族、登出联动与测试;Keycloak 是标准 OIDC OP,现有实现可直接复用。
- platform 已建 client `kongming-web`,redirect 登记为
  `http://localhost:5173/auth/keycloak/callback`;与孔明现行 `/auth/hufu/callback`
  不一致,按现状无法完成登录。

## What Changes

- provider key `hufu` → `keycloak`;环境变量 `KM_HUFU_*` → `KM_KEYCLOAK_*`
  (enabled/issuer/client_id/client_secret/redirect_uri/post_logout_redirect_uri);
  默认 redirect 对齐 platform 登记值 `/auth/keycloak/callback`。
- `ProviderKey` 与 `user_identities.provider` 写入 `KEYCLOAK`;不再接受 `HUFU`。
- spec/ADR-006/README/配置上下文同步:明确 **Keycloak 是平台外部 IdP,孔明只做 RP**,
  不内嵌、不自托管,不违反「不引入 Keycloak 组件」的技术栈约束。
- 登录页仍只显示「统一身份」,由 `/auth/options` 数据驱动;现有路由与交互不变。
- 保持所有既有安全不变量:`sub` 为唯一主键、不按 email/username 合并、本地 RS256
  双 token、refresh 轮换/重放吊销、租户/角色/`data_scope` 全部留本地、登出先吊销
  本地 refresh 再跳 Keycloak `end_session`、未配置时登录入口不出现。

## Non-goals

- 不从 token 接收角色/租户/权限;不上收 RBAC/多租户/`data_scope`。
- 不重写 provider:现有实现已覆盖标准 OIDC 与密钥轮换刷新,重写无收益。
- 不保留 `hufu` 兼容别名或双 provider:greenfield、零真实用户,兼容层只会延长旧命名。
- 不引入 Keycloak 组件到孔明运行时;不做多 realm/MFA/SCIM。

## Impact

- 对外契约:登录清单 provider key 由 `hufu` 变为 `keycloak`,前端路由改为
  `/auth/keycloak/callback`;无真实用户,无兼容负担。
- 数据:dev 库若存在 `user_identities.provider='HUFU'` 行随改名清理(零真实用户,
  不写迁移脚本);`sys_user` 与业务数据不动。
- 配置:部署需把 `KM_HUFU_*` 替换为 `KM_KEYCLOAK_*`;client secret 走环境变量。
- 安全边界不变;回滚 = 恢复旧配置/旧镜像,无 schema 变更。
