# HANDOFF — 孔明(kongming)

> 四块:当前状态 / 下一步 / 开放决策项 / 验收记录;变更细节归 git log 与 OpenSpec 归档。

## 当前状态(2026-11-27)

### 本地开发

- api 8000、agent 8001、web 5173、admin 5174。`apps/api` 按工作目录读取 `.env`;
  agent 无独立 `.env`,配置在仓库根 `.env`,须从仓库根启动。
- 本机 PostgreSQL/Redis 用统一环境栈 `~/workspace/env`(5432/6379),不要用
  `make up` 抢端口;测试数据源切到「Sakila 影院」。仓库根残留
  `.playwright-mcp/` 会让 lint-config 的 yamllint 报红,删掉即可。
- `make check` 需要 `openspec`、pnpm 与 `/opt/podman/bin` 在 PATH;只跑 Web
  子集时用 `mise exec -- pnpm ...`。`make check` 不覆盖 `apps/admin`,另跑
  `make admin-check`。

### OpenSpec 与归档状态

- Active changes:0。10 个 `2026-11-26-add-kongming-*` 运行时控制面 change 已归档;
  `payment-gateway` 能力随支付退役删除 spec,`developer-portal-billing`/`web-admin`/
  `rbac` 已按 REMOVED 增量移除退役需求。
- 归档时保留的阻塞:`control-snapshot-consumer` 2.2 与
  `service-registration-client` 2.1 的 X-01 跨仓契约对拍未跑;
  `runtime-console` 2.2 响应式亮暗走查未做。三条在归档 tasks 中保持未勾选。
- `openspec validate --all --strict`:63/63。

### 身份与访问

- Keycloak 是唯一登录入口(`KM_KEYCLOAK_*`、`/auth/keycloak/callback`),授权仍在
  本地(角色/租户/`data_scope`/本地 RS256 会话令牌)。`/auth/login` 已移除,评测
  登录取现签 access JWT(`KM_EVAL_ACCESS_TOKEN`)。访问令牌只存内存,刷新/登出只认
  HttpOnly cookie。
- 三端 SSO 已验收(muniu/kongming/jinguan):issuer 统一为 `127.0.0.1:8080`、
  realm `longzhong`;多标签页 state 已并发安全。登录 demo 落到本地种子账号
  demo-keycloak(DEVELOPER+默认租户)。
- 管理台独立会话:`apps/admin`(5174、`kongming-admin-web`、`/admin-auth/*`),
  access 5m、refresh 30m 绝对上限;refresh 票据带 `surface`,管理台与用户面互不
  接受对方 refresh。生产环境配置 `KM_CONTROL_READER_TOKEN` 即拒启,只允许
  Keycloak 服务 token。旧 `apps/web` `/admin/*` 由反代或 Vite dev/preview 308
  到管理台。
- `sys_user.id` 依赖 Keycloak sub 为 UUID;登录限流为进程内、每 worker 独立。

### 运行时控制面与管理台(ADR-0005)

- 服务默认 `KM_CONTROL_MODE=standalone`(本地种子/部署配置);`managed` 拉取锦官
  快照(last-known/版本单调/HMAC 验签)、注册心跳、claim/ack 命令、上报产品事件。
  安全类快照缺失/验签失败/版本回退时受保护请求 503(不阻塞 `/auth`、`/internal`、
  `/service-read`);控制面缺席时安全类按 last-known 继续,不做时间硬过期。
- 身份缓存按快照版本单调应用(advisory lock + `control.identity_state`);快照先落库、
  身份后补,失败由 304/同版本路径重试。多实例不会用旧版本覆盖新身份。
- 租户作用域唯一锚点是 `tenant_key`:权益/策略执行先用本地 `tenant_id` 解析
  `tenant_key`,再匹配快照;快照 `tenant_id` 只展示,不与本地 UUID 直接等值比较。
  策略包按 `tenant_keys` 匹配;`features` 仅展示,不推断功能开关。
- 本地命令 `POST /system/commands`(reindex/清缓存/取消任务)不依赖锦官;命令
  `reason` 进审计;非对象 `params` 拒绝并回执。跨服务命令只显示回执;服务只读投影
  `/service-read/{section}` 供锦官(本地 reader token 仅限本地演练或 Keycloak 服务
  token,字段脱敏、无写入口)。
