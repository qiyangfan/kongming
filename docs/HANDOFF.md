# HANDOFF — Kongming 交接文档

> **本文定位**:面向新会话的开工指引,只记「代码里看不出来」的当前事实。
> 不堆变更日志——那是 git 的职责。变更明细三处可查:git log 提交信息、
> `openspec/changes/archive/`(每变更 proposal/design/tasks/spec delta 工件)、
> 文档历史版本(`git show <提交>:docs/HANDOFF.md`)。
> 维护纪律:每变更收尾只更新「当前状态」一行与必要的教训/终态条目;
> 逐变更长段一律不进本文。

## 当前状态(2026-11-04)

- **评分/兜底/观测三变更现态(已落地归档,工件在
  `openspec/changes/archive/2026-11-03-*`)**:
  ①答案异步评分:SUCCESS 问答轮图外异步评审——judge 只产出结构化主张
  分解,分数由控制面按确定性公式重算;权重/封顶/净化规则单源
  `apps/api/kongming/chat/scoring.py`,评审 prompt 侧
  `apps/agent/kongming_agent/scorer.py` 的分级口径须与权重表同源演化,
  两侧不得单边漂移。幂等=message_id UNIQUE+ON CONFLICT DO NOTHING;
  开关 `KM_ANSWER_SCORING_ENABLED`(缺省 true)/`KM_ANSWER_SCORING_TIMEOUT_SEC`
  (90s);表 chat_turn_score(0059);scorer.py 不进 prompt_fingerprint
  资产集(主图指纹零影响)。不评分范围:非 SUCCESS、非落库轮、ReAct
  链路、证据不可装配(无结果集且无有效来源引用)。
  ②兜底分类:fallback_type 诊断列(0060,CHECK 枚举 CHITCHAT/CLARIFY)
  复用 NO_DATA 中性通道——四态映射零新增;词表直通三族静态文案(招呼/
  致谢不再答数据缺失);route_intent 一帧三用(四意图+ambiguous/clarify,
  判闲聊口径=完全无取数意图,闲聊优先于澄清);防循环=api 读
  `last_fallback_type` 注入载荷,上一轮 CLARIFY 则本轮不命中澄清;
  no_data 率分子分母剔除兜底轮(原始计数如实保留)。
  ③观测深化:stage_trace 诊断列(0061,流式轮消费端就地收集 running/
  done 帧,cap 64 超限带截断标记;同步轮如实为 null;传输失败轮落已
  收集部分)。质量台新端点 /system/chat-quality/score|context|stage-trace|
  export——score/stage-trace 对不可见租户统一 404 防探测(stage_trace_detail
  曾把「行不存在」与「轨迹 null」混同致 404 不可达,已修并由测试钉住);
  串联 /context 锚点必含、上限 50;CSV 导出共享 `chat/export.py` 的
  to_csv(BOM/RFC4180/公式注入中和),5000 行截断附标记行,导出落操作
  日志(add 后必须 commit——`_Oper.log` 缺提交曾在本域翻车;chat 域
  costgate 的同款问题仍挂账待裁决)。质量台前端:AI 平均分/AI 评分列
  (未评分≠0 分,低于阈值红,点击开分解详情)/分类徽标与筛选/导出/
  轨迹与串联抽屉。
- **prompt 指纹与评估现值**:INTENT_ROUTE_SYSTEM 扩为四意图+ambiguous/
  clarify 后指纹已重建,现值 **0b54ecda**——evals 基线对比须用同指纹
  轮次。eval 三层实跑(2026-11-03 本地 live 栈):smoke 7/7(含 judge
  抽检层)、full 1/1、multiturn 12/12。multiturn 一题曾同因连败:LLM
  反复生成 `SELECT DISTINCT … ORDER BY 未投影列`(PG 硬约束),教训=
  **selfheal 喂回抽象指令修不动模型,须给逐字正确形态模板并明令不要
  什么**(`_selfheal_guidance` 的 DISTINCT 分支即该形态,测试锚定)。
- **pyright strict 的 JSON 边界解析模式坑(本批实证)**:防御 isinstance
  在形参已声明 dict 时会被 strict 判冗余;正确形态=形参标 `object`
  保留守卫(守卫常是被测契约,如 StageTraceCollector 畸形帧跳过)再
  `cast("dict[str, object]", …)` 收窄——**不要删守卫**。另:本批曾只跑
  ruff+mypy 漏掉 pyright/format 两个 make check 组成项连红,收工门禁按
  Makefile 全链跑,勿手搓单项(钉版与 `PYRIGHT_PYTHON_FORCE_VERSION=latest`
  双口径都要过,latest 才是 CI 同款)。
- **本地环境现值增量(2026-11-03/04)**:dev 库已迁移至 head 0061;
  olist 数据源已在 dev 库注册(sys_datasource:datasource_key=olist→
  tenant_olist,此前 olist 冒烟 NO_DATA 即缺此行);评估账号
  **eval-owner/EvalOwner#2026a**(绑 sakila/olist/default 三租户,可删);
  **admin 密码 KmSmoke#2026a 已实测失效**(连败触发 auth:lock:admin,
  锁经 Redis 清键解除,密码未动)——勿再猜 admin 凭据,评估一律自建
  账号。live 冒烟栈:api:8000/agent:8001/web:5173 + ~/workspace/env
  (core+pg)。
- **门禁现值(2026-11-04)**:api 1613/agent 589/web 134 全绿;pyright
  钉版+latest 双口径双 app 0、lint-imports 27 kept;openspec 主 specs
  52 passed 0 failed(>500 字符 WARNING 为存量噪声);openapi/前端类型
  随三变更再生成,无漂移。

## 当前状态(2026-10-22)

- **搜索索引评估边界**:chat、datasource、store、system 当前搜索均为
  `ILIKE` 包含匹配。临时表验证表明 `pg_trgm` 可为含足够 trigram 的模式提供
  索引路径，短词仍会扫描；合成数据命中分散时索引计划仍读取全部堆页，不能
  据此认定业务表有读收益。当前未为这些字段增加生产索引；新增索引前须以
  对应业务表分布和完整查询执行 `EXPLAIN (ANALYZE, BUFFERS)`，同时评估写入
  与存储成本。

## 当前状态(2026-10-20)

- **工具域退役与向量升级现态(2026-10-20,四变更落地)**:①ticket 工单域
  与 Siku 知识库接入整体退役(remove-ticket-domain / remove-siku-integration):
  `ticket_*`/`knowledge_search` 工具、`KM_TICKET_*`/`KM_SIKU_*` 配置、
  `tool:ticket:*`/`tool:knowledge:search` 权限点(迁移 0057 精确删除两项
  ticket 权限及绑定,历史审计保留)全部移除;遗留工具名直调与存量待确认
  ticket_create 均 fail-closed 零出站(tests/test_toolcall.py 退役回归);
  需要工单/外部知识访问改走租户级通用 HTTP 连接器(099)。②问答速率窗口
  平滑(smooth-chat-rate-windows):固定窗口计数改单条 Lua 原子(递增+TTL+
  限额,无 TTL 旧键自愈),问答速率闸(登录/公开嵌入)改近似滑动窗口
  (前后窗口加权,窗口切换不再重置完整额度);工具配额仍固定窗口。
  ③向量升级 halfvec(2560)(upgrade-rag-qwen3-halfvec-2560):四类向量列
  (表说明/历史问句/文档子块/用户记忆)迁移 0058 切 halfvec(2560)+HNSW
  halfvec_cosine_ops(旧向量清空、原文保留);agent embedding 切
  qwen3.7-text-embedding 显式 dimensions=2560 并校验返回维度;emb 缓存
  前缀 bump v2;SQL cast 全量 halfvec;重建命令
  `apps/api/scripts/rebuild_embeddings.py`(可观测/按批提交/可重跑/
  --dry-run/按表筛选)。**重建已完成**:dev 库四类存量 56 行(表说明 51+
  文档子块 5)全部经 agent→百炼直连重建写回,库内实测 2560 维;冒烟经 agent
  端点实证 qwen3.7-text-embedding+dimensions=2560 精确返回 2560 维。评估:
  smoke 7/7、full 1/1(阈值过)、multiturn 11/12(1 例为历史基线已记录的
  LLM 多轮生成质量问题,优于基线 10/12)。凭证口径:根 .env 的
  KM_AGENT_LLM_API_KEY 即 DASHSCOPE_API_KEY 本身(直连百炼,不经 muniu);
  qwen3.7-text-embedding 默认返回 1024 维,必须显式传 dimensions=2560
  (agent 代码已落实,设计 D1 实证)。muniu 网关(独立仓,本地 8787)已重建到
  HEAD 二进制并补 4 条 embedding 路由(qwen3.7-text-embedding/-flash 经网关
  实测通;qwen3-vl-embedding/tongyi-embedding-vision-flash 仅百炼原生
  multimodal 端点支持,muniu 数据面无该端点透传,待其侧代码变更);新模型
  定价未配(计量记 token 成本 0,可经管理面运行时补)。四变更已 openspec
  归档(archive/2026-10-20-*),主 spec 同步完毕。

## 当前状态(2026-10-19)

