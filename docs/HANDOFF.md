# HANDOFF — 孔明(kongming)

> 四块:当前状态 / 下一步 / 开放决策项 / 验收记录;变更细节归 git log 与 OpenSpec 归档。

## 当前状态(2026-11-27)

- 运行时控制面 10 个 change 已归档,语音轮次收口 change
  `2026-11-27-fix-voice-turn-resilience` 已归档;主规格 66/66 strict。OpenSpec
  仍有 3 个并发写者新建的 active 提案(`add-chat-turn-resume`/
  `add-eval-latency-cost`/`add-session-context-summary`),均 0 任务,未实施、
  未归档。
- X-01 managed 消费已落地:快照 RSA `X-Jinguan-Signature` + `X-Jinguan-Key-Id` 按 kid
  拉取并缓存锦官 JWKS(静态 PEM 仅离线 pin)、注册/心跳/健康、claim/ack 命令、
  `/ingest/events` 产品事件、`/service/v1/read-projection` 只读投影;Keycloak
  client credentials 请求 `control:*` 四 scope。
- 身份缓存按快照版本单调应用;租户作用域锚定 `tenant_key`;成员 `subject` 收紧为
  Keycloak UUID,成员停用只撤租户关系;目录 `enabled` 即 `LISTED`;安全类快照缺失/
  验签失败/版本回退时受保护请求 503,控制面缺席按 last-known 继续。
- 管理台独立为 `apps/admin`(5174、`kongming-admin-web`、`/admin-auth/*`、独立
  refresh surface);已提供侧栏/窄屏顶栏亮暗切换、首帧防闪烁(`km-theme`),
  `infra/admin/nginx.conf` 用内联脚本 sha256 白名单而不是放宽 `script-src`。
- Alembic 0074/0075/0076:快照签名列前向修正 TEXT、租户名 128;控制数据按天清理
  (已投递用量 90d/终态回执 180d/失联注册 7d)。
- 语音通话收口:模型问答工具参数缺失/畸形时回退同一句听写原话;无听写仍拒绝,
  不伪造问题;每次语音输入都要求走 `ask_kongming`,未放行的模型自答继续取消。
  单值/单行结果不再追加「详细数据已显示在屏幕上」,只有多行结果表或图表提示
  页面有数据。语义断句阈值 0.4、静默下限 2000ms;建立中挂断立即释放拨号锁,
  旧建立请求迟到完成不会挡住下一次拨号。
- 结果表容器显式 `overscroll-y-auto`,覆盖全局 `overscroll-behavior: contain`;
  表格自身横向滚动保留,鼠标在表格上时页面可继续纵向滚动。

## 下一步

1. 部署侧注入 `KM_CONTROL_*` 与 `KM_ADMIN_KEYCLOAK_CLIENT_SECRET`,在目标 realm 复核
   管理台会话与角色授权;生产只允许 Keycloak 服务 token。
2. 真实反代剩余项:`/admin/*` 308、WebSocket Upgrade 与 `Sec-WebSocket-Protocol` 透传;
   确认 `KM_AUTH_COOKIE_SECURE`、可信代理头、登录限流口径、TLS/DNS。本机已演练
   `/api` 剥前缀与 CSP。
3. GitHub Actions billing 解除后核对 codeql/ci/security 与分支保护;三个 active
   OpenSpec 提案由各自 owner 推进。工作流/Bot 产品事件按 X-01 `metric` 规则继续扩展;
   eval-nightly 维持原触发条件。
4. 真实语音复测必须覆盖:播报结束后紧接着问新问题、连接中挂断后再次拨号、
   短句「等一下」打断、单值数量播报、结果表上滚轮滚动。若仍出现「没受理」,
   先查文字事件日志是否有听写事件与 `ask_kongming` 调用:没有听写事件说明
   当前供应商未下发转写,需另立变更接入转写;有听写但未调用工具则继续收紧
   模型行为说明或补确定性路由。
5. 断句阈值 0.4 尚缺真实环境 A/B:若环境噪声造成误打断,回退 0.5;调整只改
   `END_OF_TURN_THRESHOLD`,不涉及协议或配置项。

## 开放决策项

- X-01 已冻结:快照/命令/用量/注册/只读投影 wire 以锦官为准,本仓不单边改。
  `tenant_key` 是跨平面作用域锚点;`features` 仅展示;缺失配额维度按未配置(0=不限)。
- 安全类快照按 ADR-0005 使用 last-known,不做时间硬过期;若要求硬过期需单独 ADR
  并明确停锦官即停服务的可用性影响。
- 管理台角色撤销窗口由 30m 绝对会话封顶;即时吊销需 Keycloak 会话绑定/introspection。
- 管理台未配置锦官外链地址:计费/共享控制入口直接移除,是否需要外链待部署口径。
- refresh cookie 作用域为 API 同源全路径;同源再挂其他应用前需复评。
- 图片价目已入快照契约,锦官未下发前 managed 只能静态 YAML;只读列表是否隐藏静态行待收敛。
- 同一快照内“租户改名并复用旧名且旧租户有引用”按 `tenant_identity_conflict` 拒绝,分批下发。

## 验收记录

- 2026-11-27 跨仓联调:锦官快照 v3/v5、注册/心跳/健康、命令 claim/ack、产品事件、
  只读投影全部通过;控制面管理台显示 managed/version 5/receipts。
- 2026-11-27 JWKS 轮换:active kid 换新并保留退役公钥;v5 由新 kid 签名,旧快照仍可
  用退役公钥验签;本仓遇未知 kid 强制刷新 JWKS 后应用 v5。停 Keycloak 40s 期间
  last-known 继续、健康 200,恢复后注册/心跳回正。
- 2026-11-27 管理台:真实 Keycloak 登录,`/users` 与 `/control` 整页渲染;375/768/1280
  页面级无横向溢出(768 表格容器内横滚 39px);亮暗切换、持久化、首帧防闪烁通过。
- 2026-11-27 反代 CSP:nginx 挂载生产 `dist` 与 `infra/admin/nginx.conf`(仅上游换成本机
  网关),`/api/healthz` 200;真实登录与主题切换无 CSP 违规;去掉 sha256 的负向对照
  被浏览器拦截(首帧暗色未生效)。
- 2026-11-27 全量门禁:`make check` exit 0;API 1928 passed/10 skipped/覆盖率 78.02%,
  Web 256 tests + 3 e2e,typecheck/lint/format/build/size 通过;管理台 admin-check
  typecheck/lint/lint:css/format/vitest 7/build/e2e 4 通过;contracts-check 通过。
- 2026-11-27 语音自动化状态机定向 28 passed,网页通话交互 9 passed;真机语音复测
  见「下一步」,未完成前不记为已验收。
- 2026-11-27 结果表滚动:浏览器实测悬停表格滚动滚轮,主滚动区正常滚动 500px。
- 2026-11-27 OpenSpec:`openspec validate --all --strict` 66/66;语音规格增量已 sync。