- 租户/成员/粗角色由快照写入本地 last-known 执行缓存;用户写端点 403;管理台用户页
  只读。商店货架只列 `LISTED` 且按租户/权益过滤;`UNLISTED` 只可直链/直跑。
- 控制数据按天清理(已投递用量 90d、终态回执 180d、失联注册 7d;EXECUTING 回执
  不删;dry-run 可审计);心跳审计只在注册成功、首次失败、恢复时写。
- 本地 billing 域已删除(计划/账单/支付/门户),四张表改名 `legacy_*` 只读;
  `developer:portal`/`system:billing:manage` 权限点已从种子与角色绑定删除;
  权益/配额改由快照执行,standalone 用部署配置。`KM_BOT_QUOTA_DAILY_CALLS`
  控制网关 Bot 日配额(0=沿用 `KM_CHAT_QUOTA_DAILY_CALLS`)。
- break-glass:`apps/api/scripts/break_glass.py`(独立凭证/限时/原因/二次确认/审计;
  口令交互读取,不走 `--token` argv),见 `docs/BREAK-GLASS.md`;无 admin 端点。
- 管理台反代样例 `infra/admin/nginx.conf`:同源 `/api` 前缀剥离、CSP、
  `client_max_body_size 12m`、长任务 300s 超时;旧入口规则
  `infra/web/admin-redirect.conf`。真实反代尚未演练。

### 问答、流式与取消

- `/chat/query/stream` 只用默认 SSE 消息(`data:` 行),业务帧为
  `{"type":"…","data":{…}}`,六种 type 为 stage/sql/summary/tool_call/result/error;
  `result.data` 与同步 `/chat/query` 同构。`trace` 只走 Agent 内部流,不出公开面;
  `error.reason_code` 只允许 `AGENT_UNAVAILABLE`/`STREAM_OUTPUT_FAILED`。前端用
  生成的 `chat-stream-validate.gen.js` 做运行时校验,坏帧/未知类型/字段不合格
  一律跳帧,不中断流。
- 问答轮取消:响应头 `X-Trace-Id` 标识本轮;
  `POST /chat/query/stream/{trace_id}/cancel` 返回 `CANCELLED`/`COMPLETED`。
  Agent 按 trace_id 中止;取消轮落 `chat_message.status=CANCELLED`,历史顺序沿用
  原轮用户消息时刻。收尾已开始后不被请求取消打断;流式 FAILED 行带 trace_id,
  已完成轮再取消返回 `COMPLETED` 并带消息标识。断开连接只是脱离接收,后台仍会
  收口落库。
- 在途轮跨实例共享:持有实例在 Redis 记 `chat:turn:{trace_id}`,TTL
  `KM_TURN_REGISTRY_TTL_SEC` 缺省 900s;取消落其他实例未命中本地登记时,经内部面
  `POST /internal/chat/turn-cancel` 转发给持有实例。多实例必须给每个实例配唯一
  可达的 `KM_API_SELF_BASE_URL`,登记 TTL 必须大于 Agent 超时乘最坏阶段数。Redis
  故障退化为落库兜底。已知限制:通知 Agent 等 3 秒、等本轮收敛 5 秒;超时才硬
  取消本地任务;被中断的模型调用不产生 `sys_llm_usage` 行;质量观测面不计已取消
  轮次。

### 语音通话

- ADR-013/014,可选启用:`KM_VOICE_API_KEY`、`KM_VOICE_REALTIME_BASE_URL`、
  `KM_VOICE_MODEL` 三项齐备才启用;缺任一项端点 503,页面凭
  `GET /voice/calls/capability` 决定入口。接入地址现值
  `https://cn-beijing.maas.qianwenaiapi.com/api/v1/webrtc/realtime`,模型
  `qwen3.8-omni-flash-realtime`,音色 Serena。声音浏览器直连(标准 `oai-events`
  DataChannel),控制回后端(`/voice/calls/channel`,票据优先
  `Sec-WebSocket-Protocol`,query 仅兼容一版)。
