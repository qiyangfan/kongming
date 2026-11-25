# HANDOFF — 孔明(kongming)

> 四块:当前状态 / 下一步 / 开放决策项 / 验收记录;变更细节归 git log 与 OpenSpec 归档。

## 当前状态(2026-11-25)

- 统一身份:Keycloak 为唯一登录入口(`KM_KEYCLOAK_*`、`/auth/keycloak/callback`);
  授权仍全部本地(角色/租户/`data_scope`/本地 RS256 会话令牌,不消费 Keycloak claim);
  `/auth/login` 已移除,评测登录取现签 access JWT(`KM_EVAL_ACCESS_TOKEN`)。
  刷新/登出端点只认 HttpOnly cookie;访问令牌只存内存。三端 SSO 已验收
  (muniu/kongming/jinguan):同一浏览器任一端登录,其余两端经 OIDC 免密进入,
  三端落到同一 Keycloak sub。kongming 的 state/refresh cookie 作用路径覆盖 SPA 的
  `/api` 前缀。多标签页 `km_oidc_state` 为点分隔 state 列表(上限 8),并发 tab 互不覆盖。
- 认证依赖已收成具名身份:`CurrentUser` 只携带 `user_id`;`require_perm` 走
  `AuthService.get_rbac_user()` 只读账号有效性与启用角色权限,不再为权限检查读取
  角色名与租户列表。权限绑定变更下一次请求立即生效。
- 本地联调口径:Keycloak `127.0.0.1:8080` realm `longzhong`;三端 issuer 必须统一写
  `127.0.0.1`(`localhost` 与 `127.0.0.1` cookie 按主机隔离,会破坏 SSO)。
  api 配置在 `apps/api/.env`(pydantic 按工作目录加载);agent 没有自己的 `.env`,
  配置在仓库根 `.env`,须从仓库根启动。本机 DB/Redis 用统一环境栈
  `~/workspace/env`(5432/6379),不要用 `make up` 抢端口。
- 真实测试环境配方(2026-11-25 验通):api 8000(另带语音三项配置与
  `KM_AGENT_BASE_URL=http://127.0.0.1:8001`)、agent 8001(仓库根起,另设
  `KM_AGENT_API_BASE_URL=http://127.0.0.1:8000`)、web 5173(`pnpm dev`);
  Keycloak 先用 `podman start keycloak-spike-keycloak-1`;登录用户 demo 落到本地种子
  账号 demo-keycloak(DEVELOPER+默认租户);测试数据源须切到「Sakila 影院」
  (默认数据源无业务表,问业务必答「未找到数据」)。
- 流式问答公开契约:`/chat/query/stream` 只用默认 SSE 消息(`data:` 行,不用
  `event:`),每条业务帧是 `{"type":"…","data":{…}}`,六种 type 为
  stage/sql/summary/tool_call/result/error;`result.data` 与同步 `/chat/query`
  响应同构。`trace` 诊断只走 Agent 内部流,不出公开面;`error.reason_code` 只允许
  `AGENT_UNAVAILABLE` / `STREAM_OUTPUT_FAILED`。前端用
  `apps/web/src/lib/chat-stream-validate.gen.js`(由契约生成)做运行时校验,
  坏帧/未知类型/字段不合格一律跳帧,不中断流。
- 问答轮真取消:流式响应头 `X-Trace-Id` 是本轮标识,
  `POST /chat/query/stream/{trace_id}/cancel` 返回 `CANCELLED`/`COMPLETED`;
  Agent 面按 trace_id 中止(`POST /internal/abort-query`);被取消轮落
  `chat_message.status=CANCELLED`,已取消行沿用原轮用户消息的落库时刻,历史顺序
  不会插到下一问之后。刷新、切会话、断网只是脱离接收,后台照常跑完并落库。
- 取消与收尾的先后:一轮收尾(落库 + 终态帧)一旦开始就不被请求取消打断;硬取消
  落在收尾段时等它做完,取消结果按收尾定。流式 FAILED 行带 trace_id,以失败收尾
  的轮再取消返回 `COMPLETED` 并带消息标识,不是 404。实现在
  `apps/api/kongming/chat/stream_runner.py`。
- 在途轮跨实例共享:持有实例在 Redis 记 `chat:turn:{trace_id}`,TTL
  `KM_TURN_REGISTRY_TTL_SEC` 缺省 900s;取消落任一实例未命中本地登记时,凭该键经
  内部面 `POST /internal/chat/turn-cancel` 转给持有实例。多实例部署必须给每个实例
  配唯一可达的 `KM_API_SELF_BASE_URL`,且登记 TTL 大于 Agent 超时 × 最坏阶段数;
  Redis 故障退化为落库兜底,单实例语义不变。
