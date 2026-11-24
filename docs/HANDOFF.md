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
- 问答轮真取消:对话页「取消本轮」是真取消——流式响应头 `X-Trace-Id` 是本轮标识,
  `POST /chat/query/stream/{trace_id}/cancel` 返回 `CANCELLED`/`COMPLETED`;Agent 面按
  trace_id 中止(`POST /internal/abort-query`,同一道内部令牌);被取消的轮落
  `chat_message.status=CANCELLED`(迁移 `0068` 加的第五态;`0069` 给 `chat_message.trace_id`
  建了索引)。刷新、切会话、断网仍只是脱离接收,后台照常跑完并落库。
- 取消与收尾的先后:一轮的收尾(落库 + 终态帧)一旦开始就不被请求取消打断——硬取消落在
  收尾段时等它做完,取消结果按收尾定(已有业务行或 FAILED 行即 `COMPLETED`)。流式的
  FAILED 行带 trace_id,以失败收尾的轮再取消返回 `COMPLETED` 并带消息标识,不是 404。
  实现在 `apps/api/kongming/chat/stream_runner.py`(`_await_settle`/`_settle`)。
- 取消的界面走查已做(375/768/1280 × 亮暗两态,2026-11-24):历史里的已取消卡片、点停止
  后的提示、输入框即时释放、问句回填均正常。走查中修掉三个问题:①新对话第一问点停止
  后「本轮已取消」提示不渲染——空态判断漏了停止提示,整个吞掉(`chat-thread.tsx`
  `ThreadBody`);②停止后消息列表不刷新,已取消卡片要等刷新页面才出现——取消收敛后
  改为失效重取消息与会话列表(`use-chat-session.ts`);③开发模式严格模式把流式请求发
  两遍,同一问句跑两轮模型、双倍 token(`streaming-turn.tsx` 存活标志提为 ref 共享)。
- 模型调用可经 muniu 网关(2026-11-24 实测):agent 的 LLM/向量/模型注册表整体指向
  muniu(隔离实例 `:8788`、独立 SQLite、一次性 `mk-` key,不碰用户已有的 `:8787` 实例),
  问答/向量/路由/计量/密钥鉴权全通;取消时正在路上的模型请求被掐断——muniu 记
  `client_cancel`、0 token、15ms 收尾(对照组同类调用 2–3s),取消经网关一直传导到上游。
  起法:复制 muniu.yaml 删 `admin_oidc` 段(配置比二进制旧,缺 admin_subjects)、库指向
  临时文件;`DASHSCOPE_API_KEY` 取孔明根 `.env` 的 `KM_AGENT_LLM_API_KEY`(两域名互认)。
- 刷新端点只认 HttpOnly cookie(2026-11-24 修):切 Keycloak 那次提交把前端刷新改成只发
  cookie,后端却仍要求 JSON body——空 body 判 422,等于刷新页面即登出。已删
  `RefreshRequest`/`LogoutRequest`,刷新/登出/统一登出一律只取 cookie(与锦官同构);
  契约已再生,令牌测试 42 项绿(含「只发 body 不带 cookie 必须 401」反向用例)。
- 真取消的已知限制:①`0068` 降级前须先处理 CANCELLED 行(删除或人工改写),否则四态
  CHECK 拒绝降级;②在途轮登记在进程内存,单节点部署形态下才保证命中(多实例下取消
  可能 404);③通知 Agent 面等 3 秒、等本轮收敛 5 秒,超时才硬取消本地任务——Agent 面
  挂起时取消请求约 8 秒返回(常量在 `apps/api/kongming/chat/inflight.py` 与
  `agent_client.py`);④被中断的那次模型调用不产生 `sys_llm_usage` 行;⑤质量观测面
  不计已取消轮次,按 trace 的用量时间线与按消息标识的节点轨迹仍可查。
- 本机手工起服务时 Agent 面没有自己的 `.env`:它的配置在仓库根 `.env`,须从仓库根
  启动;内部令牌两侧要对上(`KM_INTERNAL_TOKEN` / `KM_AGENT_INTERNAL_TOKEN`),否则
  Agent 面对一切请求答 403。未配置签名密钥时每个 api 进程各自生成临时密钥,
  进程外签发的令牌在另一个进程里无效。
- 门禁各项(2026-11-24):api-check 1781 passed/10 skipped、覆盖率 77.22%,agent-check 626 passed,
  liannu-check 23 passed,web 192 tests + 2 e2e,`openspec validate --all --strict` 57/57。
  `contracts-check` 比较的是工作区与已提交版本,改动未提交时必红——提交前以「重复生成
  `contracts/openapi.json` 无差异」为准。仓库根若残留 `.playwright-mcp/`(浏览器工具的
  页面快照,已被 git 忽略)会让 `lint-config` 的 yamllint 报红,删掉即可。

## 下一步

1. CI 全 job 因 GitHub Actions 账单锁定未启动;解锁后观察 contracts-check 与镜像扫描;
   eval-nightly 仅手动触发。