- 语音模型只当耳嘴,每轮提问走与打字相同的流式问答端点,请求带可选一次性
  `voice_turn_token`(绑定本人,终态同时校验 trace_id 与 user_id)。固定工具
  `ask_kongming`/`stop_current_query`;断句 `semantic_vad`,静默下限 2000ms。
  结果回填才放行远端音频;用户开口打断播放并按回应 id 保持静音。查询轮不再播放
  固定开场白,静默进入查询;停止只由后端取消一次,前端只播本地
  `zh-stopped.m4a`,不再重复 `stopStreaming`。挂断先落 `hung_up` 审计再关闭通道,
  达时长上限记 `max_duration`。审计 `chat.voice.call_end` 含轮数、打断、浏览器
  报告音频用量与结束原因;日预算拒绝落 `chat.voice.call_rejected`。
- 防护:单用户每分钟 `KM_VOICE_ESTABLISH_RATE_PER_MINUTE`(默认 6)、用户/部署
  24 小时成功建联预算 `KM_VOICE_DAILY_CALLS_PER_USER`(20)/
  `KM_VOICE_DAILY_CALLS_PER_DEPLOYMENT`(100),0=关闭;超限 429。预算释放到 0 时
  删除计数键,窗口从首个成功建联起算。`KM_VOICE_MAX_CALL_SECONDS` 只是本地授权,
  到点通知浏览器收口,不强制供应商断连;上游单会话硬上限 120 分钟且没有服务端
  关闭接口,改过的客户端可保留媒体会话。单帧上限 64KB(镜像 uvicorn 256KB)。
  媒体直连;本阶段不引入中转,重开条件见 ADR-013「媒体中转」。
- 真机验收待跑,跟念稿见 `docs/voice-acceptance.md`(自动化子集已覆盖频次 429、
  用户预算 429、未绑租户 403、通道访问日志无 ticket)。

### RAG 与 SQL 语义

- 文档检索:向量 + 全文(ts_rank,zhparser 可选)+ 可选实体扩展路 → RRF(k=60)
  → 可选精排 → 父块 top-5。精排与 zhparser 已验收未启用;被索引文本=文档名+
  块正文;全文兜底零分命中不进入融合;多查询分解已落地(子查询 2-3、注入上限 8、
  失败回落原问)。数据源内省按 `pg_inherits` 排除继承/分区子表。
- 成功 SQL 会写回 `rag.query_history` 作为 few-shot;错误但执行成功的样本会自我
  强化。改口径/表义后须同时停用相冲突的历史样本并清理 RAG 上下文缓存;Sakila
  按月租赁坏样本已停用,`_QUERY_SEMANTICS` 为 `and-cw2`。
- SQL 语义护栏:`/internal/rag/retrieve` 纯增 `schema_graph`,召回后按图补桥接
  表;recipe validate 前执行图外表/列、金额计数冒充事件笔数、一对多未去重、金额
  时间冒充事件时间、唯一归属路径被绕过、桥接内连接丢行等确定性规则;违规不执行、
  定向自愈、重试耗尽 fail-closed(`KM_AGENT_SQL_SEMANTIC_GUARD` 缺省 true)。
- 桥接自动改写只接受基表-桥表外键等值连接;桥 JOIN 带过滤/额外等值条件时交
  selfheal,不再自动删桥。
- 全图响应保留完整表/列/角色/外键供语义校验,`/internal/rag/retrieve` 通过 HTTP
  gzip 压缩传输;SSE 不受影响。全图原始 JSON 仍完整组装。
- ReAct 走工具网关的 `validate_sql`,尚未消费 schema 图;它享受与 recipe 不同的
  规则集,见开放决策项。

### 答案契约与前端

- summary/insights 中的数字与含数字标签只经系统槽位渲染
  (`rows/stats/argmax/argmin/cell/q/calc/alias/nulls`),模型文本去槽位后不得含
  阿拉伯数字;未知槽位/越界即违约,整段弃用并走确定性兜底(单行单列数值直接作答,
  多行输出轮廓)。用户可见文本不出现内部兜底标记;违约类型与层级入 trace。
  自由文本数值白名单/剥句旧审计已退役;中文数字与报告生成链路不在契约内。
