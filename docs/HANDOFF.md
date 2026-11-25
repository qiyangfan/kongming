# HANDOFF — 孔明(kongming)

> 四块:当前状态 / 下一步 / 开放决策项 / 验收记录;变更细节归 git log 与 OpenSpec 归档。

## 当前状态(2026-11-25)

- 本地开发口径:api 8000、agent 8001、web 5173。api 配置在 `apps/api/.env`(按
  工作目录加载);agent 没有独立 `.env`,配置在仓库根 `.env`,须从仓库根启动。
  本机 PostgreSQL/Redis 用统一环境栈 `~/workspace/env`(5432/6379),不要用
  `make up` 抢端口;测试数据源须切到「Sakila 影院」。仓库根残留
  `.playwright-mcp/` 会让 lint-config 的 yamllint 报红,删掉即可。
- 统一身份:Keycloak 是唯一登录入口(`KM_KEYCLOAK_*`、
  `/auth/keycloak/callback`),授权仍在本地(角色/租户/`data_scope`/本地 RS256
  会话令牌)。`/auth/login` 已移除,评测登录取现签 access JWT
  (`KM_EVAL_ACCESS_TOKEN`)。访问令牌只存内存,刷新/登出只认 HttpOnly cookie。
  三端 SSO 已验收(muniu/kongming/jinguan):issuer 必须统一为
  `127.0.0.1:8080`、realm `longzhong`;多标签页 state 已并发安全。登录 demo
  落到本地种子账号 demo-keycloak(DEVELOPER+默认租户)。
- 实时问答公开契约:`/chat/query/stream` 只用默认 SSE 消息(`data:` 行),
  业务帧为 `{"type":"…","data":{…}}`,六种 type 为
  stage/sql/summary/tool_call/result/error;`result.data` 与同步 `/chat/query`
  同构。`trace` 只走 Agent 内部流,不出公开面;`error.reason_code` 只允许
  `AGENT_UNAVAILABLE`/`STREAM_OUTPUT_FAILED`。前端用生成的
  `chat-stream-validate.gen.js` 做运行时校验,坏帧/未知类型/字段不合格一律
  跳帧,不中断流。
- 问答轮取消:响应头 `X-Trace-Id` 标识本轮;
  `POST /chat/query/stream/{trace_id}/cancel` 返回 `CANCELLED`/`COMPLETED`。
  Agent 按 trace_id 中止;取消轮落 `chat_message.status=CANCELLED`,历史顺序
  沿用原轮用户消息时刻。收尾已开始后不被请求取消打断;流式 FAILED 行带
  trace_id,已完成轮再取消返回 `COMPLETED` 并带消息标识。断开连接只是脱离
  接收,后台仍会收口落库。
- 在途轮跨实例共享:持有实例在 Redis 记 `chat:turn:{trace_id}`,TTL
  `KM_TURN_REGISTRY_TTL_SEC` 缺省 900s;取消落其他实例未命中本地登记时,经
  内部面 `POST /internal/chat/turn-cancel` 转发给持有实例。多实例必须给每个
  实例配唯一可达的 `KM_API_SELF_BASE_URL`,登记 TTL 必须大于 Agent 超时乘
  最坏阶段数。Redis 故障退化为落库兜底。已知限制:通知 Agent 等 3 秒、等
  本轮收敛 5 秒;超时才硬取消本地任务;被中断的模型调用不产生
  `sys_llm_usage` 行;质量观测面不计已取消轮次。
- 语音通话(ADR-013/014,可选启用):`KM_VOICE_API_KEY`、
  `KM_VOICE_REALTIME_BASE_URL`、`KM_VOICE_MODEL` 三项齐备才启用;缺任一项
  端点 503,页面凭 `GET /voice/calls/capability` 决定入口。接入地址现值
  `https://cn-beijing.maas.qianwenaiapi.com/api/v1/webrtc/realtime`,模型
  `qwen3.8-omni-flash-realtime`,音色 Serena。声音浏览器直连(标准
  `oai-events` DataChannel),控制回后端(`/voice/calls/channel`,票据优先
  `Sec-WebSocket-Protocol`,query 仅兼容一版);语音模型只当耳嘴,每轮提问走
  与打字相同的流式问答端点,请求带可选一次性 `voice_turn_token`(绑定本人,
  终态同时校验 trace_id 与 user_id)。固定工具 `ask_kongming` /
  `stop_current_query`;断句 `semantic_vad`,静默下限 2000ms;保护分三层:
  等待/空闲期未拿结果开口即取消、用户开口打断播放并按回应 id 保持静音、
  结果回填才放行远端音频。停止工具取消在途轮、回结果但不触发回应、播本地
  `zh-stopped.m4a`;挂断先落 `hung_up` 审计再关闭通道,达时长上限记
  `max_duration`。审计 `chat.voice.call_end` 含轮数、打断、浏览器报告音频
  用量与结束原因;日预算拒绝落 `chat.voice.call_rejected`。
- 语音防护与服务端闸:单用户每分钟
  `KM_VOICE_ESTABLISH_RATE_PER_MINUTE`(默认 6)、用户/部署 24 小时成功建联
  预算 `KM_VOICE_DAILY_CALLS_PER_USER`(20)/
  `KM_VOICE_DAILY_CALLS_PER_DEPLOYMENT`(100),0=关闭;超限 429。预算释放到 0
  时删除计数键,窗口从首个成功建联起算。`KM_VOICE_MAX_CALL_SECONDS` 只是
  本地授权,到点通知浏览器收口,不强制供应商断连;上游单会话硬上限 120 分钟
  且没有服务端关闭接口,改过的客户端可保留媒体会话。单帧上限 64KB(镜像
  uvicorn 256KB);审计轮数/用量来自浏览器转发事件,只作参考,不作计费依据。
  媒体直连;本阶段不引入中转(带宽不是瓶颈,成本在 CPU/运维/时延);重开
  条件见 ADR-013「媒体中转」。
