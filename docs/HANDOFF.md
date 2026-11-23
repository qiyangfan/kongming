# HANDOFF — 孔明(kongming)

> 四块:当前状态 / 下一步 / 开放决策项 / 验收记录;变更细节归 git log 与 OpenSpec 归档。

## 当前状态(2026-11-23)

- `2026-11-23-switch-idp-to-keycloak` 已归档并推送:功能 `24a934a`,归档 `9ffa0af`。
  provider key/env/回调改为 `keycloak`/`KM_KEYCLOAK_*`/`/auth/keycloak/callback`;
  `user_identities` 迁移 0067 将 HUFU 绑定收敛为 KEYCLOAK。
- 登录链路保持:授权码 + PKCE、state 浏览器绑定、state 单次、JWKS 验签;refresh 走
  HttpOnly cookie;本地登出后跳 Keycloak `end_session`,旧 refresh 401。
- 授权仍全部本地:角色、租户、`data_scope`(SQL AST 改写)与本地 RS256 会话令牌不变;
  不消费 Keycloak 的角色/租户 claim。
- 全量 `make check` EXIT=0(api/web/agent/liannu、contracts、security-cov、secret-scan);
  归档后 `openspec validate --all --strict` 53/53。

## 下一步

1. 观察推送后的 CI(contracts-check、eval-nightly、镜像扫描);本会话不轮询。
2. 反代部署确认 `KM_AUTH_COOKIE_SECURE`、可信代理头与登录限流口径。
3. 首次 eval-nightly 真实运行:配置 `KM_AGENT_LLM_API_KEY`/`KM_EVAL_ADMIN_PASSWORD` 并建基线。
4. 观察多标签页并发登录的 state cookie 覆盖问题,必要时改多值/按 tab 存储。

## 开放决策项

- `0066` DOWNGRADE 只恢复 `admin` 账号,不回滚其余影子账号角色/租户绑定;回滚旧二进制需人工评估。
- `/auth/refresh` 仍接受 body refresh_token 且响应回传明文;前端已走 cookie,是否收窄为只回 access 待拍板。
- `sys_user.id` 依赖 Keycloak sub 为 UUID 的既有契约;非 UUID sub 需迁移。
- 登录限流为进程内、每 worker 独立;严格全局阈值需共享后端。

## 验收记录

- 2026-11-23 provider 切换:认证子集 47 passed;旧 `/auth/hufu/*` 404;迁移 0067 单头。
- 2026-11-23 Keycloak 真实联调:demo 登录后 `/auth/me` user_id=Keycloak sub、
  roles/tenant/perms 来自本地;登出 redirect 指向 Keycloak,旧 refresh 401。
- 2026-11-23 全量 `make check` EXIT=0(api/web/agent/liannu、contracts、security-cov);
  归档后 strict 53/53。
- 2026-11-23 推送 `531e86f..9ffa0af`;本文档随单独提交入库。