- SQL 产品卡默认只读展示(语法高亮、可复制),「编辑」是次级入口;重跑结果以
  新消息追加,不覆盖原卡。SQL 回退卡与正常产物共用同一组件,不再出现裸文本
  `<pre>`。围栏代码块只注册 SQL 高亮(refractor),其余语言原样;高亮色走
  `index.css` 的 `--code-*` 亮暗 token。

### 模型路由

- 多模型注册表由人工条目按「模型×端点」编队,别名优先;TITLER/SUGGEST/
  DESCRIBE→qwen-flash;主模型与 embedding 走 maas 镜像域,dashscope 回滚。
  Agent 的 LLM/向量/模型注册表可整体指向 muniu(隔离实例、独立 SQLite、一次性
  `mk-` key,不碰用户常驻实例);取消时在途模型请求会被掐断,网关记
  `client_cancel`、0 token。启动 muniu 时复制 muniu.yaml 删 `admin_oidc` 段,
  库指向临时文件,`DASHSCOPE_API_KEY` 取孔明根 `.env` 的 `KM_AGENT_LLM_API_KEY`。

## 下一步

1. X-01 契约冻结后补跑 `control-snapshot-consumer` 2.2 与
   `service-registration-client` 2.1 的跨仓对拍;冻结前不得对外宣称跨仓联通。
2. fujie 创建 `kongming-admin-web` 与机器 client,完成真实 Keycloak 登录/角色授权/
   会话验收;当前管理台以 fake provider 验证回调语义。
3. 真实反代演练:`/api` 前缀剥离、CSP、`/admin/*` 308、WebSocket Upgrade 与
   `Sec-WebSocket-Protocol` 透传;确认 `KM_AUTH_COOKIE_SECURE`、可信代理头、
   登录限流口径。
4. 管理台响应式 375/768/1280 × 亮暗走查(归档的 runtime-console 2.2),
   以及登录/用户/Control 页的亮暗两态验收。
5. 工作流/Bot 维度产品事件待 X-01 事件类型与维度冻结后接入;当前事件挂点覆盖
   chat/tool/task/report。
6. 语音真机冒烟:停止意图、空闲自答拦截、停顿容忍、插话即停、叠音、音色、
   WebRTC 设备面与受限网络禁 UDP;异常先看 `chrome://webrtc-internals`。
7. eval-nightly 首次运行前配置 `KM_AGENT_LLM_API_KEY` 与 `KM_EVAL_ACCESS_TOKEN`;
   CI 仍受 GitHub Actions 账单锁定影响。
8. 清理 `mobile-experience` 中仍引用旧用户管理页编辑/删除的场景,改为管理台只读
   身份视图;这是归档后遗留的 spec 口径债。

## 开放决策项

- 控制面 wire 契约(v1 快照/命令/用量/注册)为本仓自定;X-01 冻结后统一对拍。
  `tenant_key` 作为跨平面作用域锚点、`features` 仅展示不作功能开关,需 X-01
  书面确认;若锦官要求 `tenant_id` 或功能开关枚举,按契约再改。
- 安全类快照按 ADR-0005 使用 last-known,不做时间硬过期;若业务要求硬过期,
  需单独 ADR 并明确停锦官即停服务的可用性影响。
- 管理台角色撤销窗口由 30m 绝对会话封顶;若要求即时吊销,需增加 Keycloak 会话
  绑定/introspection,属外部依赖变更。
- 管理台未配置锦官外链地址:计费/共享控制入口直接移除,是否需要外链待部署口径。
- refresh cookie 作用域为 API 同源全路径:同源信任边界内接受;若同源再挂其他
  应用,复评限定方案(仅 `/api` 或双 cookie)。`/auth/refresh` 响应仍回传明文
  refresh_token,是否收窄为只回 access 待拍板。
- 文档精排未启用:新索引 0.964→0.893(-7.1pp,两模型一致),旧索引 1.000→0.982;
  启用条件=语料扩容后出现非负收益。
- zhparser 未切换:本语料 A/B 退化(新索引 0.964→0.946,旧索引 1.000→0.929);
  词面缺口语料的价值已在 lab 显现;复评条件=真实语料出现词面缺口。
- 块索引上下文已生效:判别语料 0.291→1.000;15 文档 0.964(-3.6pp,009/012 各丢
  「租赁规则」一跳,012 为登记豁免边界题);收紧红线前先逐题评估 009/012。