- 真取消已知限制:通知 Agent 等 3 秒、等本轮收敛 5 秒;超时才硬取消本地任务,
  Agent 面挂起时取消请求约 8 秒返回;被中断的模型调用不产生 `sys_llm_usage` 行;
  质量观测面不计已取消轮次;跨实例持有实例硬崩后最长一个登记 TTL 内取消转落库兜底。
- 语音通话(ADR-013,可选启用):`KM_VOICE_API_KEY`、`KM_VOICE_REALTIME_BASE_URL`、
  `KM_VOICE_MODEL` 三项齐备才启用(缺任一项端点 503;页面凭
  `GET /voice/calls/capability` 的启用位决定入口可用还是置灰)。接入地址现值
  `https://cn-beijing.maas.qianwenaiapi.com/api/v1/webrtc/realtime`,模型
  `qwen3.8-omni-flash-realtime`,音色 Serena。声音浏览器直连(标准 `oai-events`
  DataChannel,发 offer 前等 ICE 收集完成,`pc.ontrack` 播放远端音频),控制回后端
  (`/voice/calls/channel`,一次性票据)。语音模型只当耳嘴,每轮语音提问走与打字
  相同的流式问答端点,请求带可选 `voice_turn_token`(一次性、绑定本人,终态回填
  同时校验 trace_id 与 user_id)。
- 语音会话固定两个工具:`ask_kongming` 与 `stop_current_query`。断句必须用
  `semantic_vad`,静默下限 2000ms(官方默认 800);`session.updated` 回显的
  turn_detection 与 voice 会写日志。保护分三层:①等待期/空闲期模型未拿结果就开口,
  服务端在首个非工具输出项到达时 `response.cancel`;等待期取消会把原调用标记为失效,
  结果回来改文字补交。②模型出声时用户开口,后端取消该回应,浏览器按回应 id 保持
  静音,残余音频不再解除。③远端音频默认静音,只有结果回填的 `playback.allow`
  放行下一次回应;固定应答/停止确认是本地预录,不走远端放行。
- 语音停止与通话收口:停止工具取消在途轮、回工具结果但不触发回应、播本地
  `zh-stopped.m4a`;不计轮数、不落停止句,旧轮留下已取消说明。挂断先由服务端落
  `hung_up` 审计再关闭文字通道;通道先因网络断开时 DELETE 返回 404 属正常竞态,
  前端忽略即可。达时长上限的审计原因必须记 `max_duration`,不能被通道断开覆盖。
  通话审计只落 `chat.voice.call_end`(不进 `sys_llm_usage`),含轮数、打断次数、
  模型报告的音频用量与结束原因。
- 语音已知限制:真机冒烟待重走(真实麦克风/扬声器/模型,含受限网络禁 UDP);受限
  网络不做中转;回声消除依赖浏览器;停止句不落库;被取消的回应不报用量;放行只
  保证「有结果才允许说」,不逐字校验结果后的自由发挥。固定应答/停止录音在
  `apps/web/public/voice/`(qwen3-tts-flash 合成,44.1kHz AAC),改音色须重录。
- 文档检索:向量 + 全文(ts_rank,zhparser 可选)+ 可选实体扩展路 → RRF(k=60)
  → 可选精排 → 父块 top-5;精排与 zhparser 已验收未启用(条件见开放决策项)。
  被索引文本=文档名+块正文;全文兜底零分命中不进入融合;文档多查询分解已落地
  (子查询 2-3、注入上限 8、失败回落原问)。数据源内省按 `pg_inherits` 排除继承/
  分区子表(父表保留),不依赖表名;新 PostgreSQL 租户首次内省即生效,存量知识须
  重扫清理旧子表条目;非 PostgreSQL 数据源需在对应内省适配器实现同样的父子语义。
- 成功 SQL 会写回 `rag.query_history` 作为 few-shot;错误但执行成功的样本会自我
  强化。改口径/表义后须同时停用相冲突的历史样本并清理 RAG 上下文缓存,不能只改
  提示。Sakila 按月租赁口径的坏样本已停用;`_QUERY_SEMANTICS` 已 bump 到
  `and-cw2`,旧上下文缓存自然失效。
- 评测:15 文档 28 题、BM25 80 题、Wiki 26 题、lab 74 题与判别变体、K8s 30 题
  (73 篇/1400 父块,AI 编写+程序校验+AI 复核,未经人工领域评审);K8s 基线融合
  0.833/全跳 0.733,分解后 0.883/0.800;跨文档多跳仍不承诺。
