# HANDOFF — 孔明(kongming)

> 只写「当前状态 / 下一步 / 开放决策项 / 验收记录」四块;变更细节归 git log 与 OpenSpec 归档。

## 当前状态(2026-11-21)

- 单一身份完成:只保留虎符 OIDC;本地账密、改密与 5 家 OAuth provider 删除;`sys_user.id` 即虎符 `sub`。
- 登录链路:授权码 + PKCE、state 浏览器绑定 cookie、state 单次、JWKS 验签;refresh 只走 HttpOnly cookie;SSO 登出已闭环。
- 加固:登录发起每 IP 限流、JWKS/发现文档网络错误归一、refresh 家族轮换/重用吊销、禁用账号拒登。
- 数据与业务:聊天/NL2SQL/RAG/知识库/工具/MCP/任务/团队/计费各域保持;迁移 0065/0066 完成去账号化。
- 规格:`openspec validate --all --strict` 53/53;全部本地门禁(api/web/agent/liannu/dead-code/dup/deps/license/lint-config/lock/compose/security-cov/secret-scan)绿。
- 契约:`contracts/openapi.json` 与前端类型已生成;`contracts-check` 仅受未提交生成物影响。

## 下一步

1. 推送后核对平台 CI(含 contracts-check、eval-nightly、镜像扫描);本会话不轮询。
2. 首次 eval-nightly 真实运行:配置 `KM_AGENT_LLM_API_KEY`/`KM_EVAL_ADMIN_PASSWORD` 并建立基线。
3. 反代部署确认 `KM_AUTH_COOKIE_SECURE`、可信代理头与登录限流口径。
4. 观察多标签页并发登录的 state cookie 覆盖问题,必要时改多值/按 tab 存储。

## 开放决策项

- `0066` DOWNGRADE 只恢复 `admin` 账号,不回滚其余影子账号的角色/租户绑定;回滚到旧二进制需人工评估或补快照迁移。
- `/auth/refresh` 仍接受 body `refresh_token` 且响应回传 refresh 明文;前端已走 cookie,是否收窄为只回 access 属对外契约变更,待拍板。
- `sys_user.id` 依赖虎符 sub 为 UUID 的既有契约;如未来引入非 UUID sub 需迁移。
- 登录限流为进程内、每 worker 独立;严格全局阈值需共享后端。

## 验收记录

- 2026-11-21 去账号化:孔明只保留虎符 provider;`/auth/me` 与三仓同一 sub;测试夹具同步。
- 2026-11-21 `api-check`:1692 passed / 10 skipped / 77.04% 覆盖;ruff/pyright/mypy/pylint/import-linter/deptry 全绿。
- 2026-11-21 `web-check`:typecheck/lint/knip/183 单测/build/e2e/size 全绿;contracts 生成幂等。
- 2026-11-21 OpenSpec strict 53/53;归档 `2026-11-20-unified-identity-oidc`(spec 已由现行规格承接,归档 skip-specs)。
- 未验证:CI 实跑、eval-nightly 真实运行、真实反代部署。