- **全栈刷新现态(stack-refresh-2026-10,已落地)**:四批版本刷新行为保持。
  后端=fastapi 0.144/pydantic 2.14/SA 2.1(`[asyncio]` extra 窗 `>=2.1,<3`,
  弃用面 grep 零命中)/openai 3.35/anthropic 1.15/langgraph 1.2.16/pytest 9/
  mypy 2.4/ruff 0.17;mypy 2.x 下 strict 全量绿。**下限对齐纪律生效**:双 app
  pyproject 下限提到实锁 major 线+窗式约束(langgraph `>=1.2,<2`、openai
  `>=3,<4` 等),新环境重解析不再回退旧 major。前端=Vite 8.3(Rolldown,
  `rollupOptions`→`rolldownOptions`,manualChunks 函数形态尚可用已弃用、
  066 分包边界由 echarts-boundary 静态护栏钉住)+vitest 5+eslint 10(新规则
  preserve-caught-error 补一处 cause)+RR 8.4+echarts 6.1(**echarts-for-react
  必须走 `echarts-for-react/esm/core`——Vite8 CJS 互操作统一后 lib/ 无
  __esModule 默认导入不解包,extends 即炸**,echarts.ts 已修)+React Compiler
  1.0(plugin-react `compiler: true`,依赖 oxc-transform-react;告警零条,
  手写 memo 保留叠加)。基础设施=compose pgvector:pg18(开发机 localenv 已
  pg18)+容器镜像 python3.14-bookworm-slim(requires-python 保持 >=3.13 兼容
  声明;ctranslate2 4.8.3 cp314 轮子已验,api/agent 镜像构建+api 容器 healthz smoke 过)。
  冒烟:echarts 亮暗两态真实问答图表走查过(muniu 网关需在位,agent mk-key
  已换签为 walk310)。TypeScript 7 维持暂缓(typescript-eslint #10940)。

- **文档检索精排现态(add-doc-rerank,已落地;工件在
  `openspec/changes/archive/` 对应目录)**:document-rag 检索在 RRF 融合后
  增可选精排级——候选池扩量(`KM_RERANK_POOL_TOPK` 缺省 20)→ rerank 打分 →
  同父多子取最高分 → 截断 top-5 父块;失败(超时/限流/不可用)降级 RRF-only
  不阻断,留痕 `sys_llm_usage`(task=doc_rerank,status 区分,token 记 0);
  引用键与名次无关零改动。**部署级条件注册**:`KM_RERANK_PROVIDER`(siliconflow|
  bailian)+`KM_RERANK_API_KEY`+`KM_RERANK_MODEL` 齐备才生效,未配=精排级
  零存在感;组合残缺 prod 拒启/dev 告警降级,provider 非法值 dev/prod 同拒启。
  **启用配方**:bailian 例 `KM_RERANK_PROVIDER=bailian`+`KM_RERANK_MODEL=
  gte-rerank-v2`(key 复用百炼 sk-,与 LLM 同账号即可)+重启 api;siliconflow
  例 `BAAI/bge-reranker-v2-m3`(bailian 的 gte 系必须走原生端点,
  /compatible-mode 不提供 rerank)。**阈值建议**:缺省 0(不过滤)起步;要收
  紧先经 recall-probe/日志观察 gte-rerank 分数分布(实测可答题 top1 ≈0.26,
  无关文档 <0.08)再定档,阈值误配会诱发知识态频繁空命中(退化为纯数据答)。
  冒烟:tests/test_doc_rerank.py(25 例,适配器经 MockTransport 零真实出站)。
- **MCP 协议栈现态(adopt-mcp-sdk2,已落地)**:双向手写协议退役,收编
  官方 SDK 2.x(`mcp>=2.3,<3`,dev→prod 依赖)。**修订声明值**:平台 /mcp
  端点为无状态形态(2026-07-28 修订;现代客户端 `server/discover` 直连,
  官方客户端 discover 响应 supported_versions=["2026-07-28"]);旧修订
  客户端经 initialize 握手兼容(按客户端所报修订回签,如 2025-03-26 客户端
  收 2025-03-26)——兼容矩阵=SDK KNOWN_PROTOCOL_VERSIONS 全集(2024-11-05
  起五档)。出站=SDK streamable_http_client+ClientSession(modern
  discover→旧修订 initialize 回退;SDK 异常含任务组包装,归一拆包在
  mcp/client.py `_classify_group`)。HTTP 栈=`httpx2`(mcp 2.x 的依赖,
  client.py 直接 import,api pyproject 已显式声明 `>=2,<3`——勿按纯传递
  依赖对待)。**挂载形态**:/mcp=根级 Mount("")+
  闸/鉴权 ASGI 中间件+SDK 无状态 app(Starlette 前缀挂载不匹配裸 /mcp,
  307 面不可接受);session manager 禁止重复 run,每次 lifespan 经
  McpInboundProxy.rebuild() 重建整链,宿主 lifespan MUST enter
  session_manager.run()(挂载态 SDK 内建 lifespan 失效)。openapi 契约
  面:/mcp 退出 OpenAPI(非 FastAPI 路由)。冒烟:tests/test_mcp.py
  (出站组=真实 SDK server 载体)+tests/test_mcp_live.py(双形态真实
  server:stateless/stateful 各三方法面)。
- **MCP 出站运行时熔断现态(add-mcp-breaker,已落地归档,工件在
  `openspec/changes/archive/2026-10-19-add-mcp-breaker/`;行为契约已同步
  mcp-interop spec「出站运行时熔断」Requirement)**:运行时出站(call/
  read_resource)对连续 timeout/conn(缺省 3 次)自动冷却(缺省 60s),
  期内快速失败不出站(新错误分类 `unavailable`,回灌文本含「未出站」),
  冷却期满半开放行单个探测,成功复位失败回冷却;管理面探测(probe/
  get_prompt)结构上不受限(装饰器不拦),确定性错误(protocol/remote/
  unsupported)不计数,发现同步成功显式复位。落点=`kongming/mcp/
  breaker.py`(McpBreaker 纯状态机+BreakerMcpCaller 端口装饰器+
  build_mcp_caller 单点工厂),四个组合根(mcp/bot/chat/toolcall router)
  统一经工厂装配,进程级单例(`get_mcp_breaker`,内存态重启即清);
  计数维度=endpoint(端口契约零变更)。配置 KM_MCP_BREAKER_ENABLED/
  THRESHOLD/COOLDOWN_SEC(缺省 true/3/60;false=逐字旧行为)。冒烟:
  tests/test_mcp_breaker.py(18 例,fake clock+脚本 stub 不触单例;日志
  断言经模块级 log 替身——本仓 pytest 日志基建 INFO 级不可测;半开探测
  的取消收口经 cancel_probe 幂等回滚,防探测位挂死)。

- **图级检查点与图内挂起现态(add-graph-checkpoint,已落地归档,工件在
  `openspec/changes/archive/2026-10-18-add-graph-checkpoint/`;行为契约已同步
  `openspec/specs/graph-checkpoint/spec.md`)**:任务步骤受限循环获得超步粒度
  恢复与图内 HITL 第三形态。要点:检查点 blob 对控制面不透明(agent
  `graph/checkpoints.py` InternalCheckpointSaver 经 `/internal/task/graph-checkpoint*`
  回调读写;控制面与图框架解耦,换运行时只换适配器);线程确定性派生
  `task:{task_id}:step:{step_id}`(api 不感知图语义,恢复由 agent 自查快照);
  图内挂起=react 图 `confirm_gate`(interrupt)节点,任务通道 confirm_required
  时挂图,批准/拒绝经任务端点 `POST /tasks/{id}/confirms/{confirm_id}` →
  重派 → begin_step 携 resume_confirm → `Command(resume)` 从挂起点精确恢复,
  动作兑现走 `/internal/tool/resolve-confirm`(与 chat confirm() 镜像复校序,
  刻意不复用不复写——安全面两侧复校序变更须同改);恢复方式(fresh/snapshot)
  落步骤 checkpoint.resume_origin 如实呈现;任务终态钩子清理全部快照。
  **安全闸(评审循环补齐,勿拆)**:`_run_react_task` 的恢复提示复合校验——
  提示 confirm_id 必须与快照在途确认一致才 `Command(resume)`,失配/判读失败
  一律按无决策处理走快照续跑原地重挂(同确认行幂等复用);同一线程先后挂起
  多个确认时,旧决策不得套到新在途确认上。resolve-confirm 幂等路径反查回填
  首次执行结果(与 confirm() R1-S1 同口径),缺结果会诱发模型重复发起同一动作。
  协作编排(圆桌/分工)暂未接入检查点(后续独立立项,非外部触发条件);
  交互问数通道行为逐字不变。冒烟:api tests/test_task_graph_checkpoint.py +
  agent tests/test_graph_checkpoint.py + tests/test_task_gen_graph_suspend.py。
  本地环境注记:localenv redis 带密码(`redis://:redis123@…`,见
  ~/workspace/localenv/.env),api/tests/conftest.py 与 apps 双 .env 已对齐;
  podman machine 需先 start 再起 localenv-postgres/redis。

- **collab-worker-roster 分工名册现态(已落地归档,工件在
  `openspec/changes/archive/2026-10-15-collab-worker-roster/`;delta 已同步
  主 specs,strict 单域 valid,openapi 零漂移)**:分工形态(091)拓扑可选
  `workers` 名册(条目 `{name?, bot_key}`,身份=name 缺省 bot_key)——
  Bot 消费第四形态闭环(对话嵌入/OpenAI 兼容 API/分工 worker/圆桌参与者)。
  口径:声明面 workers 与顶层 bot_key 互斥(409,判原始载荷)、条目 bot
  存在性 422 INVALID_BOT(圆桌同款)、归一截断+响应回显、空/全非法名册=
  未声明不落键(存量零漂移);派发面纯名册分配制(编排器把子目标分配给
  成员清单,名册外/缺分配进未派发披露、拆解失败诚实空收口,均不回落
  合成 worker);执行面逐成员人格注入+工具求交(∩任务授权),绑定解析
  失败/零工具/超执行面双钳 fail-closed 剔除必披露,全员剔除不开工;核实/
  critic 重派=治理动作无绑定不占名册(D4)。**无名册零漂移含幻觉字段**:
  拆解输出混入的 worker 字段不成为槽位/结论标签(评审修复,回归测试钉住)。
  分工名册运行期解析失败=决策降级 None+执行面剔除(091 人格侧口径,与
  圆桌「声明即剔除」的分叉见 2026-10-14 节)。冒烟:
  `apps/api/scripts/e2e_roster_smoke.py`(验收单元=run 收口,同 115 口径;
  api:8000+agent:8001+smoke088/sakila)。
- **SSRF 同步解析测试已修(2026-10-15)**:
  `test_ssrf_sync_host_allowed_check` 解析失败分支改 monkeypatch
  `getaddrinfo` 抛 gaierror(不触真实 DNS)——本机 DNS 通配劫持 `.invalid`
  域名的环境假红已消除,本机/CI 恒绿;教训留存:涉及 NXDOMAIN 解析失败
  分支的测试一律模拟解析,勿依赖真实 DNS。

## 当前状态(2026-10-14)

- **115 圆桌对等拓扑现态(2026-10-14 已落地归档,工件在
  `openspec/changes/archive/2026-10-14-115-roundtable-swarm/`)**:collab 域第二形态
  (`collab_topology.kind=roundtable`,
  缺省 decomposition,存量行零漂移)——缺省名册 analyst+skeptic(2 人轻形态,
  2026-10-14 实测收轻;auditor 可显式声明 opt-in,
  2..KM_COLLAB_MAX_WORKERS 人,声明期锁定),轮次 ≤KM_COLLAB_MAX_ROUNDS
  (缺省 3,api 钳制+agent 兜底双口径)。黑板=追加式 turn 账
  (`sys_agent_collab_turn`,0054;唯一键 run+round+worker_no,主席判定恒 0 号位,
  无 UPDATE 路径);终止三通道=主席共识判定(每轮 ≤1 次,畸形按未共识+披露)/
  轮次硬顶/执行预算池(=名册×轮次,发言查证同池)+全员弃权提前收口。
  诚实收口:PASS 需全体实质发言带真实执行证据(纯文本对辩=ERROR,090 闸投影),
  共识不洗白执行失败,未共识 dissent=true 分歧如实保留;参与者=受限 ReAct
  (Bot 绑定或内置章程;工具=任务授权∩Bot 名单;**零工具、绑定解析失败、
  超执行面部署上限(agent 侧 KM_AGENT_COLLAB_MAX_WORKERS 双钳)一律
  fail-closed 剔除并披露,不放宽为任务全量**——授权侧不沿用 091 的
  「解析失败降级为无绑定」口径,那是人格侧口径)。「每轮完成」事件按
  **实际派发最大序号**判定(有效名册经剔除压缩编号,拓扑名册长不可作依据)。
  judge 增 RoundtableRubric v1(共识质量/分歧诚实/证据接地/收敛效率)+
  roundtable 对象类型(0055)。**取数口径(115 决策)**:按运行终态取
  (DONE=共识收口/FAILED=未共识·无证据的诚实收口/PARTIAL=未开桌诚实失败,
  零转写 run 经 turns 非空闸剔除不评),**不要求宿主任务 DONE**——
  评审单元是 run(转写自含),任务后续步骤被预算饿死与对辩质量无关;
  与 092「DONE 任务」口径有意分叉(task/collab 交付物=步骤产出,圆桌=
  收口转写)。基线落库=条件触发:评估账号名下出现首个收口圆桌 run 时由
  定时门禁自然落,不人工硬凑(圆桌诚实收口语义下 DONE 非必然,伪造共识
  凑基线=作假)。
  冒烟:`apps/api/scripts/e2e_roundtable_smoke.py`(api:8000+agent:8001+
  smoke088/sakila;断言口径=圆桌运行终态+账本诚实,任务因步级 token 预算
  PAUSED 属 090 语义非冒烟失败)。**成本口径**:3 参与者 ×2 轮 ≈80-100k
  token,顶满 agent 编排 100k 累计硬顶(task_gen _MAX_TOTAL_TOKENS)——
  冒烟/judge 载体用 2 人 ×≤2 轮(≈40-60k)留余量。契约/主 specs 已同步
  (strict 单域 valid),openapi 重导出零漂移。
- **judge 评估账号坑(2026-10-14 实证)**:judge 账号必须 (a) 绑定租户
  (sys_user_tenant 缺行 → 取数全 403 TENANT_FORBIDDEN,被 gather
  return_exceptions 吞成「类型空缺」——排查先看 api 日志的
  「judge 对象取数失败」warning);(b) **对象归属该账号**——/tasks 列表
  按归属用户过滤,别的账号跑的圆桌对 judge 不可见(SUPER_ADMIN 角色权限
  ≠ 数据可见面,与 datascope 同哲学)。登录连败触发账号锁定(13 分钟),
  建多账号勿重试同一账号。现役评估账号 eval116(自建,已绑 default 租户)。
- **环境口径勘误(本机现态核对 2026-10-14)**:pg/redis 实为
  `~/workspace/env` 栈(env-postgres/env-redis,**redis 免密**,与本文顶节
  「localenv 带密码」口径不符——conftest 缺省即正确,跑测试无需注入
  KM_REDIS_URL);kongming 角色本无 SUPERUSER,已按文档口径升齐
  (`ALTER ROLE kongming SUPERUSER`,migrated_tmp_db 一次性演练库建 vector
  扩展需要)。apps/api/.env 与 apps/agent/.env 当前**不存在**(顶节 2026-10-11
  「均存在」口径失效)——本地起服务用根 .env 选择性注入:api 取
  KM_INTERNAL_TOKEN/KM_DATASOURCE_ENCRYPT_KEY/KM_ADMIN_*;agent 取
  KM_AGENT_LLM_*+KM_INTERNAL_TOKEN(改写为 KM_AGENT_INTERNAL_TOKEN 前缀),
  **不得整份 source 根 .env**(KM_DB_PASSWORD 触发 agent fail-closed 拒启)。

## 当前状态(2026-10-12)

- **全仓评审循环收尾(2026-10-12)**:本会话两轮评审确认 22+3 项缺陷全部
  修复,门禁全绿零挂账。高影响三件:**全仓 httpx 出站统一 `trust_env=False`**
  (系统代理会架空 SSRF 钉扎/劫持内网回调,新增出站点必须带上);**stt 推理
  创建+消费同线程**(faster-whisper generator 惰性,消费留事件循环=全进程
  阻塞);**迁移 roundtrip 测试全部改 `migrated_tmp_db` 一次性临时库**(共享
  库 dropped 列占位撞 1600 硬限事故,见环境事实 13)。其余:store 并发上架
  原子化/回滚零工具闸、trigger 邮件游标收尾提交/派发取消竞态补记不续链/
  IMAP 中断归类、工具网关三域真流式字节闸+DecodingError 收敛、test_call
  凭证 key 一致闸、web 录音代际守卫/画布 nokey(含 portal)等——细节以
  git log 与测试名(「评审修复/评审循环回归」注释)为准。
- **099 HTTP 连接器域(租户级,首个租户级域)现态**:连接器=租户库表行
  (`tool_http_connectors`,迁移 0051),注入集**按请求按租户动态装配**
  (注册/编辑/启停即时生效,不要求重启);管理面 `/system/connectors/*`
  (挂 `connector:manage`,web 管理台→连接器),调用权限 `tool:http:call`
  (两点均显式授予制,不进 DEVELOPER 推荐集,SUPER_ADMIN 种子自动覆盖);
  域配额独立分桶(`KM_TOOLGATEWAY_HTTP_QUOTA`,缺 20/分,域标签=http);
  凭证加密落库复用 008 AES-GCM 单点(webhook secret 同机制),任何接口只出
  「有无+尾号掩码」,明文仅在出站装配瞬时存在;写语义缺省规则=非 GET 需确认
  (确认流复用既有写工具闸)/注册可显式覆盖。**出站安全双落点**:注册期校验
  白名单主机形态+模板主机命中白名单;执行期网关出口校验+029 SSRF 拒绝段
  (`workflow/http_runner.py::assert_url_allowed`,回环/私网/链路本地/云元数据
  一律拒,**白名单命中不构成豁免**),重定向不跟随。工具名=`http__<key>`
  (下划线口径与 MCP 同构,LLM 函数名字符集兼容)。
  **冒烟配方(需运行栈+LLM;注意 SSRF 拒绝段使 127.0.0.1/内网地址不可做
  真实出站目标——刻意的,本地 mock 做不了这一步,集成测试是靠放行缝覆盖
  治理面语义的,见 `tests/test_http_connector.py::allow_local_ssrf`)**:
  1. 注册:持 `connector:manage` 账号进 管理台→连接器→注册连接器;方法 GET、
     路径模板 `https://httpbin.org/anything/order-{order_id}`(公网 echo,免自建
     mock;离线环境可换任一 https echo 端点)、参数 `order_id`(字符串,必填)、
     白名单 `httpbin.org`、鉴权 bearer+任意材料(凭证核验用)、超时缺省;
  2. 出站:授予 `tool:http:call` 后问「查一下 order-42」→ 注入集含
     `http__orders_api`,回调执行,观察=httpbin 回显 JSON 的文本形态
     (大响应截断标注);测试调用入口(列表行内/表单内)可先于问答验证
     (显式确认后真实出站一次,展示状态码/摘要/耗时/失败分类,落审计
     channel=test_call);
  3. 白名单外拒绝:编辑把白名单改成 `good.example.com`(模板主机随之偏离)
     → 再问 → 拒不出站,审计 error_reason 前缀 `policy:`;直接把模板主机
     带外改指内网(如 `http://10.0.0.1/…`)→ SSRF 段拒绝同样 `policy:`
     (白名单改回含该内网主机也不豁免——存量行兜底语义);
  4. 凭证掩码核验:列表/详情/创建/更新响应只有 `****尾号` 形态;
     `SELECT auth_secret_enc FROM public.tool_http_connectors` 为密文;
  5. 审计核验:`SELECT tool_name,status,domain,error_reason FROM
     public.sys_tool_audit WHERE domain='http' ORDER BY created_at DESC LIMIT 8;`
     (失败分类前缀 unreachable/protocol/policy;无 LLM 用量行)。
- **本机代理截流回环的测试假败坑(2026-10-11 实证)**:本机若开代理/增强模式,
  对**无监听**的 127.0.0.1 端口的连接会被代理截流并回 HTTP 502(而非拒连)——
  「关端口=unreachable」前提的用例随之假败(与代码无关;出站类 curl 需
  `--noproxy '*'` 同源)。全量套件见此类失败先查代理再查代码。
- **ticket 工单域与 Siku 知识库接入已退役(remove-ticket-domain /
  remove-siku-integration,2026-10-20 落地)**:`ticket_search`/`ticket_get`/
  `ticket_create` 三工具与 `knowledge_search` 工具、`KM_TICKET_*`/`KM_SIKU_*`
  配置、`tool:ticket:*`/`tool:knowledge:search` 权限点(迁移 0057 精确删除
  两项 ticket 权限及绑定,历史 sys_tool_audit 保留)均已移除;遗留工具名
  直接调用与存量待确认 ticket_create 均 fail-closed 拒绝且零出站
  (tests/test_toolcall.py 退役回归)。需要工单/外部知识访问的部署改走租户级
  通用 HTTP 连接器(099,显式配置端点/凭证/出口白名单并单独授权)。历史决策
  与审计说明见 openspec/changes/archive 对应工件。
- **110–113 批现态(2026-10-12,四变更全部落地归档)**:
  ①110 指标可见性:metric_definitions 增 visible_scope(ALL/DEPT)+dept_id
  (迁移 0053),注入面按调用者 data_scope 收窄,管理面保持全量;
  ②112 全文路查询语义=「AND 优先、内容词兜底」(兜底轮 length≥2 词条
  EXISTS,单字虚词被谓词排除;098「查询客户」场景自此真正兑现;111 无差别
  形态曾被评估事故误判否决,勘误见 evals/README 基线表);③113 MCP 客户端
  真实集成验证:官方 mcp SDK(**dev 依赖,钉 <2**)起本地真实 server 走
  probe→发现→调用闭环,暴露并修复两处协议缺口——streamable-http 会话头
  (`Mcp-Session-Id`)按 endpoint 透传 + 调用面懒握手(按 endpoint 加锁),
  对无状态 server 零影响;④评估栈两个假跌源已修:日配额耗尽(评估 api
  启动必带 `KM_CHAT_QUOTA_DAILY_CALLS=0`)与 hex id 题租户推断 403
  (sakila.yaml 显式 tenant)——历史基线跨轮对比以逐题失败名单为准。
- **114 语音转写(/chat/stt)现态与启用配方**:本地 faster-whisper(api 面
  运行,零凭证不触 ADR-002);**可选启用形态**——依赖随 main 安装,模型
  在位即用,缺失时端点 503 明示(无开关,状态自明)。预下载(一次性,
  ~480MB):`cd apps/api && uv run python -c "from faster_whisper import
  WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"`
  (HuggingFace 直连;失败配 `HF_ENDPOINT=https://hf-mirror.com`)。
  模型规格经 `KM_STT_MODEL` 换(base/medium 同接口)。注意:faster-whisper
  自带的 decode_audio 与 av>=15 不兼容(参数已移除),stt/engine.py 内置
  av 解码替代,**勿改回**。前端:输入框麦克风按钮→录音→转写文本填入
  输入框(用户确认后发送,Escape 取消)。
- **094 web 检索域(联网搜索)现状**:web_search 工具 = 部署级条件注册,
  `KM_WEBSEARCH_PROVIDER`(tavily|bocha|zhipu|alibaba_ai_search)+
  `KM_WEBSEARCH_API_KEY` 齐备才注册;zhipu=智谱独立检索 API(与 LLM 同平台,复用 bigmodel key 零额外
  注册;search_query 方言硬限 70 字符在装配层截断,域契约 500 不动);
  均缺省=域零感知(合法形态);组合残缺(供应商缺 key、仅配 key 缺供应商、
  供应商非法值):prod 拒启(fail_closed)、dev 启动告警降级。权限
  `tool:websearch:search` 显式授予制(不进 DEVELOPER 推荐集)。
  供应商端点与出口白名单在 `toolgateway/models.py::WEBSEARCH_PROVIDERS`
  代码单源派生(部署不可改端点);新供应商接入=该表加一条 + `websearch.py::
  _ADAPTERS` 加一条映射,**接入前核对供应商参数硬限与响应字段名并在方言
  装配层消化**(zhipu 70 截断即先例;域级 schema 与策略器共引 models 常量,
  勿为单供应商改域契约)。参数边界(500/1–10/缺省 5)同文件常量单源。
  观察=检索卡 title/url/snippet(追踪参数剥离、评分不透),域配额按域独立
  分桶(`KM_TOOLGATEWAY_WEBSEARCH_QUOTA`,缺 20/分)。演示需真实检索
  供应商 key,无 key 时其余能力零影响;zhipu web_search 按**次独立计费**
  (不同于 chat 额度),演示前确认 key 已开通该工具。
  (评审循环登记的两条范围外待办已清:knowledge.py DecodingError 收敛与
  前缀收编落地;test_config_failclosed 已钉空 websearch 字段。)
  **检索供应商调查结论(2026-10-11 契约核对;2026-10-14 全五路实测收口)**:
  主流平台开放「独立检索 REST API」的是少数——智谱已收编;zhipu/bocha/
  tavily 均为固定公网端点+单 key。阿里云 AI 搜索开放平台(归 OpenSearch
  品牌线):**2026-10-14 owner 提供实例域名与 `OS-` key,注册表已回填真实
  host 并经网关实调验证(executed,5 检索卡;真链路=网关白名单+SSRF 段+审计
  test_call 通道)**——host 为账号级实例域名,多账号部署需将 endpoint/host
  升级为部署可配(当前单账号按固定端点同列);凭证形态 `OS-` 独立 key 与
  百炼 `sk-` 不通用。DeepSeek(官方
  文档零 search 端点)、Kimi(仅会话内「内置联网搜索」工具)、豆包/火山方舟
  (WebSearch 仅为会话请求配置对象)均无独立检索 API,不可收编——反证网关
  域+多供应商适配是正解,检索能力不绑模型选型。

- **中文全文检索分词(098)现态与启用配方**:全文路分词配置部署级单值
  `KM_RAG_FULLTEXT_TS_CONFIG`(白名单 `simple|zhparser`,缺省 simple=零回归;
  白名单外值与目标库 pg_ts_config 缺失一律 fail-closed 拒启,dev/prod 同、
  无降级形态——降级=换词法,与存量 tsv 错配)。rag 表说明路与文档全文面
  写读同源;ctx 缓存键含配置值(换配置天然隔离,无需清缓存);代码单点
  `kongming/config.py::FULLTEXT_TS_CONFIGS`(白名单放 config 层不落 rag 域包:
  auth 域经 auth.ports→config 的既有依赖链不得反向触达 kongming.rag,
  import-linter 契约)。**启用配方(2026-10-12 localenv 实测跑通;PGDG **无**
  zhparser 包——apt 路线不存在,走容器内源码编译)**:
  1. 容器内源码编译(依赖=build-essential/git/automake/libtool/pkg-config/
     binutils/bzip2/postgresql-server-dev-18,apt 可装):
     ①SCWS:git 协议在本机网络被重置,**用 codeload tarball**——`wget -O
     scws.tar.gz https://github.com/hightman/scws/archive/refs/tags/1.2.3.tar.gz`
     (xunsearch.com 官网不可达,勿试);解包后仓库无 configure,须 autotools
     引导:`touch README; aclocal; autoconf; autoheader; libtoolize; automake
     --add-missing` → `./configure && make install && ldconfig`;
     ②zhparser:`wget -O zhparser.tar.gz https://github.com/amutu/zhparser/
     archive/refs/heads/master.tar.gz`,`PATH=/usr/lib/postgresql/18/bin:$PATH
     make && make install`(dict.utf8.xdb 由 make install 落 tsearch_data,
     缺则手补);
     ③`podman restart localenv-postgres`(.so 重启才装载);
  2. 控制面库各建**扩展+检索配置两件**(扩展只建 parser,配置不自带——
     漏第二件=启动自检过但 to_tsvector 报 config 不存在):开发库 kongming
     与测试库 kongming_test 各执行 `CREATE EXTENSION zhparser;` +
     `CREATE TEXT SEARCH CONFIGURATION zhparser (PARSER = zhparser);` +
     `ALTER TEXT SEARCH CONFIGURATION zhparser ADD MAPPING FOR n,v,a,i,e,l
     WITH simple;`;
  3. `export KM_RAG_FULLTEXT_TS_CONFIG=zhparser` → 重启 api(启动自检过才起,
     自检=查 pg_ts_config,缺失即拒并指回本配方);
  4. **逐数据源重扫/重建重算存量 tsv**(fetch-metadata / reindex / 文档重新
     上传)——存量 tsv 是旧分词形态,不重扫=该路检索静默错配;
  5. **复标全文路阈值**:`KM_RAG_GATE_FULLTEXT_TOP1/MEAN`(064;现恒裁剪
     形态,改值即生效、无 shadow/primary 切换步)——zhparser 改变 ts_rank
     量纲,先经 `/system/rag/recall-probe` 试跑采新量纲分布再定档。
     **查询语义现态(112,2026-10-12)**:召回=「AND 优先、内容词兜底」——
     问句切词条后先全词条 AND;零命中落**内容词兜底轮**(length≥2 词条
     EXISTS,单字虚词词条被谓词排除)。098 验收场景「查询客户→『客户』
     词条命中」自 112 起真正兑现(此前 AND-only 下结构性不成立)。
     **111 勘误**:无差别 OR 兜底曾实施并「实证否决回退」——该否决经查为
     **评估栈日配额耗尽事故误判**(非召回质量信号,逐题 429/403 在案,
     见 evals/README 勘误行);无差别形态仍被 112 规范排除(噪声面无界),
     内容词形态为 spec 认可的唯一兜底形态。历史「AND 语义压制」表述作废。
  回退:`KM_RAG_FULLTEXT_TS_CONFIG=simple` + 重启 + 重扫/重建(零 schema 依赖)。

- **本机评测演示环境(2026-10-12 已重灌现态)**:`infra/example-data/load.sh`
  对 localenv-postgres 实跑通过(幂等可重放)——tenant_sakila 28 对象/
  tenant_olist 9 表/sakila_ext 独立库 28 对象,public 零泄漏;sakila/olist
  租户已注册并绑 admin。三源知识已灌且全量向量化(sakila 22/olist 10/
  sakila_dyn 21 条目;reindex 自动润色)。tenant_default.goods 负例域表
  已建(051 语义)。动态源 sakila_dyn 挂 default 租户(sys_datasource 已注
  册,凭证=api 同款 kongming 角色)。**default 路径自动路由
  tenant_{tenant_key} schema,租户数据源无需注册**(reserved 保留字)。
  评测口径:LLM key 仅 agent 面持有(ADR-002;api 经 /internal/embedding
  回调),向量路生效前提=8001/18001 agent 进程在位 + muniu 桥(8787)在跑
  (key 失效用 `./bin/muniu keys create --tenant default --name <名>` 重签并
  更新 apps/agent/.env);评估栈配方(18000/18001+互联覆盖+一次性超管账号
  +两轮间清 query_history 与 km:ragctx:*)见会话记忆 dev-machine-env-facts。
  **评估环境坑(2026-10-12 实证)**:①`chat_quota_daily_calls` 缺省 500 按
  租户日累计,一天多轮评估必在中后段撞 429 CHAT_QUOTA_EXCEEDED(分数假跌)
  ——评估 api 启动必带 `KM_CHAT_QUOTA_DAILY_CALLS=0`(0=不限);②逐题
  异常(429/403)会让「失败题」混入总分,**任何轮次判定前先 grep 逐题日志
  的「异常」行**,总分下降≠质量劣化。
  上游 dump 依赖 DISABLE TRIGGER ALL 乱序装载,**数据段必须单事务+超管跑**
  (非超管必拒);脚本含业务 schema→kongming 只读授权步(缺授权 api 侧权限拒)。
- **评测基线现值(2026-10-12,112 后)**:zhparser 档(现役档)**宽松
  50/53 (94%)/严格 34/53/表命中 47/53——历史新高**(112 内容词兜底+
  evals 基建修复后 B'' 轮;失败 3 题全确定性 FILTER 归因)。simple 缺省档
  基线=48/32/44(重灌日)。**历史基线可比性警示**:2026-10-12 各轮均含
  2 道 hex id 题恒 403(已修)且 A 轮后日配额递减污染(已加配额豁免)——
  跨轮对比以失败名单逐题对拍为准,勿直接比分。基线表=evals/README 单源
  (111 勘误注记在同表);
  olist_016 为已知 LLM 采样波动题,宽松偶发落败。编写坑:
  **表命中判据=末段名子集比较,gold 的 expected_tables 必须裸表名**(schema 归
  gold_sql 承载);空结果 gold 已被 validators 拒(0 行=不可判定题,错误 SQL
  返回空集即伪通过);ORDER BY…LIMIT 须唯一列 tiebreaker(校验器括号感知,
  函数包键/多参逗号均正确判定,字面量内 order by/limit 字样不误伤)。
- **064 召回闸现状(106 已收编三态,恒生效)**:闸门恒以裁剪形态执行——
  无 off/shadow/primary 之分,`KM_RAG_GATE_MODE` 配置已删除(已设该变量的
  部署请显式清理,未知变量虽被配置体系忽略但属误导残留)。向量档标定缺省
  0.30/0.28 成对写入(对拍依据:知识在位可答题 top1 最低 0.683,2.3× 余量
  零误拒);**启用前置已代码化**:零知识数据源闸门自动旁路(has_knowledge
  EXISTS 单点;「没灌知识」≠「低相关」,否则 sakila_dyn 类内省回退数据源
  全量误拒),recall-probe 试跑面同口径(gate_skipped=no_knowledge 如实
  标注;响应无 mode 字段,丢弃计数键=dropped);全文档量纲未标定保持 0
  (该路不裁)。
- **关系感知召回扩展与评估失败归因(2026-10-05 批,变更 095/096——原提案编号
  088/089 与已归档的 088-094 批撞号,归档件与文档引用已重编号)**:rag 召回
  主结果(三路 RRF+047 继承路)后按已声明表关系一跳扩展——复用
  `rag.table_relations` 零 schema 变更,缺省开启(`KM_RAG_EXPAND_ENABLED/MAX`),
  扩展表带 JOIN path 注入、relations_hint 收窄基准=「主召回 ∪ 扩展」;关系写
  路径(introspection upsert/手工增删/级联清理)经窄端口 `RecallContextEvictor`
  失效检索缓存,**缓存键并入扩展开关状态**(否则切关后 TTL 窗口命中含扩展旧
  输出,违背「关停输出逐字一致」)。实证(50 题时代,基线表 2026-10-05 行):
  同指纹 f05c8028 开/关两轮——关=与 08-30 基线逐项一致(零回归),开=严格
  +4pp/表命中 +1;冒烟 sakila 配 customer→rental 后真实问答生成
  `customer INNER JOIN rental` PASS。失败归因=`evals/attribution.py` 九类
  纯函数,full 统计块+失败明细带判据,multiturn 同函数,12 单测进默认门禁;
  **判定序裁决点**:spec 字面序 EXEC 在 REJECT/ABSTAIN 前会把 BLOCKED/NO_DATA
  题(success 恒 false)全吞进 EXEC,实现取「verdict 终态先行」,README 口径
  节已写明。**探针面板已兑现(097,2026-10-12 归档)**:知识库管理页内置只读
  「召回试跑」面板——问句+数据源→三路命中(各路原生分)/闸门判定(verdict+
  被裁命中数)/零知识旁路标注(gate_skipped=no_knowledge)/关系扩展明细
  (补入表/经由关系/扩展后表集合);试跑端点补 response_model 走契约管线,
  零副作用、权限复用 system:rag:manage。
- **召回/评估批编写坑**:`kongming.feedback.models.extract_table_names` 返回
  末段名小写,与 `expected_tables` 比较前先 `.split(".")[-1].lower()` 折叠
  (否则 SELECT 层拆分误判);测「形态切换」类缓存语义禁止中途清缓存(kill
  switch 测试恰在切开关前 flushdb,曾把缓存键缺扩展态的缺陷掩盖);纯函数
  上限先判 `len(out) >= max` 再 append(max=0 仍补 1 条);evals 用
  `KM_EVAL_ADMIN_USERNAME/PASSWORD` 自建账号,不必知道 dev 库 admin 真密码。
- **租户/控制面隔离(三层)**:default 路径知识内省只看 tenant_{key};执行
  scope 白名单按路径分流(default 无 public);liannu 保留名单含 judge_ 前缀+
  report/report_section/report_version——动态源误指平台库按名拒读(最后兜底)。
- **多副本域启停**:sys_tool_domain_state 为运维意图单源——disable/enable 写穿
  +启动装载(lifespan),装载后同步运行时注册表:停用域工具与「域从未存在」
  同形(不入注入集、分发即拒 TOOL_NOT_REGISTERED),不是执行期才被网关拒。
- **progress 租户闸 + 审计 domain**:/internal/report/progress 载荷带 tenant_key,
  写侧按租户过滤(未知/错配租户静默无效,同形 204);审计读面含 domain 列。
- **093 融合链判据单源**:融合道接手与否只认 `state.fused_takes_over`(条件边/
  流式 summary 抑制/fused_answer 前置复核三处共用)——**改接手条件只改谓词
  一处**,别处复制判据会造成数据稿被吞或双稿拼接;fused sources 按过审终稿
  存活标记投影(057 审计剥句后正文与来源列表双向无幽灵)。
- **judge/construct 契约坑**:评审对象装配必须按各端点真实响应模型取数
  (TaskDetailOut 的 goal/status 在 task 嵌套内;CollabRunOut 无顶层
  goal/conclusion,目标经 step_id 映射步骤)——字段错位会让 judge 对近空文本
  失真;LLM 应答解析统一收口 JudgeLlmError(runner 只接它与 JudgeParseError,
  裸异常逃逸会把单对象失败放大成整个 run 中断);维度分档按 rubric「1-N:」
  前缀做范围校验。
- **流式协议覆盖现状**:anthropic 与 responses 条目流式 e2e 均已通(协议层验收:
  ReAct 工具往返+用量归一;responses 于 2026-10-11 经 muniu 桥 `/v1/responses`→
  dashscope chat 补验)。**百炼直连 responses 端点当前不可用于多轮工具**:其
  服务端把 function_call_output 翻成 role:'tool' 后被老校验器拒 400,流式/
  非流式/previous_response_id/换模型四路实证——responses 条目现役上游=muniu 桥,
  直连待百炼修复。kongming responses 出站已修正为显式形态(role 消息带
  `type:"message"`+纯文本 part 数组;官方字符串/省略 type 快捷形态 muniu 入站
  不认,且单测已同步)。
- **selfheal 冒烟配方**:ALTER TABLE tenant_sakila.film RENAME COLUMN
  release_year ⇄ release_year_tmp 后问「各上映年份的影片数量分布」→
  retry_count=2 诚实 FAILED+列类喂回;还原后 PASS。
- **pyright 门禁口径(推送前必查)**:CI 的 pyright=最新版(严格
  reportUnknown*/reportPrivateUsage 系),本地旧版可能解析到旧版本看不见——
  推送前必须 `PYRIGHT_PYTHON_FORCE_VERSION=latest uv run pyright` 且双 app=0;
  **范围=全仓含 tests(2026-10-12 教训:CI 挂于只扫源码包漏掉新测试文件的
  5 个错误,pyright 无参数跑的就是 pyproject 配置范围)**;同理**推送前跑
  `make lint`/`make api-check` 全链**(同日第二课:ruff format --check 是
  独立于 ruff check 的门禁,手搓单项会漏格式化漂移)。
  高频触发:isinstance-dict 收窄须再 cast("dict[str, object]", …);跨模块复用
  一律公开名(或测试/冒烟脚本文件头 `# pyright: reportPrivateUsage=false` 等
  豁免,先例 test_collab_wiring/e2e_collab_smoke);dict 字面量传
  dict[str, object] 形参须显式标注(不变型)。
- **领域纯净契约**:lint-imports 现为 26 kept / 0 broken——领域用例/模型不得
  import kongming.db(含其 re-export 的 uuid7;纯函数单源在 kongming/uuid7.py,
  领域层直接从那里取)。
- **门禁现值**:api 1220/agent 527/liannu 23/web 86;ruff/mypy/prettier 清;
  pyright latest 双 app 0;CI 六 job 绿。
- **环境提示**:演示数据在本机(kongming 库 tenant_sakila/tenant_olist +
  sakila_ext 库);admin 密码 KmSmoke#2026a;评测前清 query_history +
  km:ragctx:*(README 纪律)。



## 当前状态(2026-10-10,092 评测智能体化已归档 + judge/自动构造落地)

- **092 eval-agent 已落地并归档(2026-10-10)**:change 移入
  `openspec/changes/archive/2026-10-10-092-eval-agent/`,deltas 已同步主 specs
  (eval-agent 新增 4 需求;ai-eval「CI smoke 守护」整块替换,保留 065/073
  合并终态备注)。落地面:0043 迁移(judge_run/judge_result 两表,
  (object_type,object_id) 二元组无业务外键)+ kongming/evalj 域包
  (models 纯函数:summarize/compare_to_baseline 基线口径;SqlJudgeRepo)+
  `evals/judge/`(rubrics 四类带 VERSION+指纹 / client=httpx 直连 openai
  兼容端点零新依赖 / objects 四类取数 / runner 逐对象独立收口+重试一次)+
  `evals/test_judge.py`(smoke+judge 双标记,随定时门禁运行)+ `evals/construct/`
  (validators=README 规范操作化 / generate 双连接:知识读控制面库、gold 直连
  业务库 / merge=并入+全量重跑+基线行登记一条命令)。judge 凭证 env
  `KM_EVAL_LLM_API_KEY`(回退 KM_AGENT_LLM_API_KEY),缺失显式失败。
- **真实环境冒烟终值**:judge 门禁实跑两轮——report 型对 3 份真实 088 报告
  (2 BORDERLINE+1 FAIL,摘录有据:首轮空快照报告被如实判 FAIL,数据源指对
  sakila 后边界分显著上移=judge 捕捉真实质量信号);answer 型对 3 条知识/
  融合问句 2/3 PASS;task/collab 本机无 DONE 对象→「类型空缺如实记录不计
  失败」分支实测;基线对比同 run 内即触发(+0.0pp/提升不触红线)。自动构造
  实跑:5 候选生成→gold 直连 sakila 执行+规范校验+语义去重→3 过 2 拦
  (拦因=tiebreaker 违例,规范闸精确命中)→人审圈定 3 条并入 default.yaml
  (已还原:全量层需 sakila/olist 双租户演示数据,本机单租户环境全量评估
  不可达,并入管线 fail-closed 拒绝登记基线——机制正确;复跑前置=多租户
  演示环境,基线行登记逻辑已单测覆盖)。
- **门禁终值**:api 1188(+29)/agent 525/liannu 22/web 86 全绿;
  openspec strict 两变更 spec 通过(当前口径见顶节「pyright 门禁口径」
  与「领域纯净契约」——本节写作时的旧版 pyright/lint-imports 计数已作废)。

## 当前状态(2026-10-10,093 知识侧深化已归档 + 三态意图路由/融合问答落地)

- **093 knowledge-qa 已落地并归档(2026-10-10)**:change 移入
  `openspec/changes/archive/2026-10-10-093-knowledge-qa/`,deltas 已同步主 specs
  (knowledge-qa 新增 3 需求;document-rag「知识分流」与 chat「文档道回答的结构化
  来源」整块替换)。agent 面=route_intent 节点(understand 后检索前,LLM 三态
  data/knowledge/fused+置信;超时 `KM_AGENT_INTENT_ROUTE_TIMEOUT_SEC` 缺省 3s/
  失败/低置信 `KM_AGENT_INTENT_ROUTE_MIN_CONFIDENCE` 缺省 0.7 → 不写 intent 键
  走逐字词表回退,含 047 多轮闸)+ fused_answer 节点(summarize 后条件进入:
  intent=fused ∧ SQL PASS ∧ 有行 ∧ 文档命中;正文过 068 净化+057 数值审计,
  白名单=结果面∪stats∪**下发文档数字**;失败兜底纯数据答,计数
  `snapshot_intent_route_stats()`)。web=stream 标签两行+AssistantCard 融合分支
  (sources 非空时正文角标+来源面板与结果面并存,零新组件)。
- **068 引用键真实链路断线已修(应改尽改,093 期发现)**:api 侧
  /internal/rag/retrieve 068 起每项 doc_hint 纯增 citation_key,但 agent apiclient
  解析只保留 source_ref/content——键在 agent 面恒缺失,legal_keys 恒空 → 净化
  自跳过、sources 投影恒空(单测手造 doc_hints 带键,未覆盖解析点)。修复=
  apiclient 透传 citation_key(缺失=空串,回退 source_ref 语义不变),回归测试
  test_apiclient::test_rag_retrieve_doc_hints_citation_key_passthrough 锁死。
  修复后真实链路知识答的引用角标/来源面板首次真正点亮。
- **冒烟终值(本地栈,dashscope 直连)**:路由用例集 14/14=100%(≥90% 验收线,
  含长尾「退货都要满足什么条件」与多轮数据追问);三态各 2 问——数据 2/2 PASS、
  知识 2/2 PASS(引用键+sources 一一对应)、融合 2 问分别验证「双通道并存」
  (SQL 结果面+正文引用【退货政策-23CA】+sources,亮暗两态 DOM 走查 12/12)
  与「SQL 失败如实弃答」(NO_DATA 终态零文档粉饰,D4 数据为刃)。
- **环境教训**:本地 dev 库 kongming 此前无表知识(rag.table_descriptions=0),
  063 表级预筛对中文问句打分恒零 → 保留 0 表 → 生成侧如实 NO_DATA;起栈冒烟前
  须先 `/system/rag/fetch-metadata` + `/reindex`(本轮回填 sakila 21 表/16 润色)
  并上传业务文档。admin 密码被我重置为 .env 的 KM_ADMIN_INITIAL_PASSWORD 值并走
  了一次 /auth/password/change(现密码=该值,带 web 冒烟改密 KmSmoke#2026a——
  复用前先试后者)。文档库已有「退货政策.md」(sakila/default,可删可复用)。
- **门禁终值**:agent 525(+20)/api 1159/liannu 22/web 86+build 全绿;
  openspec strict=knowledge-qa/document-rag/chat 三域 valid(全库 16 项失败=
  已登记的存量 >500 字符 CLI 漂移假红,零新增);contracts 产物已重生成,
  contracts-check 在未提交树上按设计红(随批提交转绿)。pyright 本地漂移
  27 条全在 091 存量文件(collab_gen/collab_wiring/task_gen/main),与本变更无关。

## 当前状态(2026-10-10,091 已归档 + 审查循环收口 + 四项契约裁决)

- **091 multi-agent-orchestration 已归档(2026-10-10)**:change 移入
  `openspec/changes/archive/2026-10-10-091-multi-agent-orchestration/`,delta 六需求
  已同步主 specs(specs/multi-agent-orchestration/spec.md;「失控防护」预算口径按
  owner 委托裁决修订为次数口径+步级闸兜底后再同步),活动变更清零,任务 15/15。
- **审查循环收口(2026-10-10,两轮全覆盖评审,缺陷清零)**:api 侧=collector 收口
  代际复核读列缺失(兜底收口死路径)、epoch 由「读后判断」改 CAS 写入谓词
  (transition_task_system/transition_step 增 expect_epoch,begin/step-result/finish
  执行侧写入全带,finish CAS 未命中显式 409)、collab-start 补任务-租户归属闸、
  worker 派发改 INSERT...SELECT+FOR UPDATE(运行在途入写,防幽灵账)、
  collab_finish_run 收敛在途 worker 为 SKIPPED(workers_skipped 事件);agent 侧=
  worker 执行预算 2n+1(与 api 序号上限同式,解满额拆解饥饿)、worker 用量回注
  步级 token 账、_react_execution_evidence 证据闸(纯文本直答 PASS 不算数)、
  critic 轮次 continue(N>1 真实生效)、Bot 空名单=空集 fail-closed;web=runs 轮询
  收窄(PLANNING/RUNNING/PAUSED)+isError 呈现+事件 key 稳定化+披露双行化;
  冒烟脚本重写(超时带现场/finally 清场/不假绿)。**终值门禁**:api 1159(+2)/
  agent 505(+3)/liannu 22/web 86 全绿,openspec strict 本变更 spec 通过。
- **091 multi-agent-orchestration 九项接线(2026-10-10)**:
  api 侧=0041 迁移(步骤 collab_topology 列+worker 唯一索引)、拓扑配置面
  (PUT …/collab-topology,PENDING 闸/Bot 校验/部署钳制)、协作账本 internal 四回调
  (collab-start/worker-start/worker-result/collab-finish,CAS+租户过滤+事件流)、
  查询端点(GET …/collab-runs)、cancel 收敛在途协作账、begin_step 决策携带协作载荷
  (拓扑以库内声明为准,Bot 人格/工具子集经 _CollabBotResolver 按 bot 运行时口径解析);
  agent 侧=collab_gen 扩展(critic 有限轮修订/分歧核实重派[同计入预算、不参与交叉判定]/
  parallel 派发/上限参数化)、collab_wiring 组合工厂(开账 fail-closed、worker 人格注入
  +工具求交、逐 worker trace=trace_root-wN、用量归集)、task_gen 协作分支
  (聚合结论驱动步骤终态);web=TaskDetail 协作视图(DataView 双形态)+TasksPage 状态
  筛选+ReportsPage GENERATING 轮询。**部署级上限**:api KM_TASK_BUDGET_MAX_*/
  KM_COLLAB_MAX_WORKERS(创建/声明钳制)+agent KM_AGENT_COLLAB_*(最后防线),
  缺省值表见 docs/tech-stack.md「任务/协作部署级上限」。**e2e 双场景过**
  (apps/api/scripts/e2e_collab_smoke.py):协作全链(task DONE+run DONE+事件全链+
  用量 84tok/worker)与取消收敛(run CANCELLED+worker SKIPPED)。
- **090 真实链路缺陷已修(本次 e2e 首驱暴露,应改尽改)**:react 图终态只有
  `execution`(ExecutionResult)而无 `verdict` 键——090 剧本化测试的假 step_runner
  带 verdict,真实 `_run_react` 不带,导致真实链路下 `_step_outcome` 恒判 FAILED
  (090 走查任务实为播种场景,未真实跑完过)。修复=`task_gen._react_verdict`
  单一定义(兼容假体 verdict 键与真实 execution 形态),步骤终态与协作 worker
  终态共用;collab_wiring worker 返回归一化终态(原始 state 直传会让聚合裁决恒
  UNKNOWN)。修复后同链路任务真实 DONE(修复前全 FAILED 实证)。
- **0042 派发代际(双执行者竞态修复,2026-10-10)**:真实 LLM e2e 暴露 090 结构缺陷——
  pause 不杀在途编排循环(只在下一步 begin_step 生效),resume 立即派发新循环,
  真实 LLM 长步骤把竞态窗口放大到必撞(实证:旧循环在途步回写 409 后把任务 FAILED
  误杀,新循环协作迟到 DONE)。修复=`sys_agent_task.dispatch_epoch`(0042):每次真实
  派发(创建/恢复/审批)前 `_bump_and_schedule` 原子自增并携代数派发;begin_step/step-result/
  finish 三回调代数失配即拦(begin→abort 决策/回写→stale_epoch 不写账/终态→409),
  agent 收 stale_epoch 静默退出(STALE 终态口径),collector 收口前复核代数。
  epoch=None 兼容(测试内联驱动零回归);mock/快步进掩盖不了此闸(剧本化单测 9+505)。
- **契约裁决(2026-10-10,owner 委托裁决已定)**:TaskItem 暴露 version——**否决**
  (version 是服务端乐观锁实现细节,现无客户端消费场景;有真实消费方时随需求立项);
  审计读面 domain 列——**批准**(toolcall 审计行 089 起已落 domain,读面纯增可选字段
  投影,随下一批触及审计面的变更搭批落地 + make contracts);internal progress 租户过滤;
  域启停多副本语义(已随同批 0044 落地:写穿+启动装载,见顶节「多副本域启停」)。**环境(2026-10-10 已解)**:LLM 已切 dashscope 直连
  (owner 确认;根 .env KM_AGENT_LLM_BASE_URL=compatible-mode 端点,API_KEY=本地
  DASHSCOPE_API_KEY,muniu 网关不再依赖);本地起 agent 仍须
  `KM_AGENT_INTERNAL_TOKEN=$KM_INTERNAL_TOKEN`(compose 同款映射,pydantic 前缀
  KM_AGENT_ 读不到裸 KM_INTERNAL_TOKEN);本地全量 .env 不可直接喂 agent
  (KM_DB_PASSWORD 触发 fail-closed 拒启,是设计行为)。

## 当前状态(2026-10-02,CardTitle 落地归档 + spec strict 债清零)

- **CardTitle 标题语义已落地并归档(change `2026-10-02-card-title-heading-semantics`,2026-10-02)**:
  `ui/CardTitle` 增 `as?: "h2"|"h3"|"h4"` 默认 h3 渲染真实标题元素,视觉配方一字节未动(react-dom/server
  守卫 5 例先红后绿钉死,零新依赖、零 jsdom——`.test.ts` 内 createElement 书写,vitest 网不收 tsx);
  三处认证入口(Login/ChangePassword/OAuthCallback)传 `as="h2"`。验证:web-check+vitest 86/86+
  vite build 产物抽验(压缩产物内默认 h3+配方逐字节在)+真 api 18000 浏览器三页 heading 大纲抽查
  (单条页面级 h2、无跳档);delta 已并入主规格 `web-nav` 且单域 strict 通过,归档走 `--skip-specs`。
  跨仓对齐已完成(2026-10-02 聚合层批次三:jinguan sync 逐字节+muniu 手工翻译)。
- **三仓收敛源头改动(2026-10-02,聚合层批次三;owner 插话「三仓视觉/规范对齐」裁决)**:
  ①`ui/input.tsx` 合入木牛登记的日期族 display:block 修复(type∈date/datetime-local/time/month/week
  回退 block 防日历指示器被 flex 排布,余保持 flex);②`ui/badge.tsx` 收编 success/warning 软底
  状态变体(木牛语义态入单源,15% 弱底);③`ui/alert-dialog.tsx` Content 合入木牛登记的移动端
  贴底抽屉形态(窄屏 bottom sheet+安全区内边距,sm+ 居中,size 档与动画口径同 dialog)。
  三处均经 sync 脚本流向 jinguan、muniu 手工翻译,预览复验 0 差异;门禁 vitest 86/86+build 绿,
  登录页 375 浏览器实测 h2 大纲过。
- **spec strict 存量债已清零(change `2026-10-02-spec-strict-wording-debt`,同日归档)**:
  `openspec validate --specs --strict` 自 33/45 → **45/45**。12 域 49 处修正(44 关键词升格+4 新增 Scenario+1 结构规范化)全部是措辞/结构债、
  零行为变更:44 条 Requirement 正文注入 SHALL/MUST/SHALL NOT(ai-eval 5/audit 1/datascope 6/
  datasource 1/eval 2/knowledge 1/multitenant 4/nl2sql 6/rbac 15/web-admin 3),chat 两条需求
  (口径依据/文档道来源)补从正文派生的 Scenario 块,runtime-resilience 把当年 074 以 delta 形态
  (`## ADDED Requirements`)直接躺进主规格目录的文件转回规范形态(`# 能力域`+Purpose+Requirements,
  原文逐字保留)。修正直接手工同步进主规格,change 零 delta(`.openspec.yaml` 声明 `skip_specs`)。
  **新基线**:strict 门禁自此全绿,「12 项失败是既有债」的每轮复述作废;后续只有新 delta 自身
  须过 strict 的既有纪律。超长文本 INFO 级提示多域仍存(不影响判定),按冻结裁决未处理。

## 当前状态(2026-10-10,088-090 归档 + 全量审查循环收口)

- **088-090 三变更已归档,091 保持 active(2026-10-10)**:report-generation(088,16/16)/
  tool-gateway(089,11/11)/ task-kernel(090,15/15)归档件 `openspec/changes/archive/2026-10-10-*/`;
  specs 已同步(三个新主规格 + conversation-artifacts MODIFIED),089 主规格带审查期勘误
  「超时分型:连接超时归连接类可重试/读超时归响应类不重试」且 delta 已回写对齐。
  **091 multi-agent-orchestration 6/15 保持 active**:v1 交付形态=未接线库模块(编排循环+
  深度/worker 数/预算三重上限+0040 两表+剧本化测试已交付),持久化/端点/前端/Bot 装配
  九项接线为下一步。
- **全量审查迭代循环(四路独立子代理 + 二轮复审)坐实并修复 15 处**(候选 30+,逐条溯源核验):
  **090 侧**——审批 TTL 清扫绕行(lifespan 直调 repo 漏掉 expire_stale_approvals,生产永不可达)、
  审批期暂停死锁(approve 守卫放宽 PAUSED + begin_step 任务级 CAS 未命中回滚步骤态按现态给决策)、
  拒绝审批不派发(假 RUNNING,批准/拒绝都触发派发)、回调超时 600s<墙钟预算 900s(独立
  agent_task_timeout_sec=960,链路异常收口 INTERRUPTED 可恢复而非谎称 FAILED)、resume 双派发
  (moved 标志仅真实迁移派发+PLANNING 拒绝+同态不回队)、终态任务迟到回写盲记账(封账+
  late_step_result 事件)、cancel 不结步骤(批量关闭+steps_cancelled);**spec 级**——「全部 DONE
  方可 DONE」此前只按「全部终态」实现,api DONE 前置收紧+agent not_done 如实 FAILED(带
  SKIPPED/FAILED 步骤不再全称完成,收口表述逐项附因)。**088 侧**——会话素材 role 小写错配
  静默恒空(按 USER 问/ASSISTANT 答落行配对;测试夹具同改真实形态,随机 v4 id 会抖动配对序)、
  refresh 路径 tenant_key 用原始 header(多租户错 schema,改按租户 id 取权威 key)、GENERATING
  孤儿行启动清扫、章节按 ord 重排插入会拒合法载荷(改按输入序)。**089 侧**——域治理三端点补
  路由级回归(403/404/停用后 internal execute 拒+审计带 domain/启用恢复)、清单 bool 穿透 int
  显式拒绝+非法主机条目剔除、fixture 停用集 finally 还原。**agent 侧**——证据拒绝 422→200
  携带决策(422 被回调客户端归一 None,拒绝原因曾静默丢失、分支不可达)、步骤 goal>2000 确定性
  FAILED(截断对齐控制面上限)、规划非 JSON 裸 500(收敛 FAILED 附因)、任务步骤审计
  channel="task"、collab prompt 上限 f-string 单源、任务级 DONE 收口与 api 同口径。**web 侧**——
  任务取消/审批拒绝补 AlertDialog(宪法 Article 2)、TasksPage/ReportsPage 错误态伪装空态
  (ErrorAlert+isEmpty 收紧+末页越界钳制)、详情页 4 处手写 alert 收编 ErrorAlert、报告删除确认钮
  variant="destructive" 替代手抄样式。
- **登记待办(2026-10-10 已裁决,详见「当前状态(2026-10-10,091 接线完成待归档)」节)**:
  TaskItem 暴露 version(否决)、审计读面 domain 列(批准,搭批落地);任务列表状态筛选、
  预算部署级上限、internal 端点畸形 UUID 裸 500、报告 progress 轮询拉全量章节、列表页
  GENERATING 不轮询——均已在 091 批次落地;域启停多副本语义亦随同批 0044 落地
  (见顶节「多副本域启停」)。
- **门禁终态**:api 1155(+10)/agent 488(+2)/liannu 22/web 86 全绿,双 app ruff/mypy/pyright/
  lint-imports 过(api pyright 曾报 16 项=pyright 版本漂移下既有测试鸭子型注入与类型收窄,
  按 test_task.py 同款 pragma+isinstance 收窄修复,非回归);契约再生成幂等(重新 export 前后
  md5 一致=与代码逐字节同步),contracts-check 末段 git diff 在「产物已更新未提交」态必报差
  属机制性红(本文门禁节既有口径)。**坑**:并发两轮 make check 互踩共享测试库→成片假失败+
  零 CPU 挂死(共享测试库并行 pytest 互锁的又一形态),先清干净进程再单轮跑。
- **090 浏览器走查已补(2026-10-09)**:Playwright+系统 Chrome,冒烟账号 smoke088/Smoke#2026,
  DEV 库播种 walk090a 审批挂起/walk090b 预算暂停:/tasks 与 /tasks/{id} 六组合(375/768/1280×
  亮暗)横滚溢出 0px、亮暗背景翻转、375 卡片/768+ 表格形态、审批按钮 6/6 在场,截图 12 张
  /tmp/taskwalk。**坑**:改已应用的迁移文件后测试库/dev 库须重建或回环(kongming_test 重建须经
  env-postgres 超管装 vector/pgcrypto,kongming 角色无建扩展权)。
- **087 偏好自管理落地并归档(2026-10-02)**:承接 059 任务 3.4 前端遗留 + 后端删除/清空/并发语义,
  归档件 `openspec/changes/archive/2026-10-02-087-personal-preference-management/`。要点:①PUT
  `/memory/prefs` 用 `model_fields_set` 区分「省略=保留」与「显式 null=删除」;归一化后既无
  非空设置也无 null 删除=真无操作(不建行/不增版本/不取消在途提取/不伪造审计);新增同路径
  DELETE 清空全部(保留空行、已空仍递增版本)。②`user_prefs` 增内部列 `manual_revision`
  (0036,BIGINT NOT NULL DEFAULT 0,不进公共契约);人工写(页面 PUT+聊天显式写槽位)走单语句
  UPSERT 短事务合并(行锁内 `(prefs::jsonb||values)-dels`),版本+1;自动提取在问答准备期
  (任何 LLM 调用前)捕获版本快照,贯穿同步 ChatOutcome 内部字段与流式 `_stream_finalize`,
  落库 `ON CONFLICT ... WHERE manual_revision=:rev` 版本一致才**只补缺失槽位**(协方差子查询,
  不覆盖不删除),不一致零行=放弃;版本读取失败=None 整轮跳过(两处调度点均不挂任务)。显式
  聊天写槽位从 `apply_implicit_patch` 切到人工语义。③MemoryPage 增「偏好设置」区(七槽位表单
  +metric_aliases 可编辑列表+单项清除发 null+AlertDialog 全部清空),纯逻辑抽 `lib/prefs-form.ts`
  (11 vitest);查询按租户分区(query key 含 tenant)。**坑**:`user_prefs` 列是 VARCHAR(36)
  而 `user_memory` 是 uuid——同域两种约定,测试 SQL 的 CAST/子查询要按表对齐;旧 JSON 列是
  `json` 不是 `jsonb`,操作符前必须 `::jsonb`。**评审(主对话交叉自查,子代理撞用量限额)修掉
  2 处前端真缺陷**:setQueryData 原在 onSuccess 闭包(取最新渲染 queryKey,在途保存遇切租户会把
  旧语境响应写进新语境缓存)→挪进 mutationFn 闭包固化租户;清空失败原 onError 关掉 AlertDialog
  与「失败保持打开」注释矛盾→保持打开并对话框内显示错误。其后按 owner 指令开启独立评审迭代循环
  复扫全量未提交 diff,另修 2 处文档过时陈述(roadmap「当前提案队列未实施」段随落地移除;
  「下一棒」087 条目撤除重编号),代码零改动、全量门禁沿用本批全绿结论。**门禁**:`KM_REDIS_URL=…6380/15
  make api-check` 全绿(1091 passed,cov 73.22%),agent 459/liannu 22/web 81+build 全绿;
  契约已再生(纯增 DELETE),manual_revision 零泄漏(grep 实证)。**实机冒烟**(真 api 18000+真
  agent 18001+muniu LLM+Chrome headless playwright-core,宿主 18080/18081 已被上游 energy-ai
  占用须避让):查看→编辑(含别名)→保存回显→单项清除(它槽不误伤)→AlertDialog 全部清空→
  两轮真实流式问答后 `GET /memory/prefs` 恒空(Bearer 直连核验——注意直连 api 无 `/api` 前缀,
  前缀是 web 代理层的)+审计轨迹完整;375/768/1280 亮暗走查过(亮色视觉模型两档无缺陷,暗色
  计算样式断言)。冒烟账号 `smoke087`(Smoke#2026,SUPER_ADMIN,可删)。**venv 三处旧路径重建**:
  apps/api、apps/agent、packages/liannu 均为迁移前路径所建(shebang 失效"Failed to spawn"),
  按 HANDOFF 环境事实 10 逐个 rm+uv sync。
- **既有规格门禁**:本轮主规格严格校验 33/45,通过之外的 12 项为 ai-eval/audit/chat/datascope/datasource/eval/
  knowledge/multitenant/nl2sql/rbac/runtime-resilience/web-admin 的 RFC 关键词、Scenario 或 Purpose 问题;
  在本轮提案写入前已存在,本件不代修或把局部通过记为全仓通过。实施归档前仍须满足必要门禁。

- **083 data_scope=CUSTOM 落地并归档(2026-10-01)**:CUSTOM 由「已登记未实现 + 选了必被拒」变为
  「按角色部门集合并集 IN 过滤」(不递归子部门;空集合仍 fail-closed,且判定已提到列探测与
  「主表不可定位→降级」之前,堵掉一条潜在 fail-open)。存储走关联表 `sys_role_dept`(0035,
  复合主键+两侧 FK+dept 索引),契约纯增 `data_scope_dept_ids`。归档件
  `openspec/changes/archive/2026-10-01-083-data-scope-custom/`(§6 记实机走查全过程)。**已实机走查**
  (真 api + 真库 + 一次性账号):空集合被拦并 `role=alert` 报「自定义范围需至少选择一个部门」、勾选落库一行、重开回显、
  改回 SELF 后面板消失且**不发该字段**且库内集合行归零。走查抓出并修掉两条新组件缺陷:部门行高 20px→44px(B1.2 触控热区)、超长部门名截断无 `title` 不可回读(同组件服务既有权限面板,32 行同步受益)。**同批另修一条基线缺陷(084,已归档)**:`--input` 原被抄成 `--border` 同值,控件边界亮色仅 1.24:1、暗色 1.49:1——未勾选的复选框近乎隐身。改为独立取值(亮 `#808c86`/暗 `#62767c`,实测 3.35/3.58:1),jinguan 同值、muniu 按松烟色板另算(`#7c8781`/`#6b7a6f`,实测 3.72:1);`--border` hairline 不动。口径已入 `FRONTEND-CONSTITUTION.md` Article 3(两语义不得同值 + 控件边界 ≥3:1 + 改色板须重算),spec 落 `theme-switch`。**084 的影响面判错,已由 085(已归档)纠偏**:那句「消费方只有 6 个控件」只数了 `border-input`,漏了同一 token 还喂 `bg-input/30`(暗色控件内底)与 `data-[state=unchecked]:bg-input`(Switch 未勾选轨道,**不带 `dark:`,亮色同样生效**,本仓 5 个真实消费方含 status-switch 启用/停用)——结果是未勾选开关轨道被一起提亮成实心灰条,关闭态读起来像开启态。修法是把第三个语义拆出去:`--input-fill`(muniu `--color-input-fill`)取 084 拆分前的原值,11 处填充类改指 `bg-input-fill*`,描边值不动。**教训**:改 token 前判影响面要按**变量名的一切消费类** grep(`bg-input` 与 `border-input` 同源),按语义同名类数会漏;而 `bg-*` 类拼错或 token 未注册时 Tailwind 静默不生成规则,tsc/eslint/stylelint 全都查不出来,接线只能靠 `vite build` 产物 CSS 逐条确认。**留档不改**:亮色聚焦环 `--ring` 对白底 2.31:1,属品牌色决策。
- **083/084 评审迭代循环(2026-10-01,R1→R2 一轮收敛,独立子代理复核报 4 项候选)**:确认真缺陷 2 项、
  登记待裁决 2 项。**修**:①角色表单的部门候选查询失败被渲染成空态「暂无部门,请先到「部门」页新建」
  ——`/system/depts` 挂 `system:user:manage` 而角色页只要求 `system:role:manage`,且任何瞬时失败都会
  把人支去新建;现按 web-nav spec「查询错误态必须复用共享件、不得手写副本」改为 `ErrorAlert` + 并列
  `refetch` 重试按钮(该件只收 `text` 单参,动作件放它外面),并明示
  「已配置集合保持不变(仍可保存)」——不清集合是刻意的:候选读不出来时保留回显值比清空安全,
  清空后服务端会按 083 的空集合 fail-closed 直接拒(不会静默放宽)。②083 主规格判据清单第 10 条
  「判定先于主表不可定位→降级」在 apply 层**没有用例钉住**:原 `test_apply_custom_fail_closed` 用可定位
  主表,把 fail-closed 判定挪到降级分支之后它照样绿;补 `test_apply_custom_empty_rejects_before_union_degrade`
  (UNION + CUSTOM 空集合),红灯实证:挪位后仅这条红(旧用例仍绿),复原后 datascope 39/39、api-check 1066 passed
  且覆盖率 72.85%。**登记待裁决**:禁用角色使可见范围反向放宽(权限点解析不看角色状态,与 datascope 侧
  不同口径)、`sys_dept.status` 在 DEPT/CUSTOM 不过滤而 DEPT_AND_BELOW 过滤——两条都属授权口径变更,
  不由代码评审单方面定,当轮只登记为待裁决(**后续已由 owner 裁定为 086 落地并归档,见下一条**)。**排除的臆断 1 项**:称「部门候选读取失败时仍把陈旧
  deptIds 原样提交」是缺陷——保留回显值才是正确侧(清空会撞上 083 的空集合拒绝)。
- **086 授权状态单一闸落地并归档(2026-10-01)**:上一条登记的两条「待裁决」已由 owner 裁决,归档件
  `openspec/changes/archive/2026-10-01-086-authz-status-single-gate/`。裁定的口径是两句,别读反:
  ①**角色启用状态是一道贯穿「一切按主体取授权面」的闸**,不是 datascope 独有的——本批前这样的读取
  共 7 处,只有 datascope 的 `user_role_scopes` 与 083 起的 `custom_dept_ids` 两处带闸,其余 5 处
  (权限点联查 + 审计/反馈/工具调用/质量四处 `data_scope='ALL'` 判定)都不读 `sys_role.status`,
  禁用一个 ALL
  角色于是同时得到「数据范围侧消失→降级为不过滤」和「可见面侧照旧放宽」两个相反结果,即反向 fail-open。
  现 `auth/repository.py::SqlPermRepo.list_codes`(改为联 `sys_role` 并带 `r.status='ACTIVE'`)、
  `audit/`、`feedback/`、`toolcall/repository.py::user_has_all_scope`、`quality/repository.py::SqlQualityRepo.user_has_scope_all`
  五处同闸;同时把「主体不可确定」统一为 fail-closed——用户不存在/已删除、无启用角色两类在 applier 各
  `log.warning` 一行再抛 `DataScopeUnsupported`,`DataScopeUnsupported` 的 docstring 顺势扩为
  「无法确定行级可见面的统称」(4 种触发形态)。第 5 处(quality)是评审 R2 才扫出来的:R1 按记忆列的
  「三处 ALL 判定」清单不完整,**枚举读取点要按 SQL 片段 grep(`sys_user_role`/`sys_role_permission`
  的联查处),不能靠列清单**。另有一处**已核不缺陷**并登记以免重复排查:`auth/repository.py::list_role_keys`
  不带宽谓词,它是角色名呈现面(鉴权一律走 `perm_codes`,实测无授权用途)——若日后被用来取授权面须同闸。②**`sys_dept.status` 退出行级可见性判定**——删掉 `dept_and_below` CTE 里
  那两处 `status='ACTIVE'`(而不是把 DEPT/CUSTOM 补齐成过滤),裁定的分支理由与实证面记在「挂账终态」节,
  并由 `datascope` 一条 MUST NOT 场景 + `test_dept_and_below_includes_disabled_dept` 钉住。
  用例:`tests/test_role_status_gate.py`(同一角色图上 ACTIVE/DISABLED 各跑通 4 处 ALL 判定,新增按角色
  联查的读取点必须并入)、`test_auth_me.py::test_me_perms_exclude_disabled_role`(`/auth/me` perms 为空
  且 `GET /system/users` 403,即功能面同闸)、`test_datascope.py` 三条(清单 13/14/15:未知主体、仅有
  停用角色、停用部门仍在子树内)。spec 侧 `rbac` 加「未启用角色不授予权限点」场景、`datascope` 加两条
  新需求。**门禁**:`KM_REDIS_URL=redis://localhost:6380/15 uv run pytest -q --cov` 自 086 后重跑过一遍,
  **1071 passed / 6 skipped**,覆盖率是 **72.9% 级**——该套件带 pytest-randomly,三次实测
  72.93/72.94/72.95 微抖,别把它写成到点的断言;`make api-check` 的各段(ruff format/check、mypy、
  pyright、lint-imports、pytest)本批逐段跑过且全绿,其中自查抓到 applier 模块 docstring 一条
  148 字符长行报 E501——重跑门禁才有,注释与文档不作证据;`--specs --strict` 33 通过/12 失败——失败项全是既有中文
  标题缺 RFC2119 关键词那笔债,单查 `datascope` 只剩关键词警告(086 新增段自带 SHALL/MUST NOT,未新增
  失败项);`--changes --strict` 归档后活动件为零。
- **本机跑 api 门禁的端口坑(2026-10-01 复现;2026-10-09 已随环境统一栈退役)**:
  当年宿主 6379 被带密码的上游 redis 占用、6380 是本仓避让口的口径已作废——
  现本地环境单源 `~/workspace/env`(pg 5432 / redis 6379 免密),conftest 缺省
  即正确,跑测试**无需任何 KM_* 注入**(详见「环境事实」2)。
- **两条既有测试陷阱(已修,后来者勿再踩)**:① `tests/test_feedback_promotion.py` 的迁移往返用例
  `downgrade("0033")` 后只 `upgrade("0034")`——head 前移后它会把库永久留在 head 之下,其后每个用例的
  `TRUNCATE` 撞 `UndefinedTableError`(本次实测 518 个 error 的唯一成因),规则是**回 head 不回版本号**;
  ② `tests/test_billing.py` 出账用例的「上月」原取库时钟(UTC),而 `BillingService._prev_month()`
  写死 +8——每月 1 日 00:00~08:00(北京时间)两者跨月错位,断言必红,规则是**测试用产品同一时源**。
- **门禁口径补记**:`make contracts-check` 末段 `git diff --exit-code contracts/openapi.json` 在
  「产物已更新但未提交」状态必报差(该闸按提交后语义设计),本地等价证明是
  「重新 export + 重新 `contracts:gen` 后 web-check 与 tsc 仍全绿」。
- **十二变更落地+053 切主,全部已归档(2026-09-28)**:活动变更清零。
  062 问答成本控制(登录面速率 fail-open+配额 fail-closed 双闸、bot 面
  IP 维度速率闸、用量索引 0031);063 相关表预筛(共现加权打分 top-12,
  实测大 schema prompt -35.4%、Recall@K 6/6);064 召回相关度闸门
  (原生分出 SQL、双条件判据、off 缺省、试跑端点、047 继承路同闸);
  066 前端分包+vitest 架网(入口 104 kB,60 例);068 文档道引用契约
  (确定性引用键+编造净化+ sources 纯增);069 轮次质量观测(四信号列
  0032+/admin/quality);070 纠错 SQL 审核后晋升(0034 来源/准入态,
  同事务晋升,实测四类存量治理);072 agent 出站韧性(双层分类纯函数+
  降级链,mock 实测 429 Retry-After/503);076 口径依据透出;077 用量
  协议归因(0033 protocol 列);078 流式交互(近底跟随/显式停止/有界
  恢复);080 提示词装配预算(分段优先级收敛+可归因终态,估算/实测
  1.30 保守余量)。**053 切主已执行**:两进程 `KM_SQLGUARD_MODE=primary`
  (agent 前缀 `KM_AGENT_`),容器日志实证 mode=primary+liannu 0.1.3+
  启动自检通过,真实问答流量零 ENGINE_FALLBACK(0.1.2→0.1.3 漂移消解)。
- **全量评审循环(两轮)坐实并修复 9 处缺陷**:3.13/3.14 注解求值差异
  掩盖的 TYPE_CHECKING latent bug(feedback 组合根 3.13 直接 NameError,
  新增 AST 防回归测试)、gate 影子观测算了就丢、suffix 引用键与
  sources 双向幽灵(违 chat spec)、prune 输出序与 080 首位豁免/ trace
  不一致、feedback 070 死代码、quality 口径注释与 rated 死列、
  064 继承路未过闸、bot `is True` 字面量比较、auth oauth state 损坏
  JSON 无兜底。Semgrep/ast-grep 377 条辅助发现按类裁断:注入面
  (常量+绑定参数,3 处 f-string 逐个验实质)与 int()/Protocol 存根为
  规则误报,仅 auth state 一处真修复。五门禁全绿(api 1029/agent 459/
  liannu 22/web 60+build/contracts 无漂移)。
- **环境实测(2026-09-28,冒烟栈)**:容器形态 api:18080/agent:18081
  (端口理由见「环境事实」2),LLM 出口=DASHSCOPE_API_KEY(dashscope
  qwen-plus 兼容端点),SQLGuard primary。真实问答全链路(生成→闸门→
  执行→答案卡)浏览器 10/10 走查过(playwright+系统 Chrome,路由经
  `VITE_API_PROXY_TARGET` 真代理);冒烟数据=`kongming_smoke` 库(26 表)
  + smoke 账号(SUPER_ADMIN)+ smoke_ds/smoke_ds2 数据源(可随时清理,
  自明命名)。**注意**:本机 admin 密码非缺省(容器 env 自管),冒烟一律
  自建账号(`_mk` 范式见 tests),勿猜勿改现有凭据。

## 当前状态(2026-09-27)

- **九变更落地+评审循环修复,已归档(2026-09-27)**:060 镜像构建修复
  (多阶段 context=仓库根、`.dockerignore`、CI images 门禁、CMD 三网段
  逗号拼接——原独立 argv 是潜伏缺陷)、061 日志单源(dictConfig 双格式、
  7 模块 structlog→stdlib、X-Trace-Id 经 `kongming.trace` contextvar 与
  chat/用量同值)、065 eval-nightly、067 保留表清单单源(liannu.rules
  五组唯一定义+对象恒等断言+引擎自检清单校验)、071 对话复语直通
  (闭合词表全等判定,零 LLM/检索/执行)、073 prompt 指纹、074 连接池
  弹性+compose healthcheck/restart、075 计价死字段清除、079 ReAct
  纪律段按工具集派生。评审循环坐实 3 处真缺陷并修复:全套件纪律段
  漂移(锚测试自比掩盖)、X-Trace-Id 与 trace_id 不同值、写能力断言
  措辞过粗。四门禁绿(api 958/agent 377/liannu 22/web/contracts)。
  **评估实测**:smoke 6/6、full 1/1、multiturn 10/12(2 例多轮生成质量
  差异记入基线)。`.example-data` 已灌入;sakila_dyn 归属 default 租户。
- **并发写入清理(2026-09-27)**:删重复变更 2 个(doc-answer-citations、
  turn-status-observability)、重编号 3 个(stream→078/react→079/
  prompt-budget→080)、统一 multi-model 与 ai-eval 两处 MODIFIED 整块
  为合并终态(后 sync 不丢改动)。

## 当前状态(2026-09-26)

- **双变更落地+评审循环修复,已归档(2026-09-26)**:`multi-protocol-llm`
  (agent 面三协议出站 openai|anthropic|responses,条目级 protocol 字段,
  anthropic 走官方 SDK 1.8.0——类型面已无 temperature,双头鉴权,翻译层
  收敛 llm_protocol.py,缺省零回归;ADR-008 立法)+ `mobile-experience-redesign`
  (移动优先重设计:BottomNav/管理层进/会话抽屉三件套,DataView 表格/卡片
  双形态标准件收编 17 管理页+门户/记忆,dialog 窄屏全屏化,安全区/触控基线
  壳层单点化;AGENTS.md 响应式条款重写)。评审循环两轮:首轮坐实 2 P0
  (桌面对话框 inset-0 残留压成 50vh 贴底、BottomNav 安全区双计)+5 P1+
  agent 7 项+工件失实 10 处(tanstack 谎报→自研列模型 D3 修订留痕、
  react-markdown 补立法 ADR-009、Non-goals 缺失等)全部修复;二轮专审
  15/15 通过零新增。四门禁绿(api 925/agent 287/web/contracts),
  375/1280 三档浏览器目验过。specs 已同步(multi-model-routing 增 protocol
  语义+「多协议出站适配」;web-nav 应用壳/响应式外壳两条款;新建
  mobile-experience 域)。anthropic/responses 条目的流式问答 LLM 腿已于
  2026-10-11 补验通过(现态单源=顶节「流式协议覆盖现状」;muniu 本地起法见
  muniu 仓 README「快速开始」)。
- **供应商预设目录 + ADR-007 定位修订(2026-09-26,文档级)**:用户裁决 muniu
  降级为可选端点(类 ollama,不强制经网关)→ ADR-007 状态行与文末「修订」节
  留痕,现网 muniu 形态部署不变;`docs/model-providers.md` 官方端点预设目录
  (OpenAI 兼容表 / Anthropic 分支消费路径 / muniu·ollama 条目,与 muniu 仓
  `docs/provider-presets.md` 交叉核对,2026-09-26 版)+ B 节三协议直连口径;
  `.env.example` 模型段改双形态(官方直连缺省示例 + muniu 注释可选)+
  `KM_AGENT_LLM_PROTOCOL` 示例。注意:muniu 仓有并行会话在施工
  (internal/presets + anthropic 入站协议 + 控制台预设页,状态以其仓 git 为准)。
- **056–059 批量落地+评审循环修复,已归档(2026-09-24)**:056 skill-pack
  (垂类内容包四段契约/按数据源启停/合并投影封顶/`skill_` 保留前缀/
  `system:skill:manage`;fewshot SQL 只作提示文本);057 numeric-grounding
  (派生量白名单+确定性审计剥离+compute_delta/compute_share 本地执行,
  `tool:compute`);058 react-todo-honesty(多子目标 Todo 诚实账本,预算
  退出不批量 DONE);059 user-preference-memory(`user_prefs` 闭合槽位,
  隐式只写槽位 patch、显式分流槽位/NOTE、偏好注入带「可忽略/不约束
  数据范围」前缀)。同批评审循环坐实并修复 12 处缺陷(审计剥空回退未审
  原文、asyncpg jsonb 回 str 导致 prefs/skillpack 读空、metrics 重启用
  自冲突、PrefsIn 未知键静默丢弃、时间列 str 排序错序、059 注入/路由
  假完成等),契约已再生。**059 MemoryPage 槽位编辑 UI 仍未开工**(任务
  3.4 显式阻塞,前端待做)。
- **054 gates-env-parity 已归档(2026-09-24)**:CI 补齐 pyright 步骤
  (api/agent 与 make check 同参),`.env.example` 对齐 muniu 网关与
  SQLGUARD 模式开关;「make check × CI」对照表入本文门禁节。CI 实跑绿
  (含 pyright)后归档,「本地绿 CI 假绿」面闭合。
- **053 SQLGuard 已切主并归档(2026-09-28,见顶栏)**:引擎为
  本仓 monorepo 共享包 `packages/liannu`(连弩;Python 单遍,与内置路径同源
  sqlglot AST,现版本 **0.1.3**;两进程 uv path 依赖引入,**2026-09-12 晚由独立仓
  km-sqlguard + vendor wheel 并入,vendor 机制退役**——独立性检验五问
  不占,迁移验证=引擎 21 例全绿+121 例现算与冻结基线逐字节一致)。
  历史影子期证据(2026-09-12,shadow 形态):流式问答「商品总共有多少个」
  全链路 PASS,两进程 `SQLGUARD 管线 mode=shadow…version=0.1.2,启动自检通过`,
  请求期零 ENGINE_FALLBACK 零影子不一致,对拍基线 121 例回放零不一致。
  切主=两进程 export `KM_SQLGUARD_MODE=primary`(agent 前缀
  `KM_AGENT_SQLGUARD_MODE`)+ 重启;**两 apps 无 .env 文件**(find 实证,
  HANDOFF §4 旧记载作废),export 只需改启动环境,无凭证文件可碰。
  本机以 compose override 文件(/tmp,不入库)注入 env 重建容器完成切主。
- **muniu 网关接线完成(ADR-007,2026-09-11)**:agent 面 LLM 出口已全部指向
  muniu 网关(127.0.0.1:8787;主模型/embedding/vision 注册表 `.env` 三处切换),
  直连冒烟、e2e 问答(verdict PASS)、网关计量与计价实证全过;供应商真实 key
  已收敛到 muniu 侧。**灰度一周进行中(自 2026-09-11 起计时)**:每日对账
  (muniu 仓 `scripts/reconcile.sql`,差异 ≤1%),期满达标 ADR-007 转「已接受」;
  回滚=`.env` 三处改回直连(切换前备份 /tmp/.km-agent-env-pre-muniu.bak,
  重启即失)。运行细节见「环境事实」11。
- 001–081 已全部归档(053–080 于 2026-09-28 归档)。上游 41 域全覆盖。
  近期批次:2026-08-29 四连(043 生成纪律规则 10+哨兵短路/044 NO_DATA
  四态中性形态/045 保留表收编 user_memory/046 投影 id 收敛+列级哨兵);
  2026-08-30 二连(047 检索治理/048 聚合追问与去重护栏);
  **2026-08-30 三连(049 图表候选化/050 内部 id 暴露治理/051 实体核对判据)**;
  **2026-09-11(052 知识覆盖报告)**。
- 051 收尾同批:query_history goods 硬凑毒沉淀清偿(精确 id,保留合法
  语义行);infra/example-data 新增 comments-ext.sql(sakila_ext 独立库
  中文注释,**只能对 sakila_ext 执行**,严禁混入 comments.sql 在 kongming
  库跑)+load.sh 第 6 步挂载;重扫实证 PG comment→知识链路生效(存量
  AI_GENERATED 条目按优先级不覆盖,刻意设计)。终态教训:毒沉淀清理与
  prompt 重启生效之间存在窗口期,事故会重新落库——清理+验证后必须复查
  零残留;sakila_dyn 挂 default 租户(datasource_key 实查,勿按租户 key
  想当然)。
- 评估基线(evals/README 基线表):**2026-10-05(095 扩展开,指纹 f05c8028)**
  ——单轮宽松 50/50、严格 37/50、动态 4/4、表命中 49/50(多轮未随本批复跑,
  上值为 08-30 的 12/12);同批对照:扩展关=宽松 50/50、严格 35/50、表命中
  48/50,与 08-30 基线逐项一致(关停零回归实证)。归因口径(096)同批上线,
  两轮宽松全过零失败题、归因计数皆空。基线纪律触发面已扩至知识/文档变更
  (08-23 知识再生成+文档灌入晚于旧基线致零提示词变更亦漂移的混杂教训);
  两轮评估之间须清 ctx 缓存。
- 工作区/提交状态以 `git status` 与 `git log` 为唯一事实源
  (规约:文档不写提交哈希、不谈提交形态)。
- **开放挂账=零**(backlog-settlement 终态化裁决,见「挂账终态」节)。
- 下一棒(2026-10-10 清偿后重写;原 4 项中 3 项已闭环,见顶节
  「当前状态(2026-10-10)」):
  1. ~~评估集对拍 / 064 决策 / 078 selfheal 冒烟 / anthropic 条目流式~~
     → 全部已处置;064 主链路已随 106 收编恒生效(无切换动作、无配置项);
  2. ~~responses 条目流式问答 e2e~~ → 2026-10-11 经 muniu 桥补验通过;百炼
     直连多轮工具回放不可用(服务端缺口,见顶节「流式协议覆盖现状」);
  3. ~~064「路径翻转」更大语料证据=shadow 模式自然沉淀~~ → 随三态收编
     作废;闸门观测恒走 trace(dropped 按路下发),质量信号以评估集为准。

## 新会话开工指引

1. 先读本文与 `AGENTS.md`;动敏感区前按 AGENTS.md「前置阅读」表读对应 ADR。
2. 功能开发走 openspec 流程(`/opsx:propose` → apply → archive);
   提案必须有 Non-goals;一次只保持一个活动实施变更。
   **归档前若 specs 已手工同步,必须 `openspec archive <c> --skip-specs -y`**
   ——否则 delta 双套用被拒(「already exists」),且 069 式 MODIFIED delta
   会误删当前 spec 场景(工具拒绝是对的,见 2026-09-28 归档实证)。
3. 质量底线:安全规则类(SQL 防火墙/RBAC/租户)测试先行;收工前
   `make check` 全绿 + 冒烟记录;改 api 路由/模型必跑 `make contracts`
   并提交产物;GET 端点必须挂 response_model(ADR-005,已两犯)。
4. **prompt 资产纪律**:`apps/agent/kongming_agent/graph/prompts.py` 与
   `nodes.py` 的 LLM 模板为上游逐字移植(全角标点!),改动前先读文件头
   例外清单;`test_prompts.py` 断言防手滑。titler.py 是自研资产不适用。
5. 上游参考项目 `/Users/solvela/nl2sql-data-platform`(Java COLA)只参考
   逻辑不复制代码;其容器/卷/网络绝不触碰。

## 环境事实(新会话最易踩的坑)

1. **本机是 podman 不是 docker**:`docker` 只是交互 shell 的 alias,
   非交互命令一律用 `podman`。
2. **本地基础环境已统一到 `~/workspace/localenv`(2026-10-11 现态;owner 裁决
   基础设施收敛单源、禁止另起容器——10-09 曾迁 `~/workspace/env`,该目录与
   env-\* 容器已不存在)**:api **8000** / agent **8001** / web dev **5173** /
   postgres **5432**(localenv-postgres,compose 镜像已由本仓换入
   `pgvector/pgvector:pg18`——原 postgres:18-alpine 无 vector,同为 PG18
   major 卷数据兼容;角色 `kongming/kongming` SUPERUSER+CREATEDB,与原
   compose 超管形态对齐;库 `kongming` 开发库,`kongming_test` 由 conftest
   fixture 自建;NOLOGIN 只读角色 `km_readonly` 已按 `infra/pg-init/
   001-readonly-role.sql` 预建,kongming_test 侧授权由 baseline 迁移自建)/
   redis **6379**(localenv-redis,**带密码**=localenv/.env 的 REDIS_PASSWORD,
   勿入仓库)。**跑测试必须 export KM_REDIS_URL**(conftest 缺省无密码连接串
   对 localenv-redis 必 NOAUTH 成片 ERROR;KM_DATABASE_URL 缺省即 localenv
   形态无需注入);切勿 `set -a` source 根 `.env` 跑 pytest(2026-10-09 实证:
   KM_INTERNAL_TOKEN 真值会盖掉 conftest 测试缺省,内部端点全 403,126 例假败)。
   api/agent 容器冒烟仍让到 **18080/18081**(宿主 8000 属其他项目)。本仓
   `infra/docker-compose.yml` 的 postgres/redis 服务保留给容器部署形态,
   本地开发勿再 `make up` 自起(会与 localenv 抢 5432)。另:本机系统级
   HTTP 代理常开(`scutil --proxy` HTTPEnable=1),httpx 缺省 `trust_env=True`
   会经 getproxies 采到它——127.0.0.1 任意死端口曾稳定回无特征 502(代理
   接管,2026-10-11 实证);**2026-10-12 起全仓 httpx 出站统一
   `trust_env=False`**(代理环境会架空 SSRF 钉扎、劫持 api→agent 内网回调;
   新增出站点必须带上),「死端口必 ConnectionRefused」断言已恢复本机可过。
   另:0047 迁移评审期修订为 uuid7 逐行回填(ADR-004 v7 口径;PG 无内置
   v7 生成,不用 gen_random_uuid 的 v4 形态)——本地开发库在修订前已应用
   旧版,存量在架 Bot 的 v1 行主键保持 v4,新环境与 kongming_test 从修订版
   跑全 v7;版本形态混合为已知现态,运行行为无差。
3. **本地环境冷启动**(幂等,任何"连不上/端口不对"先跑这个):
   ```bash
   cd ~/workspace/localenv && podman compose up -d
   cd apps/api && uv run alembic upgrade head      # 开发库迁移(kongming_test 由 conftest 自建)
   ```
   pg 数据持久化在 localenv 命名卷 `localenv_postgres-data`;删卷/重置属
   破坏性操作,须用户明确授权。localenv 服务清单与连接信息见其 README。
4. **两 apps 的 .env 现态**(2026-10-11 复核,替代 2026-09-28「不存在」
   旧口径):`apps/api/.env`、`apps/agent/.env` 均已存在;api 侧
   `KM_REDIS_URL` 为无密码连接串(对 localenv-redis 必 NOAUTH,跑测试仍按
   第 2 条 export 覆盖)。settings 的 `env_file=".env"` 是 cwd 相对——
   进程工作目录决定是否读取;根 `.env` 只供 `make up`/compose 插值用。
   切模式(如 SQLGuard primary)改 .env 或启动 shell export 二选一。
5. **冒烟栈现役形态(2026-09-28)**:podman compose 容器
   `kongming-api-1`(18080)/`kongming-agent-1`(18081),env 由
   override 文件注入(示例:`/tmp/smoke-primary.yml` 置
   `KM_SQLGUARD_MODE=primary` 等;`podman compose -f infra/docker-compose.yml
   -f <override> up -d api agent`)。宿主机 gvproxy 转发 18080/18081。
   **本轮凭据一律来自容器 env(根 .env),勿读勿打印**;从现有容器提
   env 到新 compose 调用可用 `podman inspect <c> --format '{{range
   .Config.Env}}...'`(值不回显,直接 export 给子进程)。
   LLM 冒烟出口=`DASHSCOPE_API_KEY` 环境变量(dashscope 兼容端点,
   qwen-plus + qwen3.7-text-embedding)。
6. 可能的后台进程:compose 容器(见 5)、web dev 多实例(5173/5174 属
   用户其他项目,**别 kill 错**——按 `ps -p <pid> -o command=` 确认
   kongming 再动手);日志 /tmp/km-*.log。
7. **浏览器实测**:ZCode IAB 面板对本机 guest 会卡死——GUI 测试用
   Playwright。本机无 MCP playwright 时的可用形态(2026-09-28 实证):
   `npx playwright install chromium` 下载会停滞,改用在装 Chrome 153 +
   `chromium.launch({ channel: "chrome", headless: true })` 免下载;
   web 的 `/api` 代理目标可用环境变量 `VITE_API_PROXY_TARGET` 覆盖
   (066 收尾新增,缺省 8000 不变)指向冒烟端口,免改仓库配置。
   React 受控 textarea 用 `fill()` 即可;流式答案断言要排除问题气泡
   自身的文字(关键词假阳性实证)。
8. **dev 库活数据**:admin 密码以容器 env 为准(**非**缺省 admin123,
   2026-09-28 登录实证);冒烟账号自建范式:`SqlUnitOfWork` + SUPER_ADMIN
   角色绑定 + 默认租户(本轮 smoke/smoke-pass-123 即此形态,可删)。
   sakila(film 1000/rental 16044/customer 599)与 oist(orders 99441/
   items 112650)两租户已注册绑定 admin;`tenant_default.goods` 小表
   4 行 + smoke-self 用户(SELF 域,密码 `Smoke#2026`)+ `sys_datasource`
   行 sakila_dyn(同实例**异库**动态源,指向独立库 `sakila_ext`——外部
   业务库形态,不复用 tenant_sakila 租户 schema;装载随 load.sh 第 6 步)
   + 5 个联邦账号(漆阳帆-dingtalk 等)——均为回归活数据。冒烟库
   `kongming_smoke`(26 表:smoke_goods/smoke_orders + 24 张 filler)
   为 063/064/080 实测建的隔离库,可整库 drop。示例数据源文件
   `.example-data/`(已 gitignore),装载管线 `infra/example-data/load.sh`
   (幂等)。存量环境 sakila_dyn 仍指旧形态(同库 tenant_sakila)的,
   手工一行迁到新落点:`UPDATE public.sys_datasource SET database='sakila_ext',
   default_schema='public' WHERE datasource_key='sakila_dyn';`(密码不变)。
9. 企微联调:ngrok 免费域名与其可信域名校验不兼容;用 cloudflared 快速
   隧道或正式域名;应用可信 IP 白名单随家宽漂移重配。
10. **仓库迁移后旧 venv 假健康**:apps/*/.venv 若建于旧绝对路径
    (如 ~/kongming),迁到 ~/workspace/kongming 后入口脚本 shebang 全
    失效——`uv sync` 仍显示 Checked(假健康),`uv run pytest` 报
    Failed to spawn。处置:`rm -rf apps/*/.venv && uv sync` 重建
    (venv 为可再生构建产物,2026-09-11 052 实证)。
11. **muniu 网关(ADR-007,2026-09-26 修订后为可选端点;官方直连一等公民)**:127.0.0.1:8787,
    **podman 后台容器形态**(muniu 仓 `make dev-up` 起/`dev-down` 停/
    `dev-status` 查;宿主交叉编译+scratch 镜像,不依赖 registry 与代理)。
    ⚠ 计量库在 podman **命名卷 muniu-data**(SQLite WAL 在 virtiofs 挂载上
    会 I/O error),宿主查询走 muniu 仓 `make dev-sql`(拷出+sqlite3,
    对账 reconcile.sql 用法不变);容器日志 `podman logs muniu-gateway`。
    **管理面对账凭证(055)**:`KM_MUNIU_ADMIN_TOKEN`(对账端点拉 muniu
    `GET /admin/v1/usage` 用,与 muniu 容器 `MUNIU_ADMIN_TOKEN` 同源;
    环境变量注入,不落库不入日志)。
    ⚠ 切换后 agent `.env` 的 KM_AGENT_LLM_API_KEY 已是 muniu 的 mk- key
    而非 dashscope key;上游 key 真值经 muniu 仓 `.env.local`(gitignore)
    注入容器,重签 key 需同步 agent `.env` 两处(主 key+registry qwen-vl
    条目)并重启 agent。管理面/控制台同端口 8787(admin token=本地 dev 值
    `muniu-dev-admin-token`,与 KM_INTERNAL_TOKEN 同级,仅本机)。
12. **liannu 引擎共享包**(`packages/liannu`,053;原独立仓 km-sqlguard 已并入):
    SQL 安全引擎,uv path 依赖。升版流程:包内 `pyproject.toml` 改版本号 →
    同步两进程 `pyproject.toml` 的 `==<版>` 声明 → `uv lock && uv sync`
    (path 依赖 editable 生效,无 wheel 缓存坑);对拍基线回放走根
    `make check` 的 `liannu-check`。历史教训仍有效:**常驻 `uv run uvicorn`
    服务持有 uv 缓存锁时 `uv cache clean` 会超时**——升版本号路线可绕开。
13. **测试库「dropped 列占位」上限(2026-10-12 事故)**:迁移 downgrade
    在 PG 是假删列——只标 attisdropped,占位永久保留、information_schema
    不可见、单表硬上限 **1600**。迁移 roundtrip 测试曾全跑在共享
    kongming_test 上,`sys_trigger_definition` 累积 1577 个占位后 0050 的
    `ADD COLUMN` 直接炸,全量门禁 fixture 级失败(表现=conftest 的 alembic
    upgrade 报 TooManyColumnsError)。**现态:roundtrip 测试已全部改走
    `tests/conftest.py` 的 `migrated_tmp_db()` 一次性临时库**(随机后缀库
    →演练→DROP,占位随库清零);新增迁移 roundtrip 测试必须用它,禁止在
    kongming_test 上跑 add/drop 循环。占位识别:`SELECT count(*) FROM
    pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON
    n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname='<表>' AND
    a.attisdropped`(超 800 即应重建);重建=DROP DATABASE kongming_test
    WITH (FORCE) 后 CREATE(纯测试库,conftest 自动迁移),**重建后必须重跑
    上文 zhparser 两件套**(扩展/检索配置按库安装,漏掉=4 个中文分词集成
    测试转 skip;2026-10-12 重建后已补装)。

## 命令速查

```bash
make check          # 全门禁(api/agent/liannu/web + contracts)
make contracts      # 导出 OpenAPI + 生成前端类型(改 api 后必跑)
# 服务: apps/api → uv run uvicorn kongming.main:app --port 8000
#       apps/agent → uv run uvicorn kongming_agent.main:app --port 8001
#       apps/web → pnpm dev(5173,proxy /api → 8000;VITE_API_PROXY_TARGET 可覆盖)
# 迁移: apps/api → uv run alembic upgrade head
# 评估: KM_EVAL_ADMIN_PASSWORD=… uv run pytest evals -m smoke|full|multiturn
#      (evals/ 不进默认收集,无凭证整层 skip)
# 冒烟: curl 127.0.0.1:8000/healthz;curl 127.0.0.1:8001/healthz
# 容器冒烟栈: podman compose -f infra/docker-compose.yml -f <override> up -d api agent
```

## 已定死的决策(不再议,引用即终)

| 主题 | 结论 | 出处 |
|---|---|---|
| 技术栈 | Python 3.13 双进程(api 持凭证 / agent 零凭证)+ React 19 SPA;不引入 Keycloak/Next.js/微服务/消息队列/Litestar | ADR-001 |
| 信任边界 | 进程边界构造级;agent 禁 DB 凭证(env/驱动双禁);合并进程=违宪 | ADR-002 + 宪法 Article IV |
| 认证 | 自建(argon2id + PyJWT RS256 + authlib 联邦连接器);未来企业 SSO 走 IdP 下游回退 | ADR-003/006 |
| Schema | baseline 制;UUID v7 / VARCHAR+CHECK / timestamptz;已规划能力列一次建全;新表/改表走新迁移 | ADR-004 |
| 契约 | FastAPI OpenAPI 唯一事实源;前端类型生成禁手写;CI 漂移 diff | ADR-005 |
| 分层 | 域包 router→service→models→repository;domain 零框架依赖 | 宪法 Article II |
| 执行路由 | verdict 三态(PASS/BLOCKED/ERROR);绑定复校;data_scope=最外层 WHERE AST 注入;控制面保留表拒读+危险函数拒用;只读池连接级只读 | sql-execution + datascope spec |
| 问答链路 | agent 8 节点图(老链路)+ ReAct 内核(019)双轨;verdict 对外四态(PASS/BLOCKED/ERROR/NO_DATA,044 如实说明);权限类不重试;重试上限=初始之外 2 次 | nl2sql + nl2sql-agent + chat spec |
| RAG/知识库 | rag 两表(四元唯一键+HNSW+GIN);三路召回 RRF top5+query_history 独立 few-shot;召回零命中回退内省;成功 SQL 回写;覆盖优先级 INTROSPECTION<AI<UPLOAD<EDIT<FEEDBACK;别名(037)=生效值同源重算派生文本 | rag + knowledge spec |
| 评估体系 | Execution Match 双分引擎;宽松=黄金指标且不惩罚序错;evals 进程外工具 testpaths 隔离;query_history 幂等治理 | eval + ai-eval spec |
| prompt 质量 | 生成/理解/摘要三层规则(统计不加 LIMIT/tiebreaker/ROUND/投影完备+技术主键默认不进投影/列级哨兵两级如实说明/指代保真/事实性接地);prompt 文本迭代经验=点名自我合理化的反例比抽象纪律条遵从度高;gold 规范十条与生成侧互锁 | nl2sql-agent spec |
| Bot/商业化 | Bot 定义+权限逐请求求交;OpenAI 兼容端点;BOT 维度配额+mock 支付闭环(核销单点 reconcile);团队归属可见性纯函数 | custom-agent + payment + team spec |
| 多模型/观测 | 注册表 env JSON(条目级 protocol:openai/anthropic/responses)+任务路由+出口白名单;协议翻译层单点收敛(llm_protocol.py);用量采集 fire-and-forget+bot 收集器双通道;失败调用也采集(status/latency) | multi-model-routing + observability spec + ADR-008 |
| LLM 网关 | muniu(127.0.0.1:8787,OpenAI 兼容)为可选端点,官方直连一等公民;供应商 key 仍可收敛 muniu 单点;回滚=.env 三处改回直连,零成本 | ADR-007(2026-09-26 修订) |
| 前端 Markdown 渲染 | 文档卡收编 react-markdown+remark-gfm/breaks;不解析原始 HTML、无 dangerouslySetInnerHTML | ADR-009 |
| 挂账治理 | 开放挂账=零;新发现项一律给终态(修/关闭/条件触发/产品登记),不留无排期「随域延后」 | backlog-settlement 终态块 |

## 挂账终态(开放挂账=零,2026-08-25 backlog-settlement 裁决;2026-10-01 曾登记的两条「待 owner 裁决」已由 086 裁决并落地,见本节末)

**明确关闭(结案)**:A-B10 confirm 硬熔断(write_burst 写闸实证兜底)/
B2-B9 http 响应头闸(h11 16KB 默认兜底已实证+注释固化)/审计页 user 筛选
UI(API 已备场景低频)/D-B7 进度条动态 style(原裁接受)/003 联邦真实回调
人工 e2e(2026-10-14 结案:收口提交明录真凭证 e2e 五家全过[github 登录即其中一路,owner 同日确认],归档件当年漏勾;
后续仅 trust_env=False 加固与类型收敛,无流程行为变更——真实使用即持续验证面)/支付回调先查单后验签探测面
(2026-10-14 结案:owner 确认真实支付不做,触发条件永久排除;mock 支付闭环
保留为商业化演示形态,与产品登记「真实支付明确不做」同口径)/
alibaba_ai_search 真实出站 e2e(2026-10-14 结案:owner 提供实例域名与 `OS-`
key,注册表回填真实 host,网关实调 executed+5 检索卡,曾在案的「待 key」
条件消除;key 已入根 .env 的 KM_WEBSEARCH_*,不入库)。

   **已裁决并落地(2026-10-01,change 086 `authz-status-single-gate`,已归档)**:
- **禁用角色让行级可见范围反向放宽 → 角色状态升为授权读取的统一闸**:裁的是「**一切**按主体取授权面的
  读取都必须 `sys_role.status='ACTIVE'`」,而不是只补 applier 那条「无启用角色」分支。理由:`datascope`
  主规格早就把范围解释成「用户**启用**角色的并集」,而功能权限点(`SqlPermRepo.list_codes`)、审计/反馈/
  工具调用的全范围判定、quality 的 `user_has_scope_all` 五侧都不看角色状态——同一事实两套口径,禁用角色
  就成了「功能权限留着、数据范围反而放到全表」的反向杠杆。一处真相要求所有读取同源,故 5 个站点一并加
  过滤(第 5 处 `quality/repository.py` 是评审 R2 才扫出来的,前三处清单当时判为完整;datascope 侧的
  `user_role_scopes` 与 083 起的 `custom_dept_ids` 本就带闸,补齐后全仓 7 处同闸)。配套把「主体不可
  确定」统一为 fail-closed:用户不存在/已删除、无启用角色,都在 applier 抛 `DataScopeUnsupported` 并
  `log.warning`。`FilterType.SKIP` 的「降级为不过滤」只保留**结构性**成因(主表无法定位、目标列不存在),
  它们与「主体解析不出可见面」是两类事实,前者由 `datascope` 主规格自定场景钉着,不是 fail-open。
- **`sys_dept.status` 退出行级可见性判定**:裁的是「**删掉**递归 CTE 里那两处
  `status='ACTIVE'`」,而不是把 DEPT/CUSTOM 补齐成过滤。三档里 DEPT(本人部门)与 CUSTOM(角色配置集合)
  本就按字面生效、083 主规格写明「按字面生效」,只有 `DEPT_AND_BELOW` 的 CTE 是唯一异类;统一到「过滤」
  侧等于给一个文档里从没承诺过的字段装上「一改就让一批人看不见数据」的隐藏宽闸;而实证面上
  `sys_dept.status` 除这条 CTE 外只在部门 CRUD 的读写里作为一个字段呈现(全仓 grep 无其他谓词消费它),
  即它此前并不承担可见性语义。现由 `datascope` 一条 MUST NOT 场景钉住(停用部门仍在其上级范围内可见),
  用例 `test_dept_and_below_includes_disabled_dept` 反查。若日后确要「停用即停可见」,那是新的产品需求,
  得另立变更并同时改三档与前端语义,不能靠回归这条 CTE 偷偷实现。

   **条件触发登记(触发即评估,非开放债务)**:
- 注入拆字/全角变体绕过:触发=出现真实绕过事件或规模化信号。
- MCP 远端错误文案反射面收紧:触发=生态滥用信号。
- 多 worker 三项(池失效广播/归档 advisory lock/调度互斥):触发=实际多 worker 部署。
- /mcp chunked 无 CL 形态的实测闸边界(body 已缓冲,属应用层输入约束
  非完整 DoS 防护):触发=网关层限流设计。
- 流式/同步端点泛异常与客户端断开窗口的收集器用量丢失(「响应携带」
  契约的边界;bot 当前走同步端点暴露面有限):触发=流式 bot 落地。
- /query/stream(老链路)附件预处理仍在 handler 段(与 react 流式移入
  gen 的形态不一致;残余异常面≈0 仅编程错误):老流式挂收集器时同步迁移。
- 辅助数据查询(tenants/myTeams)失败静默:核心已降级显式,其余触发=用户反馈。

**产品登记(非债务;单源=docs/roadmap.md「产品待办登记」节,含否定裁决
登记——Slack/zhparser 切默认/真实支付/OCR 引擎已明确不做,STT(114)/
个性化推荐(039)/图片问数(030)已落地,勿再作为待办重提;可视化画布/
MCP resources/prompts/Bot 版本化/配额/重试/邮件事件源/zhparser 配置化/
知识库团队归属/指标可见性等已分批落地归档)**。

**出处说明**:终态主清单见 archive/2026-08-25-backlog-settlement/proposal.md;
「触顶部分结果的 web 渲染增强」「辅助数据查询失败静默」两条为 R1/R2 评审
期新增裁决(本节即出处)。

**历史明细**:各域逐项挂账原文见 git 历史版本文档与 archive 工件。

## 关键教训精选(代码里看不出来的操作知识)

流程与门禁:
- 会话级 alembic upgrade 的 `fileConfig`(disable_existing_loggers)会把
  **收集期已 import** 的模块 logger 置 disabled——新测试用 caplog 断言日志,
  夹具里先把目标 logger `disabled = False`(053 test_sqlfw_engine 实证)。
- make check 后台跑必须看 **make-exit 行**(包装 shell 的 exit code 是
  最后一条命令的,曾有整库迁移炸了仍假绿的实证);后台命令内自拼
  `echo MAKE-EXIT:$?` 同样会假绿——用 `make …; echo EXIT:$?` 同行或
  前台直跑。pyright 全量含 tests/,只对 kongming/ 子集跑绿不代表门禁绿。
- **`# type: ignore[code]` 压不住 pyright 的参数级定位**(054 补 CI pyright
  首跑实证):mypy 把 kwargs 类型错汇报到调用尾部行,pyright(strict)逐参数
  行报——ignore 挂在 trace_id 行压不住 api=/llm= 行的错,22 处全数暴露。
  修法 = 把 ignore 精确移到报错参数行(mypy strict 含 unused-ignores,
  不能无脑加,移完双跑 mypy+pyright 裁决)。另:管道验证退出码必须取
  `PIPESTATUS[0]`,`cmd | tail; echo $?` 取到的是 tail 的(本轮实证假绿)。
- **共享测试库并行 pytest 互锁**:两个 pytest 同时跑会零 CPU 挂死
  (非卡测试)——先停自己那份错峰重跑判归属;单挂用例先单独重跑再定性。
- **知识层事故定性先核租户/数据源维度**:default 源是共享路径(各租户
  各自登记同 key 知识),按 datasource_key 查到的行未必属于事发租户;
  042 曾据此误判「残留毒知识」两轮修正——真实根因在生成层(LLM 凭
  先验编表),rag 召回隔离本身无恙。
- **勿跑 ruff format**(全仓非 format 管理,误跑产生海量格式噪音)。
- 测试模块真建只读池的,teardown 必须 `dispose_read_engines()`(池残留
  污染后续模块断言);测试 override 禁裸开 get_sessionmaker(耗尽主池)。
- 受控行为变更必须同步对齐既有测试断言(字段钉集断言两犯)。
- 时间敏感测试禁「replace(day=N)-timedelta(相对日)」算式(偏移随日数
  变号,每月固定日期必挂;用固定回退天数±半日)。
- ruff noqa 对多行隐式字符串拼接须挂块首物理行。
- 改 api 后契约必须重生成并提交;运行时与入库 openapi 逐对象比对防漂移。
- **asyncpg 读 json/jsonb 常回 str**(system.parse_json_agg 先例):text()
  裸读必须归一化解码,`isinstance(dict)` 直接丢会静默读成空(056/059
  评审实证 prefs/skillpack content 双双中招)。
- **禁用不删指标 + 启用前同名冲突检测 ⇒ 重启用自冲突**:冲突判定须按
  「同名且定义一致=幂等放行」,否则第一次启用成功、第二次永远 409
  (056 skillpack metrics 实证)。
- **任务勾选 ≠ 代码落地**:059 的注入通道/显式路由曾全勾但 `preference_hint`
  零调用方、extract 仍吐自由文本——收口验收必须跑通链路,不能只看 tasks。
- **Python 3.14(PEP 649)惰性注解会掩盖 TYPE_CHECKING latent bug**
  (070 实证):只在 TYPE_CHECKING 导入、却在函数签名注解/except 子句
  取用的名字,3.14 测试 venv 永不求值(全绿),3.13 容器 import 即
  NameError;pyright 也因类型态可见而静默。防线= AST 断言「定义时
  求值位置禁现 TYPE_CHECKING 专有名」(tests/test_module_import_py313.py),
  组合根类签名注解/except 名必须运行时导入。
- **假 embedding 兜底签名下的相似度分布不可当真值**(064 标定实证):
  hash 种子向量对无关文本也有 ~0.7 相似度——闸门阈值标定必须用真实
  embedding 实采(试跑端点),fake 分布会得出错误阈值。
- **openspec archive 前 specs 已手工同步 ⇒ 必须 `--skip-specs`**
  (2026-09-28 实证):全量 archive 会 delta 双套用被拒(「already
  exists」);且 069 式 MODIFIED delta 替换整块会误删当前 spec 已有
  场景(工具拒绝属实名防卫)。specs 以 apply 期手工同步为終态。
- **冒烟栈注入 env 用 compose override 文件(/tmp,不入库)**,宿主机
  8000 被占用时 api/agent 容器让到 18080/18081;从旧容器提 env 用
  `podman inspect ... '{{range .Config.Env}}'` 回接子进程,值不回显。
  (2026-10-09 起 pg/redis 用统一栈 `~/workspace/env`,6380/6399 避让口径退役。)
- **浏览器断言防问题气泡假阳性**:流式答案关键词(如「库存」)可能命中
  用户问题自身——断言前先把问题文本从正文剥掉再看。

LLM/异步/协议:
- 工具回调通道 status 恒 executed,**业务 verdict 在 result 内**(去重/
  熔断集按 verdict 收,否则诱导 LLM 编造)。
- httpx 超时=单次操作间隔而非总时长(慢滴必须 wait_for 包总预算);
  `asyncio.wait_for` 抛 TimeoutError ⇔ 内部 task 被其取消——内源超时
  透传时 task 未取消,以此区分超时来源(workflow 引擎范式)。
- LLM 应答畸形(空 choices)须收敛 LlmError+归 ERROR 采集,不能裸
  IndexError;embed 应答校验 index 集合等价而非仅条数。
- AsyncOpenAI 客户端池跨事件循环不可复用(缓存按 loop 维度+weakref 校验)。
- 共享 AsyncSession 的多 repo 查询必须顺序 await(并行会话状态未定义)。
- api 调 agent 回调的应答解析须防非 JSON/畸形(resp.json() 裸抛会违约
  降级契约;先取文本再显式解析,异常收敛到该用例的降级分支)。
- evals「Server disconnected」成片失败=本地 VPN 切换环境事件(TUN 路由
  重建波及回环),勿误判限流勿长冷却,网络稳定后重跑。

SQL/PG:
- sqlglot 深嵌套抛 RecursionError **不在** SqlglotError 族(except 须扩面)。
- LEFT JOIN 的范围过滤条件必须放 ON 内(放 WHERE 把补零桶滤掉)。
- 超长中文文本列唯一索引用 md5 表达式索引;ON CONFLICT 要求表达式逐字
  匹配(裸列/表达式失配 42P10)。
- asyncpg None 参数需 CAST 形态防类型推断失败;uuid 列绑 UUID 对象。
- 并发 UPSERT 唯一约束竞态用 advisory lock+单语句 CTE 两件套
  (READ COMMITTED 下 wCTE 快照竞态消不掉)。

安全:
- SSRF DNS rebinding 防护=自定义 transport 钉扎判定 IP+Host 头保原域+
  `extensions["sni_hostname"]`(证书按原域校验)。
- 免费文本进三引号围栏前必须替换围栏序列(提前闭合=注入逃逸)。
- 别名/文件名类自由文本校验结构字符(换行可伪造表名注入);上传文件名
  过护栏 pii_*/cred_*/inj_ 规则(拒绝优于脱敏:脱敏名撞唯一键)。
- 敏感凭证字段 api_key 用 repr=False;PENDING_CONFIRM 凭证原文终态清零。

前端/浏览器:
- Playwright MCP:textarea 用原生 setter+input 事件(React 受控组件 fill
  偶发不触发);Radix 下拉/菜单 pointerdown 派发+evaluate DOM click;
  react-query 失效重渲染后快照 ref 过期,click 静默落空——改 evaluate
  派事件;短时窗口采样用 evaluate 内 setTimeout 轮询。
- 5xx 触发 react-query retry(约 7-8s 后才 isError,断言错误态要等够)。
- `useParams` 无参数=undefined,与流发起的 `?? null` 不同——守卫比较前
  必须归一(新会话首问死锁根因)。
- HMR/Fast Refresh 保留组件本地 state,评审读数以 fresh navigate 后为准。
- Tailwind v4 任意值 `calc()` 的 `+` 两侧必须写 `_`(转空格),否则整条
  声明静默无效(2026-09-26 dialog 关闭钮实证)。
- 绝对定位 `inset-0` 基类做响应式改型,sm: 只还原 top/left 会残留
  right/bottom——top:50%+bottom:0+height:auto ⇒ 50vh 贴底;四元式必须
  成组还原(桌面对话框全面破版实证)。
- border-box 下 `h-16` 与 `pb-safe` 同元素 = 安全区吃掉内容高度;安全区
  padding 与固定行高必须分层(外层吃安全区、内层行高)。
- 图标尺寸覆写别和 `[&_svg:not([class*='size-'])]` 规则打特异性——svg
  上显式钉 `size-*` 即被其 :not 排除,必胜。
- StrictMode 双 effect 只在 dev 出现,别据 dev 现象定性生产 bug;
  真代理拓扑(`VITE_API_PROXY_TARGET` 或 route.fetch 中继)二选一做
  冒烟,中继 fulfill 对 SSE 有缓冲语义,答案渲染验证以真代理为准。

## 铁律提醒(全局,详见 ~/.zcode/AGENTS.md 与仓库 AGENTS.md)

- git 写操作(add/commit/push)逐次等用户明确指令,只做字面授权那一步。
- 旧项目容器/卷/网络禁碰;任何不可逆操作前二次确认;运维指令绝不升级强度。
- 安全规则类改动测试先行;文档注释一律简体中文。
- 前端基础交互组件一律 shadcn/ui;同类元素 ≥2 处必抽公共组件;用户点名
  一个 UI 问题=该类全站范式问题,先归类再批量修。

## 门禁对照表(make check × CI,054)

> 增删任一侧门禁时双向核对本表,防止单边增删再现(054 立表动机:pyright 曾是本地专属门禁,CI 假绿)。

| 门禁 | 本地命令 | CI job | CI 步骤 | 差异说明 |
|---|---|---|---|---|
| api 静态+测试 | `make api-check`(ruff format/check + mypy + **pyright** + lint-imports + pytest --cov) | `api` | 同左全序(pyright 于 2026-09-13 补齐) | 无 |
| agent 静态+测试 | `make agent-check`(同上结构) | `agent` | 同左全序(pyright 同批补齐) | 无 |
| liannu 引擎 | `make liannu-check`(ruff+mypy+pytest,含对拍基线回放) | `liannu` | 同左 | 无 |
| web | `make web-check`(tsc+eslint+stylelint+prettier;**vitest 于 066 接入 CI `test:run`,60 例**) | `web` | 同左(066 补齐前端单测短板) | 无 |
| 契约漂移 | `make contracts-check`(已并入 check;055 教训后补齐) | `contracts` | `make -C ../.. contracts-check`(单源) | 无 |

## 邮件入站触发器演示配方(108;真实 IMAP 联调待邮箱账号,以下为可复现配方)

> 测试以内存 IMAP 桩全场景覆盖(`apps/api/tests/test_trigger_email.py`,21 例);
> 真实邮箱端到端联调挂账「待条件」(需可开 IMAP 的邮箱,QQ/163 需授权码,Gmail 需应用专用密码)。

**1. 配置 EMAIL 触发器**(管理面同 webhook;凭证即刻 AES-GCM 加密落库,任何接口不回显):

```bash
curl -X POST http://127.0.0.1:8000/system/triggers \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-Id: $TENANT_KEY" \
  -H "Content-Type: application/json" -d '{
    "tg_key": "inbox2wf", "name": "审批来信", "type": "EMAIL",
    "target_type": "workflow", "target_key": "echo",
    "imap_host": "imap.qq.com", "imap_port": 993, "imap_tls": true,
    "imap_user": "demo@qq.com", "imap_password": "<授权码>",
    "imap_folder": "INBOX", "imap_poll_interval_sec": 60
  }'
```

轮询间隔下限 30 秒封顶;创建即到期,调度 tick(30s)首个周期开拉。

**2. 发测试邮件**(任一外部邮箱向 `imap_user` 发一封,主题任意);

**3. 触发核验**:

```bash
# 历史(run 行带标准化摘要 JSON:from/subject/date/text_preview 四字段)
curl "http://127.0.0.1:8000/system/triggers/<id>/runs?page=1&size=10" \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-Id: $TENANT_KEY"
```

```sql
-- UID 游标(断线/重启续传的凭据;UIDVALIDITY 变更会重置并告警)
SELECT folder, last_uid, uidvalidity, updated_at
FROM public.sys_trigger_email_cursor WHERE trigger_id = '<id>';
-- run 历史:邮件原文/完整正文不落库,只留白名单摘要
SELECT status, error, summary, created_at
FROM public.sys_trigger_run WHERE trigger_id = '<id>' ORDER BY created_at DESC LIMIT 5;
```

排障口径:连接失败/认证失败落 `FAILED` run 且 error 前缀分类(`CONNECT_FAILED`/`AUTH_FAILED`/`PROTOCOL_ERROR`/`CREDENTIAL_ERROR`),按轮询间隔重试不堆积;停用后停轮询、游标保留,重新启用从游标续传(停用期间新邮件不丢)。
