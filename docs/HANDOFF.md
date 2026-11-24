# HANDOFF — 孔明(kongming)

> 四块:当前状态 / 下一步 / 开放决策项 / 验收记录;变更细节归 git log 与 OpenSpec 归档。

## 当前状态(2026-11-24)

- 统一身份:Keycloak 为唯一登录入口(`KM_KEYCLOAK_*`、`/auth/keycloak/callback`);
  授权仍全部本地(角色/租户/`data_scope`/本地 RS256 会话令牌,不消费 Keycloak claim);
  `/auth/login` 已移除,评测登录取现签 access JWT(`KM_EVAL_ACCESS_TOKEN`)。
- 三端互通 SSO 已验收(muniu/kongming/jinguan):同一浏览器任一端登录,其余两端经
  OIDC 免密进入,三端落到同一 Keycloak sub;协议级与浏览器 UI(Playwright)双口径通过。
  kongming 的 state/refresh cookie 作用路径覆盖 SPA 的 `/api` 前缀,修复浏览器回调
  「state 未绑定」与启动刷新 401,回归测试守护。
- 本地联调口径:Keycloak `127.0.0.1:8080` realm `longzhong`;三端 issuer 必须统一写
  `127.0.0.1`(`localhost` 与 `127.0.0.1` cookie 按主机隔离,会破坏 SSO)。
  kongming 配置在 `apps/api/.env`(pydantic 按工作目录加载,根 `.env` 仅 compose 用);
  本机 DB/Redis 用统一环境栈 `~/workspace/env`(5432/6379)。
- 多标签页登录:`km_oidc_state` 为点分隔 state 列表(上限 8,旧单值兼容),并发 tab
  互不覆盖;HttpOnly/SameSite/CSRF 绑定语义不变。
- 文档检索:向量 + 全文(ts_rank,zhparser 可选)+ 可选实体扩展路 → RRF(k=60)
  → 可选精排 → 父块 top-5;精排与 zhparser 已验收未启用(条件见开放决策项)。
  被索引文本=文档名+块正文;全文兜底零分命中不进入融合;文档多查询分解已落地
  (子查询 2-3、注入上限 8、失败回落原问)。
- 评测:15 文档 28 题、BM25 80 题、Wiki 26 题、lab 74 题与判别变体、K8s 30 题
  (73 篇/1400 父块,AI 编写+程序校验+AI 复核,未经人工领域评审);K8s 基线融合
  0.833/全跳 0.733,分解后 0.883/0.800。
- 多模型注册表:人工条目按「模型×端点」编队,别名优先;TITLER/SUGGEST/DESCRIBE→
  qwen-flash;主模型与 embedding 走 maas 镜像域,dashscope 回滚。
- 门禁:`make check` 全绿——api-check 1752 passed/10 skipped、覆盖率 77.15%,
  agent-check 620 passed,liannu-check 23 passed,web 183 tests + 2 e2e;
  `openspec validate --all --strict` 53/53。

## 下一步

1. CI 全 job 因 GitHub Actions 账单锁定未启动;解锁后观察 contracts-check 与镜像扫描;
   eval-nightly 仅手动触发。
2. 反代部署确认:`KM_AUTH_COOKIE_SECURE`、可信代理头、登录限流口径;并确认反代把
   `/api` 前缀剥离到后端根路由(认证 cookie 的路径覆盖依赖该口径)。
3. eval-nightly 首次运行前配置 `KM_AGENT_LLM_API_KEY` 与 `KM_EVAL_ACCESS_TOKEN`。

## 开放决策项

- 文档精排未启用:新索引 0.964→0.893(-7.1pp,两模型一致),旧索引 1.000→0.982;
  启用条件=语料扩容后出现非负收益。
- zhparser 未切换:本语料 A/B 退化(新索引 0.964→0.946,旧索引 1.000→0.929);
  词面缺口语料的价值已在 lab 显现;复评条件=真实语料出现词面缺口。
- 块索引上下文已生效:判别语料 0.291→1.000;15 文档 0.964(-3.6pp,009/012 各丢
  「租赁规则」一跳,012 为登记豁免边界题);收紧红线前先逐题评估 009/012。
- K8s 跨文档多跳全跳 1/6;剩余缺口受查询改写与候选精度限制,不设通过线。
- refresh cookie 作用域为 API 同源全路径:同源信任边界内接受;若同源再挂其他应用,
  复评限定方案(仅 `/api` 或双 cookie)。
- `/auth/refresh` 仍接受 body refresh_token 且响应回传明文;是否收窄为只回 access 待拍板。
- `sys_user.id` 依赖 Keycloak sub 为 UUID;非 UUID sub 需迁移。
- 登录限流为进程内、每 worker 独立;严格全局阈值需共享后端。
- `0066` DOWNGRADE 只恢复 `admin` 账号,不回滚其余影子账号角色/租户绑定。

## 验收记录

- 2026-11-24 认证 cookie 路径:state/refresh 覆盖 `/api` 前缀;回归测试旧实现红、
  新实现绿;认证测试 47 passed;浏览器三端 SSO 9/9(三种首登入口,其余两端免密);
  协议级 e2e ALL PASS;`make check` 全绿(数量见「当前状态」)。
- 2026-11-23 多标签页登录 state cookie:并发 tab 各自回调 200,超上限丢最旧,
  非 ASCII state 返回 401;api-check 1751 passed/10 skipped。
- 2026-11-23 Keycloak 真实联调:demo 登录后 `/auth/me` user_id=Keycloak sub,
  roles/tenant/perms 来自本地;登出 redirect 指向 Keycloak,旧 refresh 401。