- 多模型注册表:人工条目按「模型×端点」编队,别名优先;TITLER/SUGGEST/DESCRIBE→
  qwen-flash;主模型与 embedding 走 maas 镜像域,dashscope 回滚。Agent 的 LLM/
  向量/模型注册表可整体指向 muniu(隔离实例、独立 SQLite、一次性 `mk-` key,
  不碰用户常驻实例);取消时正在路上的模型请求会被掐断,网关记 `client_cancel`、
  0 token。启动 muniu 时复制 muniu.yaml 删 `admin_oidc` 段(配置比二进制旧),
  库指向临时文件,`DASHSCOPE_API_KEY` 取孔明根 `.env` 的 `KM_AGENT_LLM_API_KEY`。
- 门禁各项目前:api-check 1892 passed/10 skipped、覆盖率 77.80%;agent-check 627
  passed;liannu-check 23 passed;web 236 tests + 2 e2e;
  `openspec validate --all --strict` 55/55(归档 4 个变更后,在途仅
  add-sql-semantic-guard 提案)。`make check` 全量绿,`contracts-check` 以已提交
  契约为基线复跑零差异。仓库根若残留 `.playwright-mcp/`(浏览器工具快照,已 git
  忽略)会让 `lint-config` 的 yamllint 报红,删掉即可。

## 下一步

1. CI 全 job 因 GitHub Actions 账单锁定未启动;解锁后观察 contracts-check、镜像
   扫描与 e2e;eval-nightly 仍只手动触发。
2. 反代部署确认:`KM_AUTH_COOKIE_SECURE`、可信代理头、登录限流口径;反代须把
   `/api` 前缀剥离到后端根路由。多实例按 `KM_API_SELF_BASE_URL` 逐实例配唯一可达
   地址,并确认实例间内部面互通。
3. eval-nightly 首次运行前配置 `KM_AGENT_LLM_API_KEY` 与 `KM_EVAL_ACCESS_TOKEN`。
4. 语音真机冒烟:停止意图、空闲自答拦截、停顿容忍、插话即停、叠音、音色、
   WebRTC 设备面与受限网络禁 UDP;异常先看 `chrome://webrtc-internals` 的 ICE 与
   DataChannel 状态。`2026-11-25-add-voice-intent-guard` 的 4.3 为此保留未完成。
5. `add-sql-semantic-guard` 仅立项、17 项任务全部未实现;实施前先过 tasks 1.4
   (动态数据源 PK/FK 图采集)与 3.1(事件表被金额表引用仍判事件)两个前置门。

## 开放决策项

- 文档精排未启用:新索引 0.964→0.893(-7.1pp,两模型一致),旧索引 1.000→0.982;
  启用条件=语料扩容后出现非负收益。
- zhparser 未切换:本语料 A/B 退化(新索引 0.964→0.946,旧索引 1.000→0.929);
  词面缺口语料的价值已在 lab 显现;复评条件=真实语料出现词面缺口。
- 块索引上下文已生效:判别语料 0.291→1.000;15 文档 0.964(-3.6pp,009/012 各丢
  「租赁规则」一跳,012 为登记豁免边界题);收紧红线前先逐题评估 009/012。
- K8s 跨文档多跳全跳 1/6;剩余缺口受查询改写与候选精度限制,不设通过线。
- refresh cookie 作用域为 API 同源全路径:同源信任边界内接受;若同源再挂其他应用,
  复评限定方案(仅 `/api` 或双 cookie)。`/auth/refresh` 响应仍回传明文
  refresh_token,是否收窄为只回 access 待拍板。
- `sys_user.id` 依赖 Keycloak sub 为 UUID;非 UUID sub 需迁移。登录限流为进程内、
  每 worker 独立;严格全局阈值需共享后端。
- 语音多实例:当前在途通话表、票据与轮关联表都是进程内单例,多实例部署前须外置
  或做实例粘滞;语音模型密钥只在 api,不下发浏览器。
- 数据源内省父子语义目前只覆盖 PostgreSQL;其他数据库适配器需补同语义,不能靠
  表名过滤兜底。

## 验收记录

- 2026-11-25 门禁:api-check 1892 passed/10 skipped、覆盖率 77.80%;agent-check
  627 passed;web-check 236 tests + 2 e2e(构建/体积限制通过);dead-code、dup-check、
  lint-config、OpenSpec strict 均通过。`make contracts` 连续重复生成零差异,
  `oasdiff breaking` 无 BREAKING。
- 2026-11-25 语音单元与集成:状态机、准入/票据/牵线、语音轮关联、流式取消、
  WebSocket 通道、播放闸门与前端 UI 用例通过;未做真实麦克风/扬声器真机验收。
- 2026-11-24~25 认证、流式契约、问答取消、跨实例取消、Keycloak 三端 SSO 的
  浏览器/协议级验收均已记录在对应 OpenSpec 归档与 git 历史。