- 文档检索:向量 + 全文(ts_rank,zhparser 可选)+ 可选实体扩展路 → RRF(k=60)
  → 可选精排 → 父块 top-5。精排与 zhparser 已验收未启用;被索引文本=文档名+
  块正文;全文兜底零分命中不进入融合;多查询分解已落地(子查询 2-3、注入上限
  8、失败回落原问)。数据源内省按 `pg_inherits` 排除继承/分区子表。
- 成功 SQL 会写回 `rag.query_history` 作为 few-shot;错误但执行成功的样本会
  自我强化。改口径/表义后须同时停用相冲突的历史样本并清理 RAG 上下文缓存;
  Sakila 按月租赁坏样本已停用,`_QUERY_SEMANTICS` 为 `and-cw2`。SQL 语义护栏
  已落地:`/internal/rag/retrieve` 纯增 `schema_graph`,召回后按图补桥接表,
  recipe validate 前执行图外表/列、金额计数冒充事件笔数、一对多未去重、
  金额时间冒充事件时间、唯一归属路径被绕过等确定性规则;违规不执行、定向
  自愈、重试耗尽 fail-closed(`KM_AGENT_SQL_SEMANTIC_GUARD` 缺省 true)。
- 多模型注册表:人工条目按「模型×端点」编队,别名优先;TITLER/SUGGEST/
  DESCRIBE→qwen-flash;主模型与 embedding 走 maas 镜像域,dashscope 回滚。
  Agent 的 LLM/向量/模型注册表可整体指向 muniu(隔离实例、独立 SQLite、
  一次性 `mk-` key,不碰用户常驻实例);取消时在路上的模型请求会被掐断,
  网关记 `client_cancel`、0 token。启动 muniu 时复制 muniu.yaml 删
  `admin_oidc` 段(配置比二进制旧),库指向临时文件,`DASHSCOPE_API_KEY` 取
  孔明根 `.env` 的 `KM_AGENT_LLM_API_KEY`。

## 下一步

1. CI 全 job 因 GitHub Actions 账单锁定未启动;解锁后观察 contracts-check、镜像
   扫描与 e2e;eval-nightly 仍只手动触发。
2. 反代部署确认:`KM_AUTH_COOKIE_SECURE`、可信代理头、登录限流口径;反代须把
   `/api` 前缀剥离到后端根路由,并透传 WebSocket Upgrade 与
   `Sec-WebSocket-Protocol`(语音通道票据走子协议)。多实例按
   `KM_API_SELF_BASE_URL` 逐实例配唯一可达地址,并确认实例间内部面互通。
3. eval-nightly 首次运行前配置 `KM_AGENT_LLM_API_KEY` 与 `KM_EVAL_ACCESS_TOKEN`。
4. 语音真机冒烟:停止意图、空闲自答拦截、停顿容忍、插话即停、叠音、音色、
   WebRTC 设备面与受限网络禁 UDP;异常先看 `chrome://webrtc-internals` 的 ICE 与
   DataChannel 状态。`2026-11-25-add-voice-intent-guard` 4.3 与
   `2026-11-25-add-voice-call-guards` 7.2 为此保留未完成。

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
- 语音多实例:票据、准入与预算闸在 Redis;在途通话表与语音轮关联表在进程内,
  多实例部署前须外置或做实例粘滞;语音模型密钥只在 api,不下发浏览器。
- 数据源内省父子语义目前只覆盖 PostgreSQL;其他数据库适配器需补同语义,不能靠
  表名过滤兜底。非 PostgreSQL 动态源同样没有结构化图,语义硬规则自动跳过。
- 语义护栏目前只接 recipe 的 validate/selfheal;ReAct 走工具网关的
  `validate_sql`,未消费 schema 图。若要让 ReAct 也享受同一规则,需要另立项把
  图/校验器下沉到工具面,不能只在 prompt 里重复规则。

## 验收记录

- 2026-11-25 门禁:api-check 1920 passed/10 skipped、覆盖率 78.18%;agent-check
  646 passed、覆盖率 86.86%;liannu-check 23 passed;web 238 tests + 2 e2e
  (构建/体积限制通过);dead-code、dup-check、lint-config、OpenSpec strict
  54/54 均通过;`make contracts` 重复生成零差异。
- 2026-11-25 SQL 语义护栏:真实 Sakila 图 8 表/桥接通过;错误 SQL 变体 6/6
  拦截、正确 SQL 0/4 误拒;真实模型门店笔数题首轮被归属规则拦下、自愈后
  PASS(8040/8004),复杂「笔数+支付金额」题两轮未改正按 fail-closed 结束。
  数据源连接变更后的 RAG 缓存失效已覆盖连接变更与仅改名两条路径。
- 2026-11-25 语音防护:`make api-check` 1920 passed/10 skipped、覆盖率 78.18%;
  `make web-check` 全链路绿(e2e 2 passed);频次/预算/租户/子协议/帧上限有
  API 集成测试;契约重生成零差异。真机冒烟见下一步 4。
- 2026-11-25 语音单元与集成:状态机、准入/票据/牵线、语音轮关联、流式取消、
  WebSocket 通道、播放闸门与前端 UI 用例通过;未做真实麦克风/扬声器真机验收。
- 2026-11-24~25 认证、流式契约、问答取消、跨实例取消、Keycloak 三端 SSO 的
  浏览器/协议级验收均已记录在对应 OpenSpec 归档与 git 历史。
