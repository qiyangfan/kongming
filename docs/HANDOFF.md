# HANDOFF — 孔明(kongming)

> 四块:当前状态 / 下一步 / 开放决策项 / 验收记录;变更细节归 git log 与 OpenSpec 归档。

## 当前状态(2026-11-23)

- 统一身份:Keycloak 为唯一登录入口(`KM_KEYCLOAK_*`、`/auth/keycloak/callback`);
  授权仍全部本地(角色/租户/`data_scope`/本地 RS256 会话令牌,不消费 Keycloak claim);
  `/auth/login` 已移除,评测登录取现签 access JWT(`KM_EVAL_ACCESS_TOKEN`)。
- 文档检索:向量 + 全文(ts_rank,zhparser 可选)+ 可选实体扩展路 → RRF(k=60)
  → 可选精排 → 父块 top-5。精排与 zhparser 均已验收但**不启用**(证据见评测基线,
  配置与 runbook 就绪)。
- 评测体系:retrieval-eval 15 文档 36 父块 / 28 题;BM25 正式对照 80 题;
  Wiki 问答对照 26 题、七页主题页(覆盖题集全部 9 份文档)。两份评测 change
  已归档,规格增量已应用(`openspec/specs/retrieval-eval`)。
- 多模型注册表:人工条目按「模型×端点」编队,别名优先;任务路由
  TITLER/SUGGEST/DESCRIBE→qwen-flash;主模型与 embedding 走 maas 镜像域,
  dashscope 作回滚端点;compose 已透传注册表/路由/白名单三项。
- 门禁:api-check 全绿(1732 passed,覆盖率 77.09%);`openspec validate --all
  --strict` 53/53。

## 下一步

1. 观察 CI(contracts-check、eval-nightly、镜像扫描);本会话不轮询。
2. 反代部署确认 `KM_AUTH_COOKIE_SECURE`、可信代理头与登录限流口径。
3. eval-nightly 首次真实运行:配置 `KM_AGENT_LLM_API_KEY` 与
   `KM_EVAL_ACCESS_TOKEN`(旧 `KM_EVAL_ADMIN_PASSWORD` 仅兼容旧部署)并建基线。
4. 观察多标签页并发登录的 state cookie 覆盖问题,必要时改多值/按 tab 存储。

## 开放决策项

- 文档精排已验收未启用:链路/降级/延迟达标(p95≈1.3s),但本语料召回
  1.000→0.982(唯一退化=012 登记豁免边界题),无非负收益;启用条件=语料扩容后
  精排出现非负收益。
- zhparser 已验证可建(配方见 `docs/zhparser-runbook.md`),但本语料 A/B 退化
  (1.000→0.929)、zhparser+BM25 无差异,不切换;复评条件=真实语料出现词面缺口。
- `0066` DOWNGRADE 只恢复 `admin` 账号,不回滚其余影子账号角色/租户绑定;回滚旧二进制需人工评估。
- `/auth/refresh` 仍接受 body refresh_token 且响应回传明文;前端已走 cookie,是否收窄为只回 access 待拍板。
- `sys_user.id` 依赖 Keycloak sub 为 UUID 的既有契约;非 UUID sub 需迁移。
- 登录限流为进程内、每 worker 独立;严格全局阈值需共享后端。

## 验收记录

- 2026-11-23 检索评测收口:BM25 80 题 keep(两路逐题无差异,P95 19ms);
  wiki 两轮 keep(跨资料 90% 与 raw 持平、引用 <95%);精排 A/B 1.000→0.982、
  zhparser A/B 1.000→0.929,均不启用。
- 2026-11-23 Keycloak 真实联调:demo 登录后 `/auth/me` user_id=Keycloak sub、
  roles/tenant/perms 来自本地;登出 redirect 指向 Keycloak,旧 refresh 401。
- 2026-11-23 全量 api-check 1732 passed、覆盖率 77.09%;归档后
  `openspec validate --all --strict` 53/53。