- K8s 跨文档多跳全跳 1/6;剩余缺口受查询改写与候选精度限制,不设通过线。
- 语音多实例:票据、准入与预算闸在 Redis;在途通话表与语音轮关联表在进程内,
  多实例部署前须外置或做实例粘滞;语音模型密钥只在 api,不下发浏览器。
- 数据源内省父子语义目前只覆盖 PostgreSQL;其他数据库适配器需补同语义,不能靠
  表名过滤兜底。非 PostgreSQL 动态源同样没有结构化图,语义硬规则自动跳过。
- 语义护栏目前只接 recipe 的 validate/selfheal;ReAct 走工具网关的
  `validate_sql`,未消费 schema 图。若要让 ReAct 也享受同一规则,需要另立项把
  图/校验器下沉到工具面,不能只在 prompt 里重复规则。
- 语义护栏的图管辖=数据源允许 schema:管辖内、图中不存在的表会从提示词剔除
  (知识残留);管辖外 schema 的表不做剔除。`known_tables`(全图清单)用于区分
  「未召回已知表」与「库中不存在表」,后者仍硬拒。
- 「按月」类问句同时要求事件笔数与金额时口径两可(租赁月 vs 支付月),已在
  `sakila_semantic_001` 题面消歧(按租赁月份);残余是模型对
  `COUNT(DISTINCT r.rental_id)` 修正的执行力方差(单题复验 2/3),不改护栏
  放行、也不做 SQL 自动改写。
- schema 图全量传输已用 gzip;若线上出现 CPU/内存瓶颈,再评估协议级裁剪
  (只下发召见表列详情 + 全量外键/known_tables),不在渲染层猜语义。

## 验收记录

- 2026-11-27 归档收口:`openspec validate --all --strict` 63/63;10 个运行时控制面
  change 已归档,`payment-gateway` 能力退役;归档阻塞见「OpenSpec 与归档状态」。
- 2026-11-27 全量门禁:`make check` exit 0。api-check 1916 passed/10 skipped,
  覆盖率 78.03%;agent-check 719 passed;liannu-check 23 passed;
  web-check typecheck/lint/madge/lint:css/format/vitest(254)/build/size/e2e(3)
  通过;dead-code、dup-check、deps-audit、license、lint-config、lock、compose、
  security-cov、secret-scan 通过;契约导出与生成物零漂移。
- 2026-11-27 管理台门禁(前端与归档前一致):admin typecheck/lint/format/
  test(6)/build/e2e(2) 通过;真实 Keycloak、fujie client、反代 CSP 与响应式
  亮暗走查未验收。
- 2026-11-26 SQL 问答全量评测轮(隔离栈 18000/18001 加载工作区代码,真实
  Sakila/Olist,57 题):宽松 56/57(98%)、严格 36/57(63%)、表命中 53/57(93%)、
  动态数据源 4/4,指纹 4aabbc17。唯一失败 sakila_semantic_001 生成变体
  (COUNT 去重口径,登记边界);旧基线 3f78efa7 不可直接比较。
- 2026-11-27 评审修复:ReAct 多次执行结果槽位按 r0/r1 作用域隔离;完整图用于
  未召见表列校验;桥接自动改写只接受可证外键配对;计算槽位重复键作用域化;焦点
  维度、数据引用、百分比量纲与 FILTER 聚合回归补齐;API 多指标列标签/团队说明
  隔离/失败回滚/known_tables;Web refractor 细粒度、复制反馈、SQL 围栏与回退卡。
- 2026-11-26/27 门禁补验:槽位契约与 SQL 卡、语音防护、结构化图与语义护栏均有
  回归测试;语音真机与反代部署仍未验收。
- 2026-11-25 语音防护:`make api-check` 1920 passed/10 skipped、覆盖率 78.18%;
  `make web-check` 全链路绿(e2e 2 passed);频次/预算/租户/子协议/帧上限有 API
  集成测试;契约重生成零差异。
- 2026-11-24~25 认证、流式契约、问答取消、跨实例取消、Keycloak 三端 SSO 的
  浏览器/协议级验收记录见对应 OpenSpec 归档与 git 历史。
