# HANDOFF — 孔明(kongming)

> 四块:当前状态 / 下一步 / 开放决策项 / 验收记录;变更细节归 git log 与 OpenSpec 归档。

## 当前状态(2026-11-24)

- 统一身份:Keycloak 为唯一登录入口(`KM_KEYCLOAK_*`、`/auth/keycloak/callback`);
  授权仍全部本地(角色/租户/`data_scope`/本地 RS256 会话令牌,不消费 Keycloak claim);
  `/auth/login` 已移除,评测登录取现签 access JWT(`KM_EVAL_ACCESS_TOKEN`)。
  刷新/登出端点只认 HttpOnly cookie——body 通道已删(与锦官同构),前端也从不发 body。
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
- 问答轮真取消(add-chat-turn-cancel,已归档):对话页「取消本轮」是真取消——流式响应头
  `X-Trace-Id` 是本轮标识,`POST /chat/query/stream/{trace_id}/cancel` 返回
  `CANCELLED`/`COMPLETED`;Agent 面按 trace_id 中止(`POST /internal/abort-query`,
  同一道内部令牌);被取消的轮落 `chat_message.status=CANCELLED`(迁移 `0068` 第五态;
  `0069` 给 `chat_message.trace_id` 建了索引)。刷新、切会话、断网仍只是脱离接收,
  后台照常跑完并落库。
- 前端断开后的中继内存卫生:SSE 中继生成器异常退出(前端断开、取消、发送失败)时
  就地丢弃消费任务后续入队的帧直到终止标记——后台照常落库,但不在内存里为无人接收的
  帧堆队(结果集大时单轮可达数百 KB,并发遗弃轮次会叠加)。Agent 面帧队列保持无界:
  其唯一消费者(控制面消费任务)断开时图执行即被取消,队列不会无限增长;设有界反而
  在图执行快于读取时把驱动任务堵死。
- 在途轮登记跨实例共享(share-turn-registry,已归档):取消请求落到任一实例都生效。
  持有实例在 Redis 记一键(`chat:turn:{trace_id}`,值含归属用户/本实例地址/注销凭据,
  TTL `KM_TURN_REGISTRY_TTL_SEC` 缺省 900s);收到取消的实例未命中本地在途登记时,
  凭该键把取消经内部面 `POST /internal/chat/turn-cancel` 转给持有实例执行,结果原样
  回传。登记不可用、持有实例不可达、登记指向本实例而本实例无该轮(陈旧登记)时,
  一律按落库结果回答,不伪造终态。自报地址 `KM_API_SELF_BASE_URL` 缺省按主机名推导
  ——多实例部署必须逐实例唯一可达,不能填共享服务名(会让自环防护误判);登记 TTL 须
  大于 `KM_AGENT_TIMEOUT_SEC`(120s)× 最坏 5 阶段 + 收敛余量。Redis 故障时退化为
  落库兜底,单实例语义不变。
- 取消与收尾的先后:一轮的收尾(落库 + 终态帧)一旦开始就不被请求取消打断——硬取消落在
  收尾段时等它做完,取消结果按收尾定(已有业务行或 FAILED 行即 `COMPLETED`)。流式的
  FAILED 行带 trace_id,以失败收尾的轮再取消返回 `COMPLETED` 并带消息标识,不是 404。
  实现在 `apps/api/kongming/chat/stream_runner.py`(`_await_settle`/`_settle`)。
- 真取消的已知限制:①`0068` 降级前须先处理 CANCELLED 行(删除或人工改写),否则四态
  CHECK 拒绝降级;②通知 Agent 面等 3 秒、等本轮收敛 5 秒,超时才硬取消本地任务——
  Agent 面挂起时取消请求约 8 秒返回(常量在 `apps/api/kongming/chat/inflight.py` 与
  `agent_client.py`);③被中断的那次模型调用不产生 `sys_llm_usage` 行;④质量观测面
  不计已取消轮次,按 trace 的用量时间线与按消息标识的节点轨迹仍可查;⑤跨实例取消的
  残余窗口:持有实例硬崩后最长一个登记 TTL 内,取消会先转发、连不通后按落库兜底
  (最坏多等一个转发超时 16s);Redis 故障时跨实例取消退化为落库兜底(单实例不受影响,
  本地快路径不依赖 Redis)。
- 模型调用可经 muniu 网关(2026-11-24 实测):agent 的 LLM/向量/模型注册表整体指向
  muniu(隔离实例、独立 SQLite、一次性 `mk-` key,不碰用户常驻实例)时问答/向量/路由/
  计量/密钥鉴权全通;取消时正在路上的模型请求被掐断——网关记 `client_cancel`、0 token,
  无额外 token。起法:复制 muniu.yaml 删 `admin_oidc` 段(配置比二进制旧,缺
  admin_subjects)、库指向临时文件;`DASHSCOPE_API_KEY` 取孔明根 `.env` 的
  `KM_AGENT_LLM_API_KEY`(两域名同一把密钥);客户端用 `Authorization: Bearer mk-…`。
- 本机手工起服务时 Agent 面没有自己的 `.env`:它的配置在仓库根 `.env`,须从仓库根
  启动;内部令牌两侧要对上(`KM_INTERNAL_TOKEN` / `KM_AGENT_INTERNAL_TOKEN`),否则
  Agent 面对一切请求答 403。未配置签名密钥时每个 api 进程各自生成临时密钥,
  进程外签发的令牌在另一个进程里无效。