2. 反代部署确认:`KM_AUTH_COOKIE_SECURE`、可信代理头、登录限流口径;并确认反代把
   `/api` 前缀剥离到后端根路由(认证 cookie 的路径覆盖依赖该口径)。
3. eval-nightly 首次运行前配置 `KM_AGENT_LLM_API_KEY` 与 `KM_EVAL_ACCESS_TOKEN`。
4. 真取消收尾还剩:openspec 归档(`openspec archive add-chat-turn-cancel`)、
   提交(须 owner 逐次明确指令;提交后 contracts-check 转绿)。代码评审已跑一轮
   (2026-11-24,修掉两个已证实缺陷:查重读取失败会逃出取消收敛、`chat_message`
   缺 trace_id 索引);界面走查已做完,五项修复随本次提交入库。

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
- `/auth/refresh` 已只认 HttpOnly cookie(body 通道删除,前端也从不发 body);响应仍回传
  明文 refresh_token,是否收窄为只回 access 待拍板。
- `sys_user.id` 依赖 Keycloak sub 为 UUID;非 UUID sub 需迁移。
- 登录限流为进程内、每 worker 独立;严格全局阈值需共享后端。
- `0066` DOWNGRADE 只恢复 `admin` 账号,不回滚其余影子账号角色/租户绑定。

## 验收记录

- 2026-11-24 问答轮真取消:新增用例先红后绿(api 侧 `test_chat_turn_cancel.py` 26 例、
  质量面已取消轮 1 例,agent 侧 `test_query_abort.py` 6 例,web `turn-stop` 9 例);
  真实模型冒烟——查询中途取消 0.04s 返回 CANCELLED 且历史留已取消行;只断开连接不取消时
  后台 3s 照常落库;Agent 面进程冻结时取消 8.04s 返回 CANCELLED(通知超时 3s + 等收敛 5s
  后硬取消);三轮后历史恰三条 assistant 行。另验证过:被取消 trace 的 `sys_llm_usage`
  不再新增、取消后立即重发同一问正常。
- 2026-11-24 取消界面走查(浏览器实测,375/768/1280 × 亮暗两态):历史已取消卡片文案
  与窄屏换行正常;点停止后提示、输入框释放、问句回填均正常。走查修掉:停止提示被空态
  吞掉(新对话第一问)、停止后已取消卡片不自行出现(改为取消收敛后失效重取)、严格模式
  双发请求(同一问句两轮模型)。
  关键疑点有答案:停止后消息列表原本不自行出现已取消卡片——末条仍是上一轮答句,不满足
  078 回查条件;修复后取消收敛即刷新,卡片自行出现。
- 2026-11-24 取消掐断上游请求(经 muniu 网关,真实模型):对照组跑完四步全记用量;
  「生成 SQL 开始时取消」与「总结开始时取消」两组,被取消的那次调用在孔明用量表与
  muniu 用量记录里都没有行(muniu 记 `client_cancel`、0 token、15ms 收尾,对照组同类
  调用 2–3s)——取消一路传导到上游供应商,无额外 token。muniu 的问答/向量/路由/计量/
  密钥鉴权(错 key 401)同批验过。
- 2026-11-24 真取消代码评审(ai-code-review 一轮,本地未提交改动):修掉两个已证实缺陷——
  ①取消收敛的查重读取(`assistant_message_by_trace`)若抛非 ChatStoreError(连接中断一类),
  异常逃出 `persist_cancelled`:收敛任务告终、流以无终态帧收场、取消端点 500;修为查重单独
  容错(失败即不写行——失败会话上的插入必再失败,绕过查重续写有同轮双行风险),补 2 个回归
  测试(读必失败不抛且不写行;经消费任务端到端终态帧照常下发)。②取消链路按 trace_id 查
  该轮消息无索引:执行计划驱动自会话位图扫描、trace_id 只作过滤,取消延迟随用户历史消息量
  增长;新增迁移 `0069` 建 `idx_chat_message_trace`(开发库升降级往返,执行计划改走索引)。
- 2026-11-24 认证 cookie 路径:state/refresh 覆盖 `/api` 前缀;回归测试旧实现红、
  新实现绿;认证测试 47 passed;浏览器三端 SSO 9/9(三种首登入口,其余两端免密);
  协议级 e2e ALL PASS;`make check` 全绿(数量见「当前状态」)。同日下午修订正端点只认
  cookie(删 body 通道),令牌测试增至 42 例。
- 2026-11-23 多标签页登录 state cookie:并发 tab 各自回调 200,超上限丢最旧,
  非 ASCII state 返回 401;api-check 1751 passed/10 skipped。
- 2026-11-23 Keycloak 真实联调:demo 登录后 `/auth/me` user_id=Keycloak sub,
  roles/tenant/perms 来自本地;登出 redirect 指向 Keycloak,旧 refresh 401。