- 门禁各项(2026-11-24):api-check 1797 passed/10 skipped、覆盖率 77.22%,agent-check
  626 passed,liannu-check 23 passed,web 192 tests + 2 e2e,
  `openspec validate --all --strict` 56/56,`make check` 全绿。
  `contracts-check` 比较的是工作区与已提交版本,改动未提交时必红——提交前以「重复生成
  `contracts/openapi.json` 无差异」为准。仓库根若残留 `.playwright-mcp/`(浏览器工具的
  页面快照,已被 git 忽略)会让 `lint-config` 的 yamllint 报红,删掉即可。

## 下一步

1. CI 全 job 因 GitHub Actions 账单锁定未启动;解锁后观察 contracts-check 与镜像扫描;
   eval-nightly 仅手动触发。
2. 反代部署确认:`KM_AUTH_COOKIE_SECURE`、可信代理头、登录限流口径;并确认反代把
   `/api` 前缀剥离到后端根路由(认证 cookie 的路径覆盖依赖该口径)。多实例部署时按
   `KM_API_SELF_BASE_URL` 给每个实例配唯一可达地址,并确认实例间网络互通(内部面转发
   依赖它)。
3. eval-nightly 首次运行前配置 `KM_AGENT_LLM_API_KEY` 与 `KM_EVAL_ACCESS_TOKEN`。
4. 实时语音通话(`add-voice-call`,提案已备、未开工):前置(真取消)已就绪,真实通话
   验证已做完(2026-11-24,约 0.02 元;要点=浏览器可 WebRTC 直连阿里实时模型、
   必须用 semantic_vad、命中工具调用的那轮模型不出声),待 owner 裁决是否开工。
5. 两个待评审的外来提案(他人所写,未评审未开工):`refactor-current-user-auth`
   (路由统一收 `CurrentUser`)、`standardize-chat-stream-events`(流式事件统一信封,
   含 BREAKING 变更)。

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
- `/auth/refresh` 响应仍回传明文 refresh_token,是否收窄为只回 access 待拍板。
- `sys_user.id` 依赖 Keycloak sub 为 UUID;非 UUID sub 需迁移。
- 登录限流为进程内、每 worker 独立;严格全局阈值需共享后端。
- `0066` DOWNGRADE 只恢复 `admin` 账号,不回滚其余影子账号角色/租户绑定。

## 验收记录

- 2026-11-24 问答轮真取消:新增用例先红后绿(api `test_chat_turn_cancel.py` 26 例、
  质量面已取消轮 1 例,agent `test_query_abort.py` 6 例,web `turn-stop` 9 例);
  真实模型冒烟——查询中途取消 0.04s 返回 CANCELLED 且历史留已取消行;只断开连接不取消时
  后台 3s 照常落库;Agent 面进程冻结时取消 8.04s 返回 CANCELLED(通知超时 3s + 等收敛 5s
  后硬取消);三轮后历史恰三条 assistant 行;被取消 trace 的 `sys_llm_usage` 不再新增、
  取消后立即重发同一问正常。界面走查(375/768/1280 × 亮暗)修掉三个前端问题(停止提示被
  空态吞掉、已取消卡片不自行出现、严格模式双发请求);代码评审修掉两个缺陷(查重读取失败
  逃出取消收敛;`chat_message` 缺 trace_id 索引,补迁移 `0069`)。
- 2026-11-24 取消掐断上游请求(经 muniu 网关,真实模型):对照组跑完四步全记用量;
  「生成 SQL 开始时取消」与「总结开始时取消」两组,被取消的那次调用在孔明用量表与
  muniu 用量记录里都没有行(muniu 记 `client_cancel`、0 token、15ms 收尾,对照组同类
  调用 2–3s)——取消一路传导到上游供应商,无额外 token。
- 2026-11-24 在途轮登记跨实例共享(双实例手工冒烟):A(8020)起真实模型流、B(8022)
  收到取消→经 Redis 登记定位并转发到 A 的内部面→A 跑既有编排并通知 Agent 面中止
  (agent 日志「在途问答已中止 count=1」)→B 返回 CANCELLED 携消息标识,A 的流收到
  CANCELLED 终态帧,历史恰一条已取消行;杀掉 A 后 B 取消其陈旧 trace→0.02s 返回
  404(不自我转发成环、不悬挂);登记键随轮次释放。新增用例 16 个(单测 13 + 集成 3),
  api-check 1797 passed/10 skipped。
- 2026-11-24 认证 cookie 路径:state/refresh 覆盖 `/api` 前缀;回归测试旧实现红、
  新实现绿;认证测试 47 passed;浏览器三端 SSO 9/9(三种首登入口,其余两端免密);
  协议级 e2e ALL PASS;`make check` 全绿。同日下午修订正端点只认 cookie(删 body
  通道),令牌测试增至 42 例。
- 2026-11-23 多标签页登录 state cookie:并发 tab 各自回调 200,超上限丢最旧,
  非 ASCII state 返回 401;api-check 1751 passed/10 skipped。
- 2026-11-23 Keycloak 真实联调:demo 登录后 `/auth/me` user_id=Keycloak sub,
  roles/tenant/perms 来自本地;登出 redirect 指向 Keycloak,旧 refresh 401。
