# HANDOFF — Kongming 交接文档

> 最后更新:2026-08-21(**017 query-streaming 已实施归档并入库**:
> archive/2026-08-21-017-query-streaming,delta 已 sync query-streaming(新)
> +nl2sql-agent/chat/web-nav 四主 spec(21 spec 常规口径校验全过);
> SSE 端到端流式问答:agent /query_stream(astream updates+custom 双通道,
> 节点 wrapper 发事件——nodes.py/prompts.py 零侵入 prompt 资产纪律)+
> api /chat/query_stream(后台 task+asyncio.Queue 中继——**Starlette 断线
> 即取消生成器**,断线续存=独立 StreamStore 会话落库照常,010 title_tasks
> 同款模式;15s 心跳注释行;前段/终态落库与同步共用唯一实现禁第二套口径;
> 业务三态恒走 result 事件,error 仅传输层——优于上游 017 的混淆形态)+
> web fetch+ReadableStream 手写分帧(阶段逐个点亮/SQL 提前/打字机/
> 终态乐观快照接管/AbortController+上下文守卫);事件 data schema 进
> OpenAPI(responses model 联合注册+content oneOf,ADR-5 生成类型);
> R1 评审 1 严重(S1 后台任务异常逃逸=前端永久悬挂,except Exception
> 收敛+终止标记必达)+B1 接管间隙(乐观快照);**/goal R1–R5 五轮审查
> 循环清零**(R2 深挖再清 3:守卫死锁/datasource_key/快照接管;R3 契约
> 管线/跨变更交互再清 3:spec 字段漂移/合成卡片非 UUID id 422 面/读尾
> 双落库;R4 复核 R3 残留再清 2:nl2sql-agent spec 3 处漏改/result 帧
> 幂等守卫;R5 终收敛零新发现):S1 守卫 null===undefined 恒 false 新会话
> 首问死锁(冒烟抓回与 R2 推演
> 双路直证)/B1 datasource_key 重构丢失(_finalize_unavailable 补参+非
> default 数据源断言锁定)/B2 navigate 触发取消闸清快照(自建导航打标
> 豁免+Skeleton 让位);api 487+agent 95+web 全绿、契约 +1 端点+4 事件
> schema;**016 评估 smoke 5/5 复验零劣化**;真 LLM+Playwright 冒烟
> 9 项全过(阶段时间线直证/SQL 提前/断线续存落库/取消不残留/接管无
> 骨架屏闪变);**001–017 齐,活动变更为零;下一棒=prompt 护栏域**
> (摘要幻觉/规避写操作两类既有观察,016 评估集可量化验证)。
> 清空上下文/新会话先读本文与 `AGENTS.md`,
> 动敏感区前按 AGENTS.md 前置阅读表读对应 ADR。**本文行文只记录"代码里看不出来"的事实。**

## 新会话开工指引(最先看这里)

1. **017 query-streaming:已实施归档并入库**(archive/2026-08-21-017-query-streaming)——
   流式链路三件套:agent `POST /query_stream`(SSE,事件=stage/sql/summary/
   result/error 五类;**result 终态与同步 /query 同构**(共用 _build_query_out);
   事件发射点=graph/streams.py 的 stage_wrapper(**validate/selfheal 是同步
   节点**,wrapper 兼容两种形态;非流式调用 get_stream_writer 抛 RuntimeError
   静默跳过=同步路径零行为变化);langgraph 1.2.11 astream 双通道:custom=
   节点事件,updates=逐节点 dict 累积终态)。api `POST /chat/query/stream`
   (鉴权/前段与同步同构,异常常规 JSON 响应于流开始前;**中继架构=后台
   task(_consume_agent_stream)+asyncio.Queue+SSE 生成器**,Starlette 在
   客户端断开时会取消 StreamingResponse 生成器——与 Java SseEmitter broken
   标志的关键差异,断线续存因此必须独立 task;落库走 StreamStoreFactory
   独立会话(请求作用域 session 随取消关闭);**终止标记 None 必达铁律**
   (R1-S1:任何异常路径经 except Exception 收敛 error 终态,否则前端永久
   等心跳);心跳=wait_for(queue.get,15s) 超时分支(3.13 wait_for 不丢帧);
   事件名白名单 stage/sql/summary 透传,result/error 由 api 落库后重构)。
   web:api.ts streamChatQuery(fetch+ReadableStream 分帧,CR 剥除消跨 chunk
   边界,401 单飞重放,resp 非 ok 走 toStatusError,无终态抛错防永等待,
   abort 静默)+StreamCard(阶段点亮 role=status aria-live)+ChatPage 流式
   状态(**守卫 sid 归一铁律:useParams 无参数=undefined,流发起 ?? null,
   不归一则 null===undefined 恒 false 全回调被丢弃——S1 死锁根因**;
   终态乐观快照 toSyntheticMessage 消接管间隙,历史 refetch 命中
   message_id 即退位;**自建会话导航打标 selfNavigatedRef 豁免取消闸**
   ——否则 navigate 触发 sessionId 变化清掉快照,B2 间隙以骨架屏复活)。
   测试面:api 487(+17:流式 16+datasource_key 断言 1)+agent 95(+8);
   契约 +1 端点+StreamStage/Sql/Summary/Error 四事件 schema(make contracts
   重生成)。**016 评估链路零影响**:evals 走同步 persist=false,smoke 5/5
   复验;同步 service.query 重构(前段/终态抽共用)经 R2 逐字段对拍=等价
   (唯一漂移 datasource_key 已修+断言锁定)。**下一棒=prompt 护栏域**,
   其后按 roadmap 第三梯队。
   **016 eval+ai-eval 域:已实施归档并入库**(archive/2026-08-21-016-eval-execution-match)——
   评估器 apps/api/evals/(与 tests/ 平级,testpaths=["tests"] 门禁隔离,
   `pytest -q`/make check/CI 零收集,显式 `KM_EVAL_ADMIN_PASSWORD=… uv run
   pytest evals -m smoke|full|multiturn` 运行,无凭证整层 skip);三层
   marker+yaml 即资产(id/question/gold_sql/expected_tables/category/可选
   tenant/datasource_key/expect_reject/smoke);gold 直连 asyncpg(控制面
   DSN 派生),生成侧取 /chat/query 响应(sql+columns+rows,persist=false
   不落库不回写——writeback 挂 persist 条件,dev 库零污染);匹配器
   evals/matchers.py 纯函数(严格 multiset/宽松双路径:列等价+投影行近似/
   数值子集;**宽松不惩罚序错**=路径 2 天然无序,既定语义)——单测
   tests/test_eval_matchers.py 24 条进默认门禁;Recall=间接口径(生成 SQL
   表覆盖 expected_tables,复用 014 sqlglot 抽表);评估集 63 条 gold 全部
   连跑两次一致+≤1000 行;README 规范九条(第 9 条=明细题问句显式指定
   排序键,prompt 规则 6 已立约 gen 必排序,gold 裸 LIMIT 集合错位必挂——
   016 冒烟实证);**首基线:宽松 37/44(84%)/严格 26/44(59%)/多轮 7/10/
   动态 4-4/表命中 35/44**,失败归因五类(gen 统计题误加 LIMIT/::numeric
   舍入/投影丢列/指代消解错位/漏 DISTINCT)——prompt 护栏与 query-streaming
   的量化靶点。搭车:0007 迁移(query_history 存量去重保 created_at 最新+
   唯一索引 tenant+ds+question)+insert_history 改 UPSERT(ON CONFLICT 覆盖
   保留最新成功 SQL;embedding 失败 COALESCE 保旧向量;同事务上限淘汰
   QUERY_HISTORY_CAP=500)——009③ 与 014 O4 清偿,curl 同题两问实库直证
   行数=1。**下一棒=query-streaming 流式输出**。
   **015 审计可视化与留存:已实施归档并入库**(archive/2026-08-21-015-audit-visibility)——
   GET /audit/sql/page + /audit/sql/{id}(详情 403 同形)+/audit/oper/page
   +POST /audit/archive/run|restore(权限点 system:audit:view 排序 9 仅
   SUPER_ADMIN);行级可见性非 ALL 强制本人(014 同款);操作日志 ip 链路=
   contextvars 横切(ClientIpMiddleware→client_ip_var→SqlOperLogRepo.add,
   15+ 调用点零改动);chat.export 导出审计(010⑦清零,OperLogger 端口
   注入);归档=run_archive/run_restore 独立函数(180 天窗 gzip JSONL+
   manifest SHA-256;同日重跑幂等需 manifest_cutoff>=当前 cutoff 才跳过
   导出——days 调小不未导先删 R1-S2;asyncio.Lock 进程互斥 R1-S3;恢复
   按 id 幂等);lifespan 每日 03:00 薄壳调度;/admin/audit 第七子页
   (双区 toggle+时间范围筛选+详情展开);容器 --proxy-headers。
   R1 评审 5 严重+7 建议全修(含两条不可逆数据丢失路径)。**016 eval+ai-eval
   域为下一棒**(Execution Match 双分引擎+gold 数据集 sakila/olist 各
   15-20 题[上游 nl2sql-agent/tests/eval/*.yaml 参考逻辑]+基线管理+
   Recall@K+CI smoke;009③ query_history 治理+014 O4 重复沉淀搭车),
   其后 query-streaming。**014 反馈闭环:已实施归档并入库**(archive/2026-08-21-014-feedback-loop)——
   feedback 三域全落地:POST /chat/messages/{id}/feedback(幂等三态 null 保持/
   空串清空/显式覆盖+行锁串行+重复提交清空处置三列+反馈置 PENDING)+
   纠错防火墙校验(与 internal 同一规则集)复用 009 writeback 沉淀(会话快照
   数据源,清空不删已沉淀行)+PUT /feedback/{id}/status(处置限定已反馈
   assistant)+GET involved-tables(sqlglot 抽表 CTE 排除)+overview
   (Asia/Shanghai 分桶逐天补零)+page(行级可见性非 ALL 强制本人);
   messageId 打通(实时响应+历史回显 rating/corrected_sql/feedback_comment);
   权限点 chat:feedback:submit/system:feedback:view/system:feedback:handle
   排序 6/7/8(DEVELOPER 推荐绑定 submit);赞踩 UI(setQueriesData 本地
   即时回显)+/admin/feedback 第六子页;**chat_message 反馈 8 列 baseline
   已建全零迁移**。搭车:009⑦内省列注释进润色(columns_comment=
   db_comment or data_type,零签名变化三消费方获益)+⑪reindex 先润色
   后删写(分钟级 LLM 事务外,delete+insert+审计短事务,前置失败不进删写)。
   R1 评审 0 严重/4 建议全修(非法 uuid 传输层 422/编辑知识 ?table= 定位
   提示条+行高亮/comment 回显+未触碰传 null 保持/请求体类型生成)。
   **015-audit-visibility 已提案待实施**(SQL 审计/操作日志查询分页+
   行级可见性+ip 链路接通+导出审计 010⑦清零+180 天归档 gzip JSONL
   SHA-256 可重导入+lifespan 每日调度+/admin/audit 第七子页);其后
   eval+ai-eval 域、query-streaming。**013 挂账清理小修包:已同日归档**
   (7 项清零:chat.service 构造反转入契约/重命名 strip 后校验/
   AgentRagClient 单例/rag_retrieve 非 JSON 收敛/temporal 轴截断/
   scatter tooltip/删除 busy+ESC 闸)。
2. 质量底线:安全规则类测试先行;收工前 `make check` 全绿 + 冒烟记录;
   改 api 必跑 `make contracts` 并提交产物。**本地门禁只有 ruff check,
   不要主动跑 `ruff format`**(010 教训:全仓非 format 管理,误跑致 52 文件
   格式噪音,靠 git show 逐文件恢复回填)。**真建只读池的测试模块 teardown
   必须 dispose_read_engines()**(010 实证:test_datasource_admin「不建池」
   断言被先行模块的池残留污染)。
3. **prompt 资产纪律**:apps/agent/graph/prompts.py 与 nodes.py 的 LLM 模板
   为上游逐字移植(全角标点!),改动前先读文件头注释——手抄易丢全角,
   建议脚本对拍(005 实操:两次手滑半角,靠码点校验脚本兜底)。
   agent/titler.py(010)是全新撰写资产,不适用此纪律。

## 项目一句话

全能 AI Agent 助手(数据问答/分析为首发王牌),Python 双进程 + React SPA。品牌 docs/brand.md。
前身 `/Users/solvela/nl2sql-data-platform`(Java COLA,38 变更,仍在运行,**不动它**)。
知识资产已平移(41 域 spec → 本仓库 16 域 + roadmap 登记 22 域)。

## 环境事实(新会话最易踩的坑)

1. **本机是 podman,不是 docker**:`docker` 只是交互 shell 的 alias,非交互命令里
   `docker` 不存在——一律用 `podman`。
2. **标准端口**:api **8000** / agent **8001** / web dev **5173** /
   postgres **5432** / redis **6379**(均可配置,见「命令速查」)。
   **注(2026-08-17 晚实测):旧项目 nl2sql 已全面停用(容器与 vite 裸进程均已退),
   Kongming web dev 收回 5173**;两家联邦控制台回调均已按 5173 登记。
3. **本地环境冷启动**(幂等,任何"连不上/端口不对"先跑这个):
   ```bash
   make up                                 # podman compose 起 postgres+redis(默认端口)
   cd apps/api && uv run alembic upgrade head
   ```
   pg 数据持久化在 compose 卷 `kongming_pg_data`;删卷/重置属破坏性操作,须用户明确授权。
4. 三处 `.env` 已配好(不入库):根(占位密钥)、`apps/api/.env`(默认端口连接串 +
   `KM_INTERNAL_TOKEN=dev-internal-token`)、`apps/agent/.env`(同内部令牌;
   **LLM key 已配真值(005,借自上游项目 dashscope qwen-plus)**——冒烟/演示即开即用,
   注意勿提交、勿打印)。
5. 可能的后台进程:api(8000,带真联邦凭证)/agent(8001)/web(5173);
   另有 ngrok 固定域名隧道(baggie-armadillo-crave.ngrok-free.dev → 5173,演示用)
   与用户自建的 cloudflared 系统服务隧道(挂起 zone,无效但无害)。日志 /tmp/km-*.log;
   重启 Kongming 自身进程前先清理(如 `pkill -f "uvicorn kongming"`、
   `pkill -f "kongming/apps/web"`;ngrok/cloudflared 服务进程不属本项目,别动)。
6. 本地 dev 流程:基础设施(pg/redis)走 `make up`(podman compose 委托 docker-compose 二进制,
   已实测);两个 Python 服务日常 dev 直接 `uv run uvicorn ...`(compose 的 api/agent
   服务留给完整部署用)。
7. **浏览器实测**:ZCode IAB 面板 input 管道(click/press/screenshot)对本机 guest 会卡死
   (DOM 快照可读)——GUI 测试一律用 Playwright MCP 工具组
   (`mcp__plugin_playwright_playwright__browser_*`,独立管道,002 已验证可用)。

## 当前状态(017 已入库;001–017 全部归档)

- git:main 分支(以 git log 为准);无远程、未 push。
  分支策略:主干开发不拉 dev,协作按变更开短命 feature。
- openspec:**001–017 全部归档;活动变更为零**。
- **017 架构总览**:零表结构/零新权限点/零种子/零新依赖(fetch/
  StreamingResponse/asyncio 原生)。agent——graph/streams.py 新增(事件发射
  wrapper;**同步/异步节点双兼容**——validate/selfheal 是 def 不是 async def,
  wrapper 用 inspect.isawaitable 分流;**mypy 坑:stage_wrapper 返回
  Callable 别名标注会触发 add_node Protocol 联合的 Never 推断失败,函数对象
  可过——返回类型 Any 刻意擦除+注释钉住**)+build.py 挂 wrapper(nodes.py/
  prompts.py 一行未动)+main.py /query_stream(astream 双通道+终态构造
  _build_query_out 抽公共与 /query 共用;错误帧字段 snake_case reason_code
  (R2-R2:曾 camelCase 漂移)。api——chat/sse.py(SseDecoder 增量解码纯
  函数:注释行忽略/多 data 行拼接/data: 后单空格剥离)+agent_client
  query_stream(httpx client.stream 逐行;超时=读间隔语义非总时长)+
  parse_agent_reply 模块级(同步/流式共用防漂移)+ports StreamStore/
  StreamStoreFactory(独立会话落库单元:repo+history_writer+title_trigger)
  +service 前段 _prepare_query/终态 _finalize_reply/_finalize_unavailable
  全共用化(**同步 query 与流式唯一差异=谁调 agent+落库走哪个会话**;R2 逐
  字段对拍等价,曾丢 datasource_key 已修)+start_stream/_consume_agent_stream
  (后台 task+_BACKGROUND_STREAM_TASKS 强引用;except Exception 全收敛保证
  queue None 终止标记必达)+router /chat/query_stream(_relay_gen 生成器
  wait_for 心跳+_sse_frame;**事件 schema 进 OpenAPI 的手法:responses 的
  model 键放联合类型使 FastAPI 注册全部成员进 components+content 键手写
  text/event-stream oneOf $ref(此时 $ref 已有效)——直接 content 手写 $ref
  不会注册,实验直证**)+_stream_store_factory 组合根(get_sessionmaker
  独立短会话,010 同款)。web——api.ts streamChatQuery(dispatchSseBlock
  分发+**CR 剥除在 chunk 追加时(replace(/\r/g,""))消跨 chunk \r\n 分裂**,
  不能只在 block 内 normalize)+StreamCard(阶段标签七节点+selfheal
  「第 N 次自愈重试」+SQL 折叠 defaultOpen+打字机;role=status)+
  ChatPage(streaming/finalized/askError 三态+inOriginCtx 守卫+selfNavigatedRef
  自建导航豁免+取消闸依赖 [sessionId,current,datasource])。
- **017 R1 独立评审(1 严重+1 必修+6 建议全修)**:S1 后台任务异常逃逸
  (on_frame json.loads 畸形过程帧抛 JSONDecodeError→task 死亡→queue 无
  终止标记→前端永久等心跳;修=过程帧 try ValueError 跳帧+外层 except
  Exception 收敛 error 终态,直证用例 3 条)→B1 终态接管空白间隙
  (result 到达先清空后 refetch 补位;修=finalized 乐观快照 toSyntheticMessage,
  历史 refetch 命中 message_id 退位);R1-R6:spec 时序措辞统一「summarize
  完成后」/tasks 措辞对齐/错误收敛分支补测/切数据源补进取消触发点(实现
  超前于契约)/CRLF 分帧真兜底/事件名白名单。
- **017 /goal 审查循环(R2 六切口深挖,再清 3)**:S1 新会话首问死锁
  (useParams 无参数 undefined vs 流发起 sid ?? null,**null===undefined 恒
  false 全回调被丢弃**——发送按钮永久禁用;冒烟抓回与 R2 静态推演双路
  独立得出同结论;修=sessionIdRef 归一 null)→B1 datasource_key 回归
  (同步不可达分支重构丢失该字段落 default;修=_finalize_unavailable 补参
  +test_chat_datasource 非 default 数据源断言锁定)→B2 快照被取消闸清掉
  (finalizeOk 的 navigate 触发 sessionId 变化→取消闸 effect 清 finalized
  +Skeleton 分支拦截→接管间隙以骨架屏闪变复活;修=selfNavigatedRef 打标
  豁免+Skeleton 分支让位 finalized,时间线直证无闪变)。挂账:R3 断线窗口
  重进无「生成中」占位(spec 为终态后语义不违约);R4 _persist_touch_quietly
  失败回滚连带吞已成功 insert(mid 悬空,既有行为非 017 引入);O2 selfheal
  running 帧 retry_count 为进入时旧值(done 帧为本次序号,前端只用 done)。
- **017 冒烟(2026-08-21,真 LLM+Playwright MCP,9 项全过)**:curl——流式
  全链(12 stage 帧成对时序正确/sql 提前且与终态 SQL 逐字一致/summary/
  result 富化含 message_id 与 chart 推断)/REJECTED 走 result(BLOCKED+
  分态文案,4 stage=图拓扑)/断线续存(--max-time 1.5 断开收 5 帧无终态,
  12s 后 USER+ASSISTANT 双消息照常落库)/persist=false 零落库;UI——流式
  时间线直证(t=0 阶段1→t=800 阶段5→t=2800 阶段11+SQL 提前显示→t=5600
  阶段12+打字机→t=6000 终态表格接管)/终态完整卡片(图表 toggle/导出/
  SQL/执行计划/洞察/stats+标题 LLM 改写)/取消(流中切会话阶段归零无
  残留无错误)/B2 修复后接管全程无骨架屏闪变/**冒烟抓回 S1 死锁**(守卫
  日志直证 inOriginCtx 恒 false,浏览器端 fetch+reader 分帧复刻排除 vite
  proxy 缓冲嫌疑后定位 sid 归一)。016 评估 smoke 5/5 复验(同步链路零回归)。
  **MCP 操作经验:textarea 原生 setter+input 事件设值(React 受控组件
  fill 偶发不触发 state),流式中间态用 evaluate 内 setTimeout 轮询采样
  (MCP snapshot 单次往返太慢抓不到过程)**。
- **016 架构总览**:零端点/零契约漂移/零 agent 改动/零种子;新依赖仅 dev
  组 pyyaml(评估器 yaml 解析,不进产品)。api——evals/ 新目录
  (conftest/matchers/test_eval/五份 yaml/README,与 tests/ 平级不继承其
  建库 conftest);pyproject: testpaths=["tests"](默认收集隔离)+
  pythonpath=["."](tests 单测 import evals.matchers)+markers 三枚;
  评估器凭证 KM_EVAL_ADMIN_PASSWORD(缺失整层 skip)/KM_EVAL_API_BASE/
  KM_EVAL_ADMIN_USERNAME/KM_EVAL_PG_DSN(缺省控制面 DSN 派生);401 重登
  重试(access TTL 15min 全量超窗);畸形 yaml 显式 raise(R1-B2);
  治理=alembic 0007(去重+uq_query_history_tdq)+repository UPSERT
  (COALESCE 保旧向量)+cap 淘汰同事务+service 注释同步;import-linter
  零变化(evals 不在 kongming 包内,不受契约约束;evals→kongming 方向
  import 合法)。
- **016 R1 独立评审(0 严重;7 建议全修)**:B1 主 spec「评估匹配规则」
  未覆盖双路径实现→delta 补 MODIFIED(数值子集+天然无序+纯文本回退入约)
  已 sync;B2 畸形 yaml 静默缩分母→显式 raise;B3 rag service docstring/
  日志与 COALESCE 语义相反→措辞同步;B4 canon_cell 未归一 uuid.UUID
  (gold 侧 asyncpg 对象 vs gen 侧 JSON 字符串静默假阴性)→加分支+单测;
  B5 tasks 6.3 状态矛盾→勾选+注记;B6 proposal Impact 失实(service 零
  改动写成「repository+service 改造」)→对齐+补列 test_rag_writeback;
  B7 死代码 fixture smoke_cases→删。观察挂账:0007 去重无直证用例(测试
  基建全新建库无存量,review 论证+PG 语义背书);exec_sql 每题建连(66
  次本地握手可接受,statement_timeout 未设);matchers 模块双载形态
  (bare 名与 evals. 前缀各载一次,纯函数无行为差异);匹配器未测分支
  (严格真子集放行/bool 混排/date-time 归一);full 统计 expect_reject
  含 ±1 题偏置(strict 恒 False/recall 恒 True,README 已脚注)。
- **016 /goal 审查循环(R1 换切口自查+R2 独立深挖,清零)**:R1 自查核验
  3 真缺陷全修——①0007 唯一索引对超长中文问句炸(question 传输层 2000 字
  可 6000 字节超 btree 行上限 ~2704,裸列索引必炸;修=**md5(question)
  表达式索引**,去重语义不变;repository ON CONFLICT 同步 md5 形态——PG
  index inference 要求表达式逐字匹配,R2 三路径实证裸列/表达式失配 42P10;
  测试补 2000 字问句回写两次=1 行直证);②多轮 yaml 3 处裸 LIMIT 遗漏
  (单轮排序对齐修订漏改多轮,olist_mt_002 t1 实锤挂因;补 ORDER BY 后
  3/3 复验过,多轮口径 7/10→8/10);③expect_reject 分支传输层故障被当
  「拒绝」(服务未起时 deny 题假绿;httpx 对 4xx/5xx 不抛异常,真拒绝走
  状态码判定,except 只捕服务不可达——改为显式 fail)。R2 独立深挖(端到端
  数据流/并发时序/PG 语义边界/评估集语义/配置交互五切口)再修 3:D1
  KM_EVAL_PG_DSN 文档虚构接口(声明了但 conftest 不读,设置了静默无效
  →难溯源的 gold 错乱;修=实现,显式优先);D2 olist_mt_001 t2 子查询
  ORDER BY 触发保序判定而外层 GROUP BY 无序(HashAggregate 实现巧合掩蔽
  的结构性 flaky;修=外层补 ORDER BY;gold 验证脚本改**保序敏感**口径
  重验 63 条全确定性);D3 test_full_all 异常网捕不到 pytest.fail(Failed
  继承 BaseException 非 Exception,单题 gold 资产错误会中断全量统计;
  修=except 网补 pytest.Failed)。顺手:category 标签按「跳数=表数-1」
  口径修正 4 处(sakila_014/016、olist_015/016)。R2 边界确认 14 项
  (B1-B14:并发 UPSERT 终态/NOT IN NULL 语义/pytest 行为矩阵含
  `pytest tests evals` 混跑形态等)与 0007 裸列→md5 未换 revision 号的
  环境提示(未提交期修正,无已发布环境受影响)。终态 api 470+evals
  复验(multiturn 修复 case 4/4 过)全绿。
- **016 冒烟(2026-08-21,真 LLM)**:评估集两轮口径修订(首轮宽松 70%:
  明细题 gold 裸 LIMIT vs gen 必排序集合错位→问句显式指定排序键对齐
  prompt 规则 6;top-N 问句点名值列;DISTINCT 排序键进投影;NULL 类目
  语义界定)后定稿基线:smoke 5/5、full 宽松 37/44(84%)/严格 26/44(59%)/
  表命中 35/44(80%)/动态 4-4、multiturn 7/10;失败归因五类全为真实链路
  质量信号(未迁就);63 条 gold 连跑两次一致+≤1000 行(3 处超限题重设计);
  治理实库直证:curl persist=true 同题两问→query_history 行数=1(UPSERT
  幂等);评估器抓回并修 3 实现坑(settings→get_settings 形态/asyncpg
  cursor 事务外炸→prepare+fetch/评估 persist=false 不回写的语义核验)。
- **015 架构总览**:零表结构/零 agent 改动/零新依赖。api——audit 域包扩全
  (ports/repository/service/router/archive/context;writer/models 不动);
  import-linter 调整:datascope 单独保留禁 fastapi 契约,audit 新增
  models/service/ports 零框架契约(第 8 条);auth.repository SqlOperLogRepo.add
  读 client_ip_var 填 ip(contextvars 横切,BackgroundTasks 继承副本=发起
  请求 ip 合理);chat export_result 落 chat.export 审计(rollback 包
  suppress 防双失败逃逸);config 加 audit_archive_days/dir;归档锁=
  asyncio.Lock(单 worker 前提,多 worker 需 PG advisory lock 挂账);
  Dockerfile --proxy-headers --forwarded-allow-ips 内网三段。web——
  /admin/audit(双区 toggle-group 复用 013 组件;时间列 toLocaleString
  本地化;时间范围 date 筛选两区共享;详情行内展开完整 SQL/detail)。
- **015 R1 独立评审(5 严重+7 建议全修)**:S1 chat delta 缺失(导出审计
  条款会永久丢失)→补 delta 已 sync;S2 归档跳过分支不校验窗口覆盖
  (days 调小→90~180 天龄行未导先删)→manifest_cutoff>=当前 cutoff 才
  跳过+直证用例(首轮 180 窗→恢复→90 窗重跑必须重导);S3 归档无并发
  互斥(gzip "wt" 截断交错=文件损坏且删除后不可恢复)→asyncio.Lock+
  并发用例(gather 两跑=一真一幂等);S4 窗口命名与 spec 声明背离→
  改「导出日锚+跨日重复语义显式+恢复幂等去重」;S5 脱敏测试锁定
  未兑现→补用例(重置密码 detail 无明文无 password 键);B1 导出审计
  rollback 包 suppress;B2 lifespan cancel 后 gather 收割;B3 时间列
  本地化;B4 时间范围筛选补 UI(oper user 筛选挂账);B5 proposal ip 句
  改 contextvar 口径;B6 --proxy-headers;B7 冗余 import。观察挂账:
  system:audit:view 同点管查询与删数据/回灌(未来细分 archive 权限点);
  调度 UTC 03:00=北京 11:00(与低峰意图偏差,时区随部署评估);
  conftest ASGITransport 不跑 lifespan(归档调度路径仅冒烟覆盖);
  空窗口日产出空 gzip(微量存储);trace_id/action 输入无防抖;
  manifest name 字段理论可带相对路径(前提=已有文件系统写权限,无增益)。
- **015 冒烟(2026-08-21,Playwright MCP+curl,7 项全过)**:SQL 审计分页
  74 条历史+筛选+详情展开完整 SQL+trace_id;操作日志 ip 新请求
  127.0.0.1(存量 None 不回填)+perm_denied 行带 ip;chat.export 审计
  detail{fmt,rows,truncated};归档 run{exported:1,deleted:1}+两文件+
  manifest→restore restored=1→重跑 skipped_export=True 幂等;smoke-self
  sql/page+archive/run 403;UI 双区+时间筛选+导航守卫。
- **014 架构总览**:零表结构(反馈 8 列+索引 0001 baseline 已建全)/零 agent
  改动/新依赖零。api——feedback 域包(models 三态合并+sqlglot 抽表 CTE 排除/
  ports/service 零框架/repository 全参数化(CAST 形态防 asyncpg None 类型
  推断坑)/router 双前缀 /chat+/feedback);提交面 403 同形(spec 立约,
  与 chat 域 404 惯例分立);防火墙三函数复用零拷贝;writeback 复用 009
  通道(同 session 双 repository 时序安全:feedback 先 commit,writeback
  后 commit);行级可见性=repository bool_or(data_scope='ALL') 判定+
  service 强制覆盖 userId;种子 3 权限点+DEVELOPER 推荐绑定(种子断言
  精确到列表相等);import-linter 第 7 契约。web——setQueriesData 前缀
  ["messages"] 本地即时回显;FeedbackDialog 未触碰字段传 null 保持
  (commentTouched 判定);/admin/feedback(统计卡 5+趋势 token 色+筛选
  6 件+分页越界自愈+详情弹窗三区);「编辑知识」/admin/rag?table=<名>
  定位提示条+行高亮(零服务端变化)。
- **014 R1 独立评审(0 严重;4 建议全修+12 观察)**:S1 page/overview
  user_id/tenant_id 裸 str 直穿 CAST 裸 500→uuid.UUID 传输层 422;
  S2「编辑知识」裸链无参数→?table= 携带+RagPage 定位;S3 弹窗 comment
  空串恒提交静默清空既有留言→SessionMessageOut 纯增 feedback_comment
  回显+未触碰传 null;S4 请求体类型手写违先例→生成。顺手项:O1 处置
  限定已反馈 assistant(防幽灵行);O2 重复提交清空处置三列(防陈旧
  处置者);O3 parseError 改「部分失败跳过」spec 措辞;O10 工件失实
  三处(nav.ts→AdminLayout/「无 spec 变更」措辞/tasks 对齐)。观察挂账:
  O4 弹窗预填原 SQL 直接提交会重复沉淀(被 009 成功回写天然稀释,
  query_history 治理随 eval 域);O5 overview 滚动 24h 窗 vs trend 日历日
  口径微差(差 1 边界);O6 recent_user_question_before 严格小于 created_at
  (手插同刻数据漏问句,生产跨事务必晚);O7 insert+touch 同事务 commit
  失败时 mid 悬空(仅 DB 故障窗口);O8 行锁无真实并发捶打(PG 语义
  成熟);O9「初检」措辞 vs internal 分段(规则集合一致);O10③ days
  传输层 422 vs spec「钳 90」解读差(实现取 422);O11 REJECTED/FAILED
  消息 API 层仍可反馈(前端 SUCCESS 才挂入口,方向放宽无风险);
  O12 前端时间范围只作用统计卡不过滤明细表(API 参数前端未用)。
- **014 冒烟(2026-08-21,真 LLM+Playwright MCP,9 项全过)**:响应含
  message_id/点赞 setQueriesData 即时切换/DELETE correctedSql 400
  (CORRECTED_SQL_REJECTED: BLOCKED_ROOT)/正确 SQL 200+沉淀(embedding
  真向量)/相似问句召回 few-shot HIT 纠错 SQL/分析页全要素+处置保存
  ADOPTED 可见/涉及表 goods 定位+编辑知识跳转/smoke-self 提交/page/
  overview 全 403/fetch 润色+reindex 两次 {1,1,1} 幂等一致。R1 修复后
  复验:S2 定位提示条+行高亮;S3 弹窗预填「演示纠错」+未触碰提交
  库内留言保持+状态回 PENDING(O2 联动)。
- **013 架构总览**:零表结构/零种子/零新依赖;契约 diff 仅 RenameIn
  maxLength 200→2000(业务上限 200 不变、领域层 strip 后判定,传输层
  仅防离谱请求体)。api——chat/ports 新增 ChatRepo Protocol(15 方法+
  commit/rollback)与 ChatStoreError;ChatRepository 写路径 6 方法+commit
  经 `_store_error_boundary` 装饰器(PEP 695 泛型,Coroutine 返回型与
  Protocol async def 结构化匹配——Awaitable 宽形态会被 mypy 判不兼容)
  转译 SQLAlchemyError;ChatService 构造 session→repo 注入,零 sqlalchemy
  import,import-linter chat 契约补 service 与三域对齐;rename strip 后
  >200→InvalidTitle(400),RenameIn maxLength 放宽 2000。rag——
  AgentRagClient 实例 lazy AsyncClient 复用+模块级单例工厂
  get_agent_rag_client(chat/rag 两 router 组合根同调)。agent——
  rag_retrieve 200 非 JSON try ValueError 收敛 None(与 describe 形态对称)。
  web——chart-view formatTemporalLabels 数据层截断(全列 ISO
  `YYYY-MM-DDTHH:mm` 探测,任一不匹配整列放弃;时间非全零附加 HH:mm;
  pie 切片名同源截断为有意,R1-S2 已钉文档)+scatter tooltip formatter
  (列名+数值双行,unknown 窄化);session-sidebar AlertDialogCancel
  disabled={busy}+AlertDialog onOpenChange 闸(busy 期拦截一切关闭源,
  R1-S1:ESC 键盘路径等价绕过,按钮 disabled 不够)。
- **013 R1 独立评审(0 严重;3 建议全修+4 观察)**:S1 busy 期 ESC 仍可关
  删除对话框(spec 立约语义被键盘等价路径绕过)→onOpenChange 闸,浏览器
  实证 busy 窗口 ESC 派发 9 帧保持 OPEN、删除完成才关;S2 pie 被
  formatTemporalLabels 波及(design「不受影响」失实)→文档改「pie 切片名
  同源截断(有意)」;S3 get_agent_rag_client docstring「测试不经本工厂」
  失实(TestClient 经组合根触达,类级补丁/连接失败兜底恒空池)→如实改写+
  _get_client 钉单 loop 前提(成功建连的新测试须复位单例槽)。观察挂账:
  O2 rollback 不转译后外层 except 少一次兜底(终态同 500,仅记录);
  O3 formatTemporalLabels 时间判零只看 HH:mm(秒信息丢弃,类目轴按秒
  取数极罕见)。核过无异常面:Protocol 17 成员逐一镜像/转译覆盖完备/
  构造点零漏改/temporal 三重形态(date 无 T 原样/timestamp/timestamptz
  截断)/退化点图 tooltip 立约字面满足/测试锚点(无 xdist 串行安全)。
- **013 冒烟(2026-08-21,真 LLM+Playwright MCP,6 项全过)**:temporal 轴
  fiber 直证 xData=「2005-07-05」无 T 后缀(line)/scatter tooltip
  formatter 输出「length: 183<br/>rental_rate: 4.99」(挂载 function+
  数值散点 value 轴+样点 [183,4.99])/删除 busy 窗口 10 帧采样「再想想」
  恒 disabled(fetch hook 延迟 1.5s 注入)/curl 重命名带空白 201 字→
  200 strip 落库+裸 201 字→400/问答全链路 4 轮真 LLM SUCCESS
  (chat.service 反转后首验)/rag 召回 history_sql 命中(单例后)。
  **MCP 操作经验**:textarea 设值须 focus() 后 Enter 才进发送键路
  (evaluate 设值+MCP press Enter 未聚焦会落空,点发送按钮更稳);
  短 busy 窗口采样须 fetch hook 延迟注入,本地 API<25ms 直采必错过。
- **011 架构总览**:零表结构/零新端点/零新权限点/零种子/**零契约漂移**
  (chart.type 契约 schema 为自由 string,值域扩 scatter 在 schema 内,
  make contracts KEPT)。api suggest_chart 补 scatter 分支(首列数值+有
  第二数值列→scatter,x=首列 y=首个数值列;numeric_cols 非空前置已保证,
  「首列数值且无第二数值列→none」由既有 early return 覆盖,既有
  temporal/pie/bar 规则逐字未动);web chart-view 重写:四类型
  toggle-group(shadcn 新组件,radix-ui 聚合包**零新依赖**)+PNG 下载
  (getDataURL pixelRatio=2,backgroundColor=cssVar(--card) 防透明底,
  「图表-yyyyMMdd-HHmmss.png」,锚点入 DOM 再 click 的 Safari 形态)+
  scatter 双形态(x 列全数值探测**显式排除 null/undefined/''**——JS
  Number(null)===0 误判坑;value 轴遇 NaN 静默丢点为上游实证坑,一行脏
  即整体退化类目点图);切换=卡片本地 useState,映射恒定,刷新/同卡片
  新结果回落建议类型。新会话冒烟留痕:sakila 域「列出 30 部电影的时长和
  租金」(scatter)/「各分级的电影数量」(pie)/「2005年7月每天的租赁次数」
  (line)/「60 位客户的姓名和邮箱」(none)等 4 个新会话可回复核。
- **011 自查修 2 缺陷**:①回落 effect 依赖写成外层内联组装的包装对象
  ([chart.type, data]),父组件无关重渲染(输入框逐键)会误重置手动切换
  ——改依赖基元+react-query 缓存内稳定数组引用(structural sharing 保证),
  浏览器实证「切 pie 后打字不重置」;②数值散点 y 空值 Number(null)===0
  会落 y=0 假点——映 null 由 echarts 跳过该点。
- **可读性热修根因链(冒烟实证,重要操作知识)**:「列出30部电影的时长和
  租金」出不了 title 的元凶是**rag few-shot 自我强化**——同题历史成功 SQL
  (query_history)+ctx 缓存(km:ragctx:* 10min)喂旧形态范例压过 system
  prompt 规则,且每次旧形态成功又回写更多旧范例;清场(DELETE 同题
  query_history 行+DEL km:ragctx:*)+规则提级(「例外」措辞→主规则
  「必须」,反例区补两条)后生效:title+ORDER BY film_id+洞察点名电影
  (ACE GOLDFINGER…)+scatter 正确取双数值列(title 前置不误判)。新形态
  回写后 few-shot 转为正向强化。**prompt 类改动冒烟必须先清同题历史+ctx
  缓存,否则验证的是缓存不是 prompt**(009 挂账③ query_history 治理的
  具体表现)。另:编辑 prompts.py 单行字符串时换行必须是字面 `\n` 转义,
  插实际换行即 SyntaxError(现场修过)。
- **012 架构总览**:api——system service 专用 enable_user/disable_user
  (保护与编辑路径同构:自禁/超管禁 409、404;审计 user.enable/disable;
  **禁用即吊销 refresh 家族**——收口 service 单点,专用端点与 update_user
  status→DISABLED 分支两路同调,复用 007 改密吊销管线);repo 加
  set_user_status(只翻状态列);ports Protocol 同步。web——shadcn switch
  引入(token 面 primary/input/background/ring 齐备,零新依赖)+
  `components/status-switch.tsx` 公共组件(受控 checked=服务器真值、
  pending 禁用、失败回弹+行内 role=alert)+UsersPage/DatasourcesPage
  状态列统一接入(数据源页旧行内文字按钮与全局错误横幅删除,错误改行内)。
  契约 +2 端点(POST /system/users/{id}/enable|disable)已重生成。
- **012 冒烟(2026-08-20,Playwright MCP)**:用户页 7 开关渲染 aria 语义
  正确/禁用 smoke-self→真值 unchecked/启回→true/**自禁 409:开关回弹+
  行内「不可禁用自己的账号」可见**/数据源页开关统一形态、旧按钮移除、
  off→on 闭环/编辑对话框 Select 保留。禁用吊销 refresh 由 api 测试直证
  (disable 后旧 refresh 401,R2 后补判别步:enable 后旧 refresh 仍 401)。
- **R2 独立评审(全覆盖三工作包,0 严重;6 必修+8 观察全闭环)**:
  F10 service 抽 `_assert_disable_allowed`/`_revoke_refresh` 单点(保护与
  吊销改密/重置/禁用三路同调,防两份漂移);F3 吊销测试补判别步——refresh
  有 is_active 闸,仅断 401 对「删掉 revoke」不设防,enable 后仍 401 才锁住;
  F1 scatter 侵占双度量 pie/bar 形态([分类,count,avg]→scatter)为列序
  解耦既定行为,**裁决=接受**(切换器兜底+规则简洁),补测试+proposal/spec
  优先级句(temporal>scatter>pie/bar);F5 单行 pending 并发竞态(onSuccess
  清单槽致后来者错误静默)→ 恢复全局互斥;F6 错误文案 aria-describedby
  关联(id 空白折连字符);F4 审计清单测试实调两端点+扩 expected;F11 401
  参数化直测;F7 prompt 补「用户显式限定只要某些列不加强加」豁免;F2
  docstring 措辞;F8/F9 工件失实回填(prompt 热修锚进 011 Impact、tasks
  与实现对齐)。核过无异常面:update_user 吊销条件全矩阵(ACTIVE→ACTIVE
  带密/无密、DISABLED 重复编辑幂等)/软删过滤/契约与生成物一致性/开关
  受控语义/prompt 字符串完整性与聚合问法零误伤。浏览器复验:自禁 409
  回弹+错误可见+aria 链接解析正常。
- **011 R1 独立评审(作者/评审分离,0 严重;3 必修+2 边角+2 文档全修)**:
  数值散点双 value 轴补 scale:true(echarts 默认含零轴,窄区间远离 0 的
  数据挤角,冒烟数据恰好掩盖);isNumericCell 补空白串拦截(Number("  ")
  ===0 与 "" 处理不一致);design/tasks/proposal 回填自查修复后的真实形态
  (依赖数组+assistant-card 实为零改动);None-any 用例补 x/y 断言;HANDOFF
  头部 010 遗留重复行顺手清。核过无异常面:回落 effect 依赖推演(structural
  sharing 链路实证)/notMerge+deep-equal 挡板(pie↔笛卡尔互转零残留)/
  suggest_chart 分支互斥性(scatter 吸收列序解耦后对原 none 与双度量
  pie/bar 形态均生效,优先级 temporal>scatter>pie/bar 已进 delta spec)/
  bar/line/pie 与 006 版逐项行为等价/契约自由 string 实查/toggle token 面
  与 radix 单轨零新依赖。修复后浏览器确定性回归:scatter 会话 fiber 直证
  xAxis value+scale:true+数据 [86,0.99];NULL 会话 fresh load=饼图(建议
  回落)→切散点退化 category 轴 10 点→输入框打字不重置。**MCP 操作注记:
  评审中出现的 URL 漂移/切换状态残留读数系 HMR/Fast Refresh 竞态伪象**
  (app 无「恢复上次会话」重定向路径,ChatPage 仅 404 收敛与提问成功两处
  导航;Fast Refresh 会跨热更新保留组件本地 state——评测量化务必以
  fresh navigate 后的读数为准)。
- **011 冒烟(2026-08-20,真 LLM+Playwright MCP,11 项全过)**:双数值
  出 scatter 默认(网络实证 chart.type=scatter)/四类型来回切换映射恒定/
  rating 类目切 scatter 退化点图不空白/original_language_id 全 NULL 切
  scatter 正确退化(10 点分散、无 x=0 挤压,真区分 null 探测)/PNG 下载
  (中文时间戳文件名,1436×520=2 倍密度,白底)/切 pie 刷新回落 scatter/
  pie·line 推断零回归/60 客户明细 none 无图表区/切换器激活态·聚焦态
  视觉/切换后打字不重置/历史会话回显渲染。**环境坑:冒烟中途 podman
  machine 停机致 pg/redis 断连**(旧 api 进程 healthz 假 200——healthz
  不探 DB),冷启动流程 podman machine start → make up → 重启 api/agent
  即恢复;5173 假占用同理=vite 仅监听 [::1] 且挂起,pkill 重启即回。
- **010 架构总览**:零表结构变更/零新权限点(复用 chat:execute)/零种子变更。
  新端点 4:`POST /chat/explain`(message_id 引用,**优于上游**:不收裸 SQL,
  被分析 SQL 恒为落库 agent 产出;服务端复检后才拼 `EXPLAIN (FORMAT JSON)`,
  防火墙零改动、永不 ANALYZE;数据源取会话落库 datasource_key 快照)、
  `POST /chat/export`(columns+rows 不重跑 SQL;CSV=UTF-8 BOM+CRLF+RFC 4180+
  **公式注入中和**;xlsx=表头加粗+自算列宽东亚宽字符按 2 计;rows 传输层
  max_length=10000 解析期拒,service 截断双闸)、`PATCH /chat/sessions/{id}`
  (原子归属 UPDATE)、`GET /chat/sessions?q=`(标题 ILIKE OR user 问句
  EXISTS,ESCAPE 字面化)。agent 新端点 `POST /internal/rewrite-title`
  (009 图外代理范式;prompt 全新撰写,上游无此资产)。
- **010 关键实现决策**:internal/service 前段(租户→数据源→绑定→初检→改写→
  复检)提取 `_prepare` 供 execute/explain 共享(防线单一事实源,不复制安全
  逻辑);chat 域 ExplainRunner 端口反转 + router 组合根 `_InternalExplainRunner`
  (chat.service 不可间接 import internal——携 sqlalchemy,007 uuid7 同款约束);
  标题改写=首个 SUCCESS 轮 BackgroundTasks(独立 sessionmaker 会话)+ 守卫读
  (当前标题≠首问截断值短路省 LLM 调用)+ 条件写(WHERE title=expected,
  人工重命名永不被盖;不刷 updated_at 防顶列表);`_like_pattern` 上移
  kongming/like.py(system/datasource/chat 三域共享,消两份拷贝)。
- **010 R1 双路评审(0 阻断 0 严重,8 建议全修)**:api——导出 rows 传输层
  上限(原截断在解析后挡不住请求体)、xlsx 控制字符 IllegalCharacterError
  裸 500(ILLEGAL_CHARACTERS_RE 剥除)、CSV 公式注入中和(=+-@\t\r 前置 ',
  数值不中和)、title_tasks 死参数删除、_Prepared.engine 宽放改 AsyncEngine、
  孤代理 encode errors=replace;web——执行计划失败永久锁死加重试钮、
  RenameDialog 补 DialogDescription+role=alert、搜索换词骨架闪屏改
  placeholderData、downloadBlob 锚点入 DOM+延迟 revoke(Safari 坑)、
  formatPlanNode 非数值 cost 不渲染 NaN。观察挂账见「已知 TODO」010 段。
- **010 /goal 审查循环(R1 换切口自查+R2 双路深挖,清零)**:R1(契约对拍/
  数据语义边界/并发推演)零新发现(契约 q maxLength 在 anyOf 内/参差行不炸/
  反斜杠字面 0 命中/q 超长 422 逐项实证)。R2 两路:上游/spec 对拍路 3 建议
  (titler max_tokens≤60 承诺未兑现——LlmClient.chat 加形参 titler 传 60;
  条件写竞态落败无直证——补 RacingAgent 回调内改名任务级用例+repo 行数用例;
  tasks/design 文本漂移 3 处回填)+8 观察挂账;时序/生命周期路 1 建议
  (**标题后台任务持控制面池连接跨 30s LLM 调用,并发 ≥10 可耗尽 pool 5+5
  连累无关端点**——修=守卫读短会话释放、LLM 池外等待、回写另开短会话;
  探针实证 checkedout 1→0)+4 观察(双调 LLM 窗口条件写兜底无害/docstring
  口径/计划跨会话重拉/租户切换保留搜索词语义自洽)。探针实证:预校后禁用
  窗口 400 优雅/evict 对飞行中连接无影响(SQLAlchemy dispose 语义)/审计
  失败不影响响应(与 execute 同构)。
- **010 冒烟(2026-08-20,真 LLM,curl 7 项+UI 8 项全过)**:发问→截断标题→
  首个 SUCCESS 后 LLM 改写回写(「sakila 里有多少部电影」→「sakila数据库电影
  数量」,查库实证)→重命名(去空白)→次轮人工标题不被覆盖→搜索(标题词/
  问句词/通配符 % 字面化)→导出 CSV(BOM efbbbf+CRLF+Content-Disposition
  时间戳中文名)+xlsx(openpyxl 读回表头加粗)→EXPLAIN(计划树 Aggregate
  cost=13.76;审计 EXPLAIN 形态;他人消息 404)→诱导删除 LLM WHERE FALSE
  规避(数据无损,已知行为)。UI:分组(今天/昨天/近7天)/搜索过滤/菜单
  重命名(对话框 Enter 提交,侧栏即时更新)/删除受控确认开合/计划懒加载
  渲染/CSV 下载(服务器文件名)。**UI 冒烟抓回 1 共享组件真缺陷**:
  scroll-area Radix display:table shrink-to-fit 把 CJK 长标题行横向撑出
  视口(图标/组标全挤出可视区)——Viewport 加 [&>div]:block! 全局修复,
  复验全绿。**Playwright MCP 操作经验更新**:Radix 菜单项选择=trigger 派
  pointerdown/up 开菜单 → page.evaluate 里 DOM click() 菜单项(direct click
  与纯 pointer 序列都选不中)。
- **010 测试面**:api 379(+46:explain 11+export 16+搜索重命名 12+title 7);
  agent 85(+12 titler);conftest 无新增清理表(零表结构变更)。
  **测试序坑实证**:模块级全局只读池——新测试模块真建池后须 teardown
  dispose_read_engines(),否则 test_datasource_admin「不建池」断言被污染
  (test_chat_explain 已接线)。
- **009 架构总览**:rag 两表为 baseline 既有资产(0001 已建 rag schema/四元
  唯一键/HNSW+GIN),0006 只做增量(source_type CHECK 换五枚举+召回辅助索引,
  ALTER 型,空表瞬时)。信任边界方向:api 持 pgvector 与全部编排,agent 持
  LLM key,api→agent 回调新增 /internal/embedding(批量 texts≤64)+ 
  /internal/describe-table(润色,enriched=False 即降级)两通道。
- **009 api 侧**:域包 kongming/rag/(models 纯函数含 RRF k=60/top5/优先级/
  提示构造、parser `## 表名` 切块、ports 端口反转、repository SQL+全量内省、
  agent_callback httpx+emb 缓存(km:emb:v1:* 24h)、cache ctx(km:ragctx:* 
  10min SCAN 精确失效)、docparse pypdf/python-docx(to_thread)、service
  零框架编排、router)。端点:/system/rag/* 5 个(documents/upload 多部分、
  tables 列表/删除、fetch-metadata、reindex;全部 system:rag:manage+租户
  白名单复校)+ /internal/rag/retrieve(内部令牌,三路召回双过滤)。
  种子权限 system:rag:manage 排序 5(SUPER_ADMIN 覆盖/DEVELOPER 不推)。
- **009 召回与回写**:retrieve 节点召回优先(resolved_question 召回)、零命中/
  失败回退 005 实时内省(新租户零回归);generate user 模板追加「参考历史成功
  SQL」段(系统模板资产零改动);chat 最终 SUCCESS 判定后回写 query_history
  (原始问句,独立 try);embedding 失败降级全文路/无向量落库;**空结果不写
  ctx 缓存**(冒烟实证:降级空结果入缓存会让恢复后服务 TTL 窗口内持续空)。
- **009 sqlfw 拒读扩容(评审 B2)**:表引用显式 schema 限定名 db=='rag' 即拒;
  两表末段名不进保留清单(rag 不在任何执行 search_path 内裸名无通路,
  query_history 是常见业务表名防误伤)。CTE 体内限定名/引号/JOIN 夹带全拦。
- **009 web 侧**:/admin/rag 第五子页(列表/数据源筛选/分页/上传/删除受控
  确认/抓取元数据/重建索引受控确认+结果展示);api.ts 加 postForm
  (FormData 不设 Content-Type——浏览器须自动生成 boundary,手设 JSON 头
  曾致 multipart 全字段 422,冒烟实证);lib/rag-api.ts 封装。
- **009 冒烟(2026-08-20,真 LLM,12 项全过)**:fetch-metadata sakila_dyn
  28 表 40s 全润色(AI_GENERATED,中文说明+类型前缀+GROUP BY 示例)→三路召回
  (「有多少部电影」film_list 居首,润色说明进提示)→问答全链路 PASS 1000 部
  3.2s(提示来自召回非内省)→回写闭环(「电影总数是多少」召回首问 SQL few-shot)
  →rag 拒读(`SELECT … FROM rag.query_history` 诱导 BLOCKED)→上传幂等→
  reindex(29 删 28 建)→优先级不误伤→smoke-self 越权 403×3→UI 链(列表/
  重建确认/删除受控/上传闭环/直达 403)。
  **冒烟抓回 3 真 bug 已修**:①dashscope text-embedding-v2 单批 25 条上限
  (28 条整批拒→llm.embed 内部按 25 分批);②tenant_id UUID vs str 恒不等
  (合法动态源被召回预检恒拒——负向测试(期待 400)掩蔽,加正向用例);
  ③前端 doFetch 强设 JSON Content-Type 破坏 multipart。
- **009 提案评审(前轮)+实施复审(后轮)清零**:前轮 B1(rag 两表 baseline
  既有,0006 改 ALTER 增量)+B2(rag 拒读缺口)+S1-S6/R1-R8 全落工件;
  后轮 S1(agent_callback resp.json() 异常收敛)+S2(表名 128 上限 400)+
  S3(PDF/Word 零测试补齐:代码构造 docx/损坏 pdf/超 10MB)+S4(generate
  历史段断言)+S5(ctx 失效范围双租户测试)+R1-R7 全修;O 级观察 5 项
  (datasource⇄rag 包级双向依赖为适配层组合、writeback 同步阻塞、fetch
  TOCTOU、内省列注释未进润色、embedding 单条长度)挂账见下。
- **009 /goal 审查循环(R1 自查+R2 独立深挖,清零)**:R1 换切口自查抓
  **3 处前端真实缺陷**(均为 006/007 同类教训再犯):删除末页条目分页越界
  无自愈(useEffect 补)、删除确认失败错误被 modal 遮罩挡(对话框内渲染
  isError,007 形态)、**删除对话框 open 表达式并入 !remove.isSuccess 致一次
  成功后永远打不开**(react-query mutation 状态在下次 mutate 前保持 true;
  冒烟因每轮重新导航掩蔽——open 只看 target,成功关闭由 onSuccess 收口);
  浏览器实证连续两次删除正常+越界自愈。R2 独立评审(数据/SQL 语义+并发
  时序+前端状态机切口)抓 S1-S3:**S1「表说明零命中但历史命中」形态被
  agent 整体丢弃**(知识未抓取但 query_history 有记录的真实场景 few-shot
  永不生效;修=history_sql 独立于 schema 命中透传,内省回退与历史段共存,
  测试反向断言一并纠正)、S2 上传失败后 file input 值残留同文件无法重试
  (onChange 读值后即清)、S3 中文 simple 全文路局限未声明(design D3 补
  声明+zhparser 挂账);R1' emb 缓存维度漂移程序防线(AgentRagClient
  维度断言,漂移按未命中/降级不再 500);O5 remove.onError 清 notice;
  测试盲区补 3 直证(空结果不写缓存 redis 键数/纯 history 形态入缓存/
  坏维度缓存重取)。R2 核验清零区含 pgvector 科学计数法解析(strtof 支持
  e 记法,源码核验)、chat 时序全推演、gather 并发安全等。
**009 测试面**:api 333(防火墙 rag 4+种子断言随权限点+parser 纯函数 18+
  管理端点 28+召回/回写 18);agent 73(+纯历史形态);conftest TRUNCATE 增 rag 两表。**008 及更早状态见 git log 与下文(保留)**。
- **008 api 侧**:新域包 `kongming/datasource/`(models/ports/service/
  repository/cipher/tester/router,宪法分层;import-linter 新增
  「datasource models/service/ports 零框架」契约,并修正 auth 防线
  forbidden_modules 猜测名 datasource_admin→真实包名 kongming.datasource)。
  端点 8 个挂 `/system/datasource/*`(独立 router,非 system 域包):
  page/create/update/enable/disable/delete/test 挂
  `system:datasource:manage`(种子排序 4,SUPER_ADMIN 自动覆盖),
  **options 仅登录**(用户面,X-Tenant-Id 白名单复校与 chat 同构、
  复校查询 datasource repository 自备;首位合成 default 项非 sys_datasource 行)。
- **008 凭证加密**:AES-GCM(cryptography),密文=base64(iv12‖ct+tag16)
  与上游逐字节同构;密钥 KM_DATASOURCE_ENCRYPT_KEY(base64 16/32 字节,
  prod fail-closed 已在);**dev 缺省降级=代码内固定 dev-only 密钥+构造告警,
  仍走真 round-trip(优于上游 dev 明文直通)**;.env.example 注记生成方法。
  DTO/日志/审计永不出现明文或密文;编辑密码留空=COALESCE 路径不触碰该列。
- **008 动态池与路由**:executor 放开任意 key——get_or_create_engine
  (模块级 asyncio.Lock 防双池)/evict_engine 定向失效(pop+dispose)/
  dispose_read_engines 由 **lifespan teardown 接线**(此前零调用点)。
  internal/service 移除 SUPPORTED_KEYS 硬编码:非 default 校验存在+启用+
  归属(同形 400 DATASOURCE_INVALID 防探测,审计落 BLOCKED+NOT_FOUND/
  DISABLED/CROSS_TENANT 分类内部可溯),解密→URL.create 组 DSN(密码转义
  由 SQLAlchemy 负责)→动态池;防御层与默认路径逐行同构(search_path=
  default_schema+public 事务级、data_scope 探测集与 schema 边界集同取
  {default_schema, public}、保留表/危险函数/截断全同);密文不可解归 ERROR
  (DATASOURCE_CREDENTIAL_UNAVAILABLE,不暴露细节)。meta.py 同构动态化
  (限 default_schema 单 schema 内省)。chat 入口 DatasourceChecker 端口
  预检(伪造/禁用/跨租户同形 400,不建会话不调 agent)。
- **池生命周期**:连接信息(host/port/database/username/password/
  default_schema)任一变更 commit 后定向 evict;仅改 name 不动池;
  启停/删除均 evict(禁用即拒,启用后按最新信息惰性重建);evict 失败仅
  告警不阻断;多 worker 跨进程失效挂账(design Open Questions)。
- **注册边界**:key pattern `^[a-z][a-z0-9_]{1,63}$`+保留字 default 禁注册
  +default_schema PG 标识符校验,三者在**领域层**判 400(spec 口径;
  查询面 chat/internal/meta 传输层 pattern 收口,畸形 key 422)。
  key+租户归属创建后不可改(编辑须提交原值否则 400);创建租户归属超管
  显式指定(存在且 ACTIVE),隐式上下文不生效;管理端点全局视角。
  删除=物理删除(硬 UNIQUE 随行释放,key 可再注册)。
- **008 web 侧**:/admin/datasources 第四子页(列表/搜索/分页/创建编辑
  对话框——key+schema+租户禁改回显、密码留空提示、对话框内测试连接、
  启停行内按钮、删除受控确认);对话页数据源切换器(datasource-store
  km.datasource 单源,options 按租户 react-query,切租户 calibrate 重置首项,
  切换=回新建态+失效会话域查询);ChatPage 提问携带 datasource_key、
  react-query 键含数据源维度;api.ts 头注释已修订(options 例外消费方);
  登出/会话终结同步清 km.datasource。
- **008 冒烟(2026-08-19,Playwright MCP + 真 LLM,全过)**:curl 链——
  注册(查库验密文非明文+可解)→测试连接(编辑态留空密文补位)→
  options(首位合成 default)→/chat/query 动态源真 LLM 全链路
  (「有多少部电影」命中 tenant_sakila,1000;meta 动态内省供 retrieve)→
  禁用即拒 400→改错密池失效重建 ERROR→改回 PASS→删除 400→
  sys_oper_log 五动作可查。UI 链——管理页 CRUD/测试连接(创建态+编辑态
  补位)/启停 badge 翻转/删除受控确认;切换器选中 sakila_ui 发问,
  网络实证请求体 datasource_key=sakila_ui,真 LLM 答 200 演员;
  切租户 km.datasource 自动重置 default;smoke-self:管理入口不可见+
  直达 /admin/datasources 403 品牌页+选项仅 default。越权面 curl 复验:
  保留字 default 400/schema 注入 400/跨租户 key 400/非超管 403+
  perm_denied 审计落库。**冒烟形态=同实例异 schema 假动态源**
  (design Risks 钉住;语义合法——search_path 首段切换)。
  **Playwright MCP 点击坑(实操经验)**:react-query 失效重渲染后快照 ref
  过期,MCP click 静默落空——改用 browser_evaluate 派 PointerEvent
  (Radix 下拉/菜单项走 pointerdown,element.click() 无效)。
- **008 测试面**:api 273(+68):cipher 9+管理端点 28+动态执行 11+meta 动态 4
  +chat 预检 4+seed 断言随新权限点更新;测试库动态源=同实例异 schema
  (kongming_test 的 ds_* schema,模块级建删);conftest TRUNCATED 增
  sys_datasource。agent 53 零改动(透传链 005 已就位)。
- **008 审查循环(/goal R1 双路+R2 复审,清零)**:R1 api 路 1 严重+3 建议全修——
  S1 options 缺省租户解析与 chat 不同构(创建序 vs 授予序,多租户下选项选中
  即被 chat 预检误拒;修为 ut.granted_at 逐字同构+错序回归测试);S2 领域层
  pattern 用 re.match 被尾换行绕过("default\n" 可注册;改 fullmatch,查询面
  pydantic Rust regex 本无此洞,参数化用例补齐);S3 非法密钥请求期才 500
  (lifespan 启动试构造拒启,TestClient 实证+防删行测试);S4 tester finally
  close 二次异常顶掉失败结果(包 try/except)。web 路 0 严重+2 建议全修——
  行内启停失败静默(toggleError+role=alert);options 拉取失败兜底误标
  (isError 分支「选项加载失败」)。会话跨源追问确认为 spec 未立约域,守
  「禁擅改对外契约」只修注释去过度承诺+挂账⑤。R2 复审抓回:S1 回归测试
  弱断言(!=400 曾被 403 假放行)收紧 ==200,连带暴露 dependency_overrides
  传类被 FastAPI 解析 __init__ 签名的 422 坑(零参工厂修正)+用户缺
  chat:execute 的吊具缺口;chat 侧 ORDER BY 补 t.id tiebreak(两侧逐字同构);
  dispose_read_engines 单失败不跳过后续;0005 补 upgrade 前置注记。
  O1(create TOCTOU)核验为既有 IntegrityError handler 已覆盖 /system 前缀
  归 409,非缺陷剔除。api 273+agent 53+web 绿。
- **007 api 侧**:新域包 `kongming/system/`(router/service/models/ports/
  repository,宪法分层;import-linter 契约「system models/service/ports 零框架」)。
  端点 16 个全挂 require_perm(用户/部门/租户=system:user:manage,
  角色/权限清单=system:role:manage);领域错误 router 统一映射
  (404 不存在/409 冲突保护/400 参数)。
- **内置数据保护(007)**:内置角色(SUPER_ADMIN/DEVELOPER 按 role_key 集合
  判定)禁删;role_key 全局创建后不可改(编辑须提交原值否则 400);
  超管账号(按 settings.admin_username 判定)禁删+禁禁用+禁剥 SUPER_ADMIN
  绑定(评审 R1 扩展:防锁定/接管向量);自删/自禁 409;角色删除前置校验
  「无未软删用户绑定」;部门删除前置校验「无子部门且无未软删用户」;
  部门编辑防成环(新父落在自身后代集合内即拒;「父设为祖先」是合法树上移
  ——实现初版方向写反被测试抓回,公式见 service 注释)。
- **role_key 契约(007)**:传输层 pattern `^[A-Z][A-Z0-9_]+$`(评审 R4:
  拒空白/小写近形变体,防社工混淆;保护本身按精确字符串判定,变体无提权面)。
- **审计(007)**:管理面全部写操作(9 类动作 user.create/update/reset_password/
  delete、role.create/update/delete/bind_perms、dept.create/update/delete)
  与业务同事务落 sys_oper_log(组合 auth 域 SqlOperLogRepo,同会话=同事务);
  require_perm 403 落 perm_denied 审计(rbac spec 立约补齐;审计写失败
  仅日志不阻断拒绝)。动作清单测试全量断言锁定。
- **0004 迁移(007 实现期发现)**:baseline 的 username UNIQUE 全表硬约束
  与 spec「软删过滤唯一」矛盾(软删记录仍占名);换成部分唯一索引
  `WHERE deleted_at IS NULL`。连带修复:auth 仓储 get_by_username 补软删过滤
  (同名双行曾会炸 one_or_none)。角色/部门为物理删除(无软删列,
  删除有前置校验+级联清绑定+审计留痕兜底)。
- **uuid7 拆分(007)**:kongming/uuid7.py 纯函数模块,db.py re-export
  维持既有 import 路径——system service 受 import-linter
  「不间接 import sqlalchemy」约束(db.py import sqlalchemy 链条曾违规)。
- **编辑回显字段(007 冒烟抓回)**:用户列表项含 dept_id/tenant_id
  (租户经 LATERAL 取 tenant_key 最小的一条——管理面单租户绑定形态);
  缺失曾致编辑对话框静默清空部门绑定(前端回填不了),api+web 已修,
  测试断言锁定。web 侧配套:角色清单未就绪时禁止编辑提交
  (否则 roleIds 空集覆盖会静默清空角色绑定,评审 R3)。
- **并发唯一冲突兜底(007)**:username/role_key 先查后插窗口命中唯一索引
  → IntegrityError 由 app 级 handler 收敛 409(评审 R2),不再 500。
- **007 web 侧**:/admin 管理区(AdminLayout 子导航按权限过滤——
  与 nav.ts 同构 hasPerm,无权者直达子页只见 403 品牌页不见子导航);
  三页:用户(表格/分页/关键字 ILIKE 搜索/创建编辑对话框——用户名创建后
  不可改、角色多选、部门树形下拉、状态启停/重置密码/删除确认)、
  角色(表格+内置 badge+删除禁用/创建编辑——roleKey 禁改/dataScope/
  sqlScope 下拉/权限绑定面板按命名空间分组复选,覆盖式保存)、
  部门(树形+折叠+加子级/编辑防环/删除前置校验兜底)。
  新 shadcn 组件 table/dialog/select/checkbox(语义类已核:tw-animate-css/
  popover/input token 齐备);api.ts 加 put;lib/system-api.ts 封装
  (类型全生成)。部门树缩进用预定义类数组(评审 R5:禁内联 style)。
- **007 冒烟(2026-08-19,Playwright MCP,12 项全过)**:admin 登录→用户列表
  (7 真实用户含联邦账号活数据,角色/部门聚合)→部门两级树(锦官城研发中心
  →军师算法组)→建用户 pangtong(绑 DEVELOPER+军师算法组)→搜索「凤雏」
  过滤→角色页(内置 badge+删除禁用)→建 ANALYST(本部门及以下)→权限绑定
  (chat/system 分组,勾 chat:execute→1 项)→删 ANALYST→编辑 pangtong
  (回显修复后部门/租户/角色全正确,改昵称保存部门未清空)→重置密码
  (curl 验 must_change_password=True)→删除(确认框→列表消失)→
  smoke-self:无管理入口+直达 /admin/users 403+子导航隐藏。
  收尾 curl 探针:create→reset→delete 全 204+审计三动作落库可查。
- **007 审查循环**:独立评审 2 严重+5 建议全闭环——S1 重置密码未复位
  首登改密标志(update_user COALESCE 路径不触碰该列,专用 reset_password
  语句修复+「先自助改密再被重置」用例);S2 管理面写操作零审计
  (design 承诺未兑现,9 类动作补齐+测试全量断言);R1 超管保护扩禁用/剥角色;
  R2 并发 IntegrityError→409;R3 角色清单未就绪禁存;R4 role_key pattern;
  R5 部门树内联 style 改预定义类。评审确认合格面:16 端点权限守卫无漏挂、
  改 SUPER_ADMIN role_key 再删等绕过链逐一验证不可行、契约与代码一致、
  XSS 无风险、事务语义正确。
- **007 /goal 审查循环(R1 轮,双路深检)**:api 切口+web 切口两路独立评审。
  api 严重=管理面改密(重置/编辑传密码)不吊销 refresh 家族——账号泄漏场景
  下重置密码后攻击者旧 refresh 仍可续换 access;修复=system service 注入
  RefreshFamilyStore(auth.ports 复用),改密 commit 后吊销全家族,spec delta
  补「管理员改密踢下线」场景,+2 用例(旧 refresh 401/编辑传密码哈希变+吊销)。
  api 建议:IntegrityError handler 补日志+作用域收敛(仅 /system 409,其他域
  维持 500 等价行为);ILIKE 元字符转义(%/_/\ 按字面,ESCAPE '\')+对照行用例;
  0004 downgrade 前置条件注记(软删同名行需先清);测试死赋值清理+审计用例
  补状态码断言+detail 非空断言;log_perm_denied 的 commit 前提注释锚定。
  web 严重=三页删除确认错误提示死代码(Radix AlertDialogAction 点击即关,
  失败错误行随卸载不可见——006 曾抓过同款)→ 改受控 Button(成功才关,失败
  保持开);删除末页唯一条目分页越界(「5/4」空页误导)→ useEffect 越界自愈。
  web 建议:三页 error 态渲染(403/500 下不再空白);DeptsPage 子部门租户随
  父节点(原恒取清单首项,多租户下会静默挂错);对话框 form 化支持 Enter
  提交+aria-describedby 消 Radix 警告。核验降级不修:「role:manage-only
  自定义角色侧栏无管理入口」——子路由 URL 直达可达(非功能不可达),属 nav
  单权限点模型边角,登记待办。浏览器复验:SMOKE_SELF 删除 409 错误可见+
  对话框保持开/部门 Enter 创建/删除成功受控关闭。api 205+agent 53+web 绿。
  评审同时核验通过:SQL 语义(GROUP BY 函数依赖/count-list 同构/json_agg)、
  事务边界(operlog 同事务/中途抛错隐式回滚)、uuid7 拆分零回归、
  react-query 键空间、契约消费逐一对、shadcn 组件 token 兼容。
- **006 api 侧**:chat 域三端点——`GET /chat/sessions`(limit 1..200 缺省 50,
  updated_at 倒序+id DESC tiebreak)、`GET /chat/sessions/{id}/messages`
  (升序+id ASC tiebreak;user 问句/assistant 要素含 chart 完整对象)、
  `DELETE /chat/sessions/{id}`(204;DELETE..WHERE user+tenant RETURNING
  单语句原子归属校验,级联删消息)。越权/跨租户/不存在统一 404 同形
  (find_session_owned 收口);权限点/租户白名单与 /chat/query 同构。
- **result_json 形态演进(006)**:写入方改为
  `{columns,rows,insights,stats,chart:{type,x,y},error}`(005 为裸 chartType
  且无 error);回显读档优先,存量缺失 x/y 时 suggest_chart 纯函数确定性
  重算兜底(测试双路径锁定);AgentUnavailable 分支也落 result_json
  (error 文案统一读取路径);**落库段整体 try SQLAlchemyError**——agent
  回调期间会话被并发删除时 FK 违约仅日志+rollback,不炸 500
  (R1 评审实证竞态);_to_message 对坏 JSON(非 dict/元素畸形)全窄化防御。
- **006 web 侧**:应用壳 AppLayout(侧栏:品牌区/新对话/会话列表/导航区/
  租户切换/用户区;md 以下 Sheet 抽屉);路由 `/chat` + `/chat/:sessionId`
  (URL 即会话状态单源,刷新/直达天然成立)+ `/admin` 占位 + 403/404 品牌页;
  **RBAC 守卫双源同构**:lib/nav.ts 配置单源(id/label/icon/to/perm)+
  hasPerm 谓词,菜单过滤(visibleNav)与路由守卫(PermRoute)同源;
  对话区:消息回显/提问(Enter 发送/Shift+Enter 换行/isPending 防重复/
  **失败回填草稿且尊重等待期新输入**)/三态渲染(SUCCESS 卡片含图表+
  表格+SQL 折叠+洞察+stats;REJECTED/FAILED 分态文案)/会话被删 404 时
  自动收敛回新建态;图表 echarts,**色板与轴色全部 getComputedStyle 从
  CSS token 派生**(系列色 [--primary,--ring,--accent-foreground,
  --muted-foreground],轴字 --muted-foreground/轴线 --border,禁硬编码,
  R1 评审曾抓到 #666/#bbb 违规已修)。
- **租户切换(006)**:localStorage `km.tenant` 单源,api.ts 每请求统一注入
  X-Tenant-Id(单点免遗漏;对非 chat 端点无语义);auth-store loadMe 后
  calibrate(持久值失效回落首项),登出/会话终结清租户;切换=先导航 /chat
  再 invalidate sessions+remove messages(避免旧请求带新租户头 404 空转);
  单租户只读展示。react-query 键全含租户维度;QueryClient retry 对 4xx
  不重试(api 层 Error 附 status)。
- **Radix 单轨(006)**:shadcn 新组件(sheet/dropdown-menu/alert-dialog/
  scroll-area/avatar/separator/skeleton/collapsible/badge/textarea)走
  radix-ui 聚合包,label.tsx 一并切换,@radix-ui/react-label 已移除
  (proposal 原预期零新增依赖,实际 CLI 自动引入聚合包,已在评审中裁定
  单轨化处理;button.tsx 同步升级 shadcn 新版函数式写法)。
- **006 审查循环**:R1 独立评审 4 严重+10 建议全部闭环
  (建议全列:成功路径先导航/租户切换顺序/删除失败对话框受控/403 出口
  动态取首项/main key 死代码/色板去前景色/radix 双轨/FK 违约容错/
  坏 JSON 防御/显式租户头正向测试)——严重:提问失败
  静默丢输入(onError 回填+错误条)/会话被删空白死态(404 收敛)/删除按钮
  hover-only 不可达(focus-within+max-md 常显)/图表硬编码色(token 化);
  建议:onSuccess 先导航防空态闪回/租户切换顺序/删除失败对话框受控保持
  开启(404 视为已删)/403 出口动态取 visibleNav 首项/色板去前景色/
  radix 双轨/FK 违约容错/坏 JSON 防御/显式租户头正向测试。
  **R2 复核抓回三处假闭环**(删除提示随对话框卸载不可见/首问失败错误条
  被 EmptyState 分支吞/AgentUnavailable 分支落库未包 try——以及实施中
  一次 except 块缩进塌缩被测试红抓回),R3 复核确认全闭环。
  安全面核过无异常:同形 404/原子删除/租户白名单兜底(localStorage 篡改
  最坏自我 403)/limit 注入面/契约三端点与代码逐项一致。
  **R4 兼容缺口复审(提交前,8 项全修)**:前三轮盲区=shadcn v4 新组件与
  定制 token 体系的兼容——①动画类全量静默失效(animate-in/fade-in-0 等
  v4 核心不含,需 `tw-animate-css`,此前从未安装,抽屉/下拉/确认框全靠
  瞬间出现);②裸 `border` 落 currentColor(v4 默认边框色变更,shadcn 靠
  `@layer base` 全局兜底,本项目缺失→outline 按钮/浮层边框墨色且随 hover
  变色,波及登录页联邦按钮/403 页/租户切换器);③`bg-popover` 未定义
  (dropdown 浮层透明底)。修复:index.css 补 `@import "tw-animate-css"` +
  `@layer base` 官方基线(* border-border/body 底色字色 token 化)+
  popover token(card 别名,brand.md 已按纪律先记意图);
  另 Sheet 关闭 X 与品牌行热区交叠(showCloseButton={false})与 HANDOFF
  三处文字勘误。**经验沉淀:后续经 shadcn CLI 加新组件,必核其引用的
  语义类/动画类在本仓 index.css 是否有定义**(v4 基线三件套:
  tailwindcss+tw-animate-css+@layer base,缺一即静默降级);
  浏览器 DOM 实证:outline 边框 #dfe3de/浮层白底/animation:enter 全生效。
- **006 冒烟(2026-08-19,Playwright MCP + 真 LLM,14 项全过)**:登录→
  应用壳→首问自动建会话+URL 参数化→问答全链路(图表/表格/洞察/stats/
  SQL 折叠)→刷新恢复→字面 DELETE 诱导 BLOCKED(REJECTED 视觉+分态文案)
  →删除会话(确认框→级联→回新建态)→租户切换 sakila(跨域真实查询+
  会话按租户隔离)→404→管理面占位(admin)→smoke-self 登录:菜单无管理
  入口+单租户只读+用户级会话隔离→403 直达。
  **摘要幻觉复现实证(005 已登记行为的 006 再确认)**:诱导删除时 LLM 生成
  `SELECT COUNT(*)...WHERE name ILIKE '连弩'` 规避写操作,摘要却谎称
  "删除成功"——数据无损(库内 4 行完好),属 prompt 调优范畴
  (随 query-streaming/prompt 护栏域评估,见 005 已知 TODO)。
- **问答链路(005)**:用户面 `POST /chat/query`(JWT + **require_perm 首发**
  chat:execute + X-Tenant-Id 白名单 + 会话/消息落库 + history 补全最近 5 条 +
  trace_id 贯穿 + 图表建议纯函数 line/pie/bar/none)→ 回调 agent `POST /query`
  (120s 超时,不可达/非 200/坏报文收敛友好错误,落 FAILED 消息)→
  8 节点图全真:understand(最近 5 轮指代消解)/retrieve(经
  /internal/meta/schema 实时内省,保留表已排除,50 表截断)/generate
  (SQL_GEN_SYSTEM 七规则)/validate(sqlglot 预检四规则,非只读根=权限类
  直接终态)/execute(回调 004 路由)/selfheal(重试上限 2=初始之外两次,
  耗尽标 retry_exhausted;BLOCKED 不耗配额)/summarize(结构化洞察 +
  stats 本地算 + 三级降级)。消息 status:PASS→SUCCESS/BLOCKED→REJECTED/
  ERROR→FAILED;REJECTED 文案按 reason 前缀分态(DATA_SCOPE/RESERVED_TABLE/
  非只读)。
- **保留表拒读(005 审查实证修复)**:sqlfw 新增 check_reserved_tables——
  sys_/chat_ 前缀 + alembic_version/user_identities,末段名小写比对,
  不限 schema 生效(裸名落 public 是最危险形态),CTE 名/表函数放行;
  执行路由复检段调用,verdict=BLOCKED。背景:public 在 search_path 内 +
  km_readonly 对 public 全表有 SELECT + data_scope 对无 create_by 列降级,
  三环叠加曾使 sys_user.password_hash 可经问答链路被读出(评审逐环实证)。
  meta 内省同一判定排除保留表(不占 50 表上限)。
- **sqlglot 异常口径**:ParseError 与 TokenError 统一按 SqlglotError 捕获
  (005 评审:TokenError 逃逸曾使未闭合引号 SQL 穿出图变 500)——agent 预检、
  api 防火墙 check/check_schema_scope 三处同改。
- **agent 面新文件**:llm.py(AsyncOpenAI 薄封装,温度按节点,30s 超时)、
  apiclient.py(httpx 回调,HTTP 错误收敛领域结果)、deps.py(工厂,测试
  monkeypatch 入口)、graph/prompts.py(上游逐字,脚本生成非手抄)、
  graph/nodes.py(七节点实现)。依赖新增 sqlglot(预检);pytest-asyncio。
- **api 侧**:httpx dev→主依赖(回调 agent);种子 PERMISSIONS 加
  chat:execute,DEVELOPER 推荐绑定增量补齐(只增不摘);config 加
  agent_timeout_sec(120)/meta_schema_max_tables(50);import-linter 增
  chat.models 零框架契约。
- **重试语义澄清(005)**:MAX_RETRY=2 指「初始尝试之外最多重试 2 次」,
  总执行尝试 ≤3;selfheal 预算耗尽不增计数(retry_count=实际发起的重试数)
  并标 end_reason=retry_exhausted 由路由收束。
- **冒烟(2026-08-18,真 LLM qwen-plus,全过)**:① 首轮 COUNT 问答 3.5s
  (会话自动建/消息/审计齐);② 多轮「那它们分别是谁创建的?」指代消解 +
  LIMIT 100 明细保护;③ 诱导删除:LLM 用 WHERE FALSE 规避(数据无损,
  纵深防御生效)但摘要谎称"删除成功"——**摘要幻觉已知行为,登记观察**;
  ④ smoke-self(SELF scope)问"我创建的商品"只回连弩,审计含
  `WHERE goods.create_by = :km_ds_0` 改写形态;⑤ 评审后复验:「列出
  sys_user 密码哈希」BLOCKED + 「该查询访问了不允许的表」文案 + meta
  内省结果仅剩 goods。
- **005 审查循环**:R1 独立评审 1 阻断(B1 保留表透出通路)+ 1 严重
  (S1 TokenError 逃逸)+ 5 建议,全部修复(建议项:history 单条限长 2000、
  REJECTED 分态文案、AgentClient 解析收敛、租户 SQL 下沉 repository、
  补 trace_id/超管不豁免/TokenError 测试)。
  **R2(全量 005 变更面)2 必修 + 4 建议全部闭环**:B-1 契约漂移(history 限长
  改了没跑 make contracts)已重生成;S-1 CTE 遮蔽绕过(WITH sys_user AS
  (SELECT FROM public.sys_user) 曾穿透五层防御,评审真库实证)已修——
  CTE 别名命中保留名即拒、仅豁免非保留 CTE 名,spec 示例改非保留名;
  建议:_json_cell 非有限值映射 None(>2⁵³ 精度取舍注释钉住)、load.sh
  幂等守卫 + 落点断言、api 防火墙 TokenError 单测。**R3 复核:全部闭环,
  11 种自拟绕过形态 0 失误,总评可提交**。
  **R3 侧报已当场闭环**:危险函数拒用(pg_read_file/pg_ls_dir/pg_sleep/
  set_config/lo_import/dblink 等,FORBIDDEN_FUNC,嵌套也拦)——Select 根
  纯函数调用曾穿透全部防御层(dev 库 kongming=superuser 实证可读服务器文件);
  spec 已立约「危险函数拒用」,e2e 实证 BLOCKED。注意:Anonymous 函数名取
  .name,已收录类型用 sql_name()(sqlglot 两路径,踩过一次)。
  另:R1 引用的 km_readonly 授权链路非 live 路径(只读引擎用 database_url
  owner + 事务级只读),暴露结论不变,证据以 executor.py 为准。
- **执行路由(004)**:`POST /internal/sql/execute` 全链路——内部令牌 middleware(002 已有)
  → 租户解析(tenant_key,未知/禁用 404)→ 用户-租户绑定复校(未绑定 403;user_id 缺省
  =系统调用豁免)→ 非 default 数据源 400(动态池未落地,fail-closed 不静默回退)→
  防火墙初检 → data_scope 改写 → 防火墙复检 → 只读池执行(独立引擎,连接级
  `default_transaction_read_only=on` + 30s statement_timeout,池 5/0)+ 事务内
  `SET LOCAL search_path=tenant_<key>, public` + fetchmany 截断(默认 1000)。
  响应 verdict 三态:PASS/BLOCKED(策略拒绝,含 DATA_SCOPE_UNSUPPORTED)/ERROR
  (执行错误,reason=驱动层整洁摘要,已剥 SQLAlchemy asyncpg 包装类前缀,供 selfheal)。
- **datascope 域包**(004):models/service(纯逻辑)零框架;rewrite=sqlglot 最外层
  WHERE 注入 + 自定义方言子类保留 `:name` 绑定参数(postgres 方言默认渲 pyformat);
  值按列探测 data_type 类型感知绑定(uuid 列绑 UUID,PG 无 uuid=text 算子);
  repository=角色 scope 集/用户 dept_id/递归 CTE/information_schema 列探测
  (schema 限定 tenant_<key>+public)。多角色取最宽松序 ALL>CUSTOM>DEPT_AND_BELOW>
  DEPT>SELF(与上游 SMALLINT MIN 语义逐档对应);UNION/FROM 子查询取不到主表→安全降级;
  用户不存在/无角色→降级+告警(沿用上游裁定)。
- **审计(004)**:SqlAuditWriter 独立短事务写 sys_sql_audit,失败 error 日志不阻断;
  trace_id>64 截断告警;审计 sql_text=改写后实际执行形态(可回放)。
- schema:0001 baseline + 0002 user_identities + 0003 dept + 0004 软删唯一索引
  + **0005 sys_datasource 补 name/default_schema**(008;空表瞬时,downgrade 注记)。
- **Makefile**:api/agent-check 已接线 `lint-imports`(原仅 CI 跑,本地门禁补齐);
  api 新增约定「datascope/audit 域禁 fastapi」(import-linter 需
  include_external_packages=true 才可禁外部包)。
- **004 审查循环(2026-08-18,R1 独立评审 7 项全属实已修 + R2 验收通过)**:
  ①未知租户「不落审计」测试断言恒真→改全表计数断言;②部门递归 CTE 改 UNION
  去重止环(脏数据成环曾实证挂到超时,控制面引擎无 statement_timeout);
  ③编排段基础设施异常(池超时等)收敛 verdict=ERROR+落审计,不再裸 500;
  ④**新增 schema 边界校验 check_schema_scope**:显式 schema 限定仅允许
  tenant_<key>/public(未加引号标识符按 PG 语义折叠小写比对,表函数/CTE 名放行)
  ——search_path 管不了显式限定名,跨租户显式引用此前可读(纵深缺口,spec 已补
  「租户 schema 边界」条款);⑤trace_id 请求宽度 200→64 与列同宽;
  ⑥清单 8 补子查询形态用例;⑦防火墙补拦 SELECT INTO(PG 建表写,Select 根放行
  的预存缺口,R1 自查发现)。R2 低级观察 2 项:大小写折叠误伤已修;
  schema 限定表函数跳过校验=刻意(本通道无法创建函数,无残余风险,代码注释登记)。
- 认证(ADR-003/006 落地):账密登录(argon2id + 失败 5 次锁 15min + 禁用/软删拒登 +
  首登强制改密)、refresh 家族轮换与复用吊销(Redis 三态)、logout、/auth/me 真实
  角色/权限点/租户、自助改密(吊销全家族)、连接器框架 + **五路插件** GitHub/Google/
  企微/钉钉/飞书(state 一次性 + PKCE 按家声明 + verified-email 关联 +
  自动建号绑 DEVELOPER/default 租户)。
  **003 协议核验结论(官方文档,ADR-006 演进注记)**:飞书 v2 端点 client_id/client_secret
  直换 user token(免预取 app_access_token)且官方支持 PKCE → pkce=True;
  钉钉/企微无 PKCE 形态 → False;企微详档需通讯录权限,缺失降级 display_name=UserId;
  三家 email 一律不入管线(无验证语义)。provider 交换失败新增 ProviderExchangeFailed→400。
  **真实 e2e 五路全过(2026-08-17,真凭证全链路)**:**钉钉**✓(建号→DEVELOPER/default→
  unionId 绑定→日志齐);**飞书**✓(PKCE 实跑);**GitHub**✓(基类标准 authlib 流首验,
  email 私有未回传→不落库,verified-only 纪律实证);**Google**✓(基类 PKCE+OIDC 首验,
  verified email 落库分支实证);**企微**✓(cloudflared 快速隧道过可信域名校验;详档
  权限已开→name/avatar 全字段路径实证,降级路径单测覆盖)。
  e2e 抓出并修复:飞书 v2 token 必带 redirect_uri(官方标注可选实为必带);
  conftest 三家默认置空串防本地 .env 真凭证渗入测试面。
  **企微两个平台侧门槛(控制台操作,非代码)**:①可信域名需归属校验(校验文件放域名
  根目录,本次经隧道域名完成);②gettoken 要求调用方出口 IP 在应用「企业可信 IP」
  白名单(60020),家宽 IP 漂移需重配。
  **联邦总开关**:`KM_<PROVIDER>_ENABLED`(五家,默认 true)=false 即停用该家
  (options 隐藏 + start/callback 404),凭证保留——企微现已用 `KM_WECOM_ENABLED=false`
  关停,待正式域名/可信 IP 环境就位再翻回。
  **企微联调环境结论(2026-08-17 晚)**:ngrok 免费固定域名与企微可信域名校验**不兼容**
  (校验爬虫带浏览器 UA,撞上免费版拦截页);可用路径 = cloudflared 快速隧道
  (无拦截页,已实证)或正式域名 + named tunnel。ngrok 固定域名
  (baggie-armadillo-crave.ngrok-free.dev)保留作对外演示入口。
  **联邦建号用户名一律 {base}-{provider}(无冲突也带后缀,来源可溯,用户拍板)**——
  spec「冲突时生成不重复用户名」粒度以下的实现变更,不涉及约;脱冲突 -N 链保留。
  钉钉身份命中分支亦真人复验(再登只见 login、用户数不变)。
  **轮换判定已原子化(Lua GETDEL+立墓碑单脚本)**:并发同票刷新仅一个胜者,败者按
  spec「复用即吊销家族」处理——多标签页毫秒级同票竞刷会触发重新登录,属契约既定语义;
  前端 bootstrap 已登录态短路,路由重挂载不再重复轮换(防误触发)。
- 评审循环(002 提交前):核验出 6 真实缺陷全部修复(轮换原子化/0002 downgrade 五家 CHECK/
  config 下界/角色 sort 落库/bootstrap 短路/杂项),剔除全部虚构项;54 测试全绿。
- 启动幂等种子(lifespan):角色 SUPER_ADMIN/DEVELOPER、权限点注册表(system:user|role:manage
  + **chat:execute**,005)、默认租户 default、超管 admin(KM_ADMIN_INITIAL_PASSWORD
  缺省 admin123 强制首登改密);DEVELOPER 推荐绑定 chat:execute(只增不摘)。
- **dev 库现状**:冒烟后 admin 密码已被改为 `Kongming#2026`(非缺省;如需复位可删库重播或走改密接口);
  角色 sort 列为 0/0(种子只增不改,不补齐历史行;新库播种即 1/2);
  另有五路联邦 e2e 留下的 5 个联邦账号(漆阳帆-dingtalk/漆阳帆-feishu/qiyangfan-github/
  qyf…-google/QiYangFan-wecom,均 ACTIVE,可作身份命中回归的活数据);
  **004/005 冒烟活数据**:`tenant_default.goods` 小表(4 行)、`smoke-self` 用户
  (SMOKE_SELF 角色=SELF,绑 default 租户,本人行=连弩;005 已授 chat:execute,
  密码已置 `Smoke#2026`)——行级问答回归活数据;chat_session/chat_message
  有 005 冒烟留下的真实问答记录;
  **008 冒烟活数据**:sys_datasource 行 `sakila_dyn`(归属 default 租户,
  同实例异 schema 形态,default_schema=tenant_sakila,密码=dev 库口令经
  dev-only 密钥加密)——动态源问答回归活数据;另有「有多少部电影/演员」两会话;
  **示例数据集(2026-08-18 装载)**:sakila(film 1000/rental 16044/customer 599)
  与 olist(orders 99441/items 112650)两租户已注册并绑 admin;
  **006 冒烟留痕**:default 域 goods 表实测无库存列(仅 name/create_by),
  问"库存"时 LLM 以 COUNT 释义属正常行为;chat_session/chat_message 另有
  006 冒烟新增会话(含一条 REJECTED 拦截轮),可作回显回归活数据;
  源文件在 `.example-data/`(135MB,已 gitignore),装载管线 `infra/example-data/load.sh`
  (幂等可重放,含中文注释 comments.sql——内省进 LLM 提示的质量关键)。
- 前端:登录页五家联邦按钮数据驱动;simple-icons 覆盖 GitHub/Google/微信系,
  **钉钉/飞书 simple-icons@16 未收录 → 按例外条款内联官方 mark**;favicon.svg 已补
  (朱砂底白色羽扇)。003 冒烟:五家图标实测渲染正确。
- 质量:api ruff/mypy strict/lint-imports/pytest **485**(认证+datascope+执行路由+
  防火墙+chat+meta+会话端点+system 管理面+datasource 68+rag 58+chat 体验 44+
  图表 scatter 3+012 启停 7+013/014/015 增量+016 匹配器 24+writeback 治理 5+
  **017 流式 14+SSE 解码器 3+B1 断言**),
  真实 PG+Redis)+ agent 同门禁 **95**(+8 流式) + web tsc/eslint 全绿;
  CI api job 已带 pg/redis services(未在真实 runner 验证——push 后首验);
  **评估器(evals/)不进默认收集**,评估环境显式运行(016)。
- 契约:openapi.json 含 /auth/* + /internal/sql/execute + /chat/query +
  /internal/meta/schema + /chat/sessions(列表/消息/删除)+
  /system/* 16 端点(007)+ /system/datasource/* 8 端点(008)+
  /system/rag/* 5 端点 + /internal/rag/retrieve(009)+
  **/chat/explain + /chat/export + PATCH /chat/sessions/{id} + sessions?q(010)**+
   **/chat/query/stream SSE(017,含 Stage/Sql/Summary/Error 事件 schema)**;
  端点落定后 `make contracts` 已重生成,web tsc 过。

## 已定死的决策(不再议,引用即终)

| 主题 | 结论 | 出处 |
|---|---|---|
| 技术栈 | Python 3.13 双进程(api 持凭证 / agent 零凭证)+ React 19 SPA;不引入 Keycloak/Next.js/微服务/消息队列/Litestar | ADR-001 |
| 信任边界 | 进程边界构造级;agent 禁 DB 凭证(env/驱动双禁);合并进程=违宪 | ADR-002 + 宪法 Article IV |
| 认证 | 自建(argon2id + PyJWT RS256 + authlib);登录页=前端路由;未来企业 SSO 走"引入 IdP 作下游"回退路径 | ADR-003 |
| 联邦登录 | 连接器架构:通用 OAuth2 内核 + provider 插件 + 通用路由 + options 数据驱动;新增 provider=插件文件+配置,路由/前端零改动 | ADR-006 |
| Schema | baseline 制;UUID v7 / VARCHAR+CHECK / timestamptz;已规划能力列一次建全 | ADR-004 |
| 契约 | FastAPI OpenAPI 唯一事实源;前端类型生成禁手写;CI 漂移 diff | ADR-005 |
| 分层 | 域包内 router→service→models→repository;domain 零框架依赖 | 宪法 Article II |
| 执行路由 | 三态 verdict(PASS/BLOCKED/ERROR);用户-租户绑定复校;data_scope 改写=最外层 WHERE AST 注入(非包子查询);非 default 数据源 fail-closed;**控制面保留表(sys_/chat_/user_identities/alembic_version)拒读 + 危险函数(pg_read_file 等)拒用(005 审查循环)** | sql-execution + datascope spec(004/005) |
| 问答链路 | agent 8 节点图;权限类(BLOCKED/预检非只读)不重试;重试上限=初始之外 2 次;schema 提示 RAG 前=实时内省过渡形态 | nl2sql + nl2sql-agent + chat spec(005) |
| RBAC 管理面 | 内置角色按 role_key 集合判(SUPER_ADMIN/DEVELOPER)禁删;role_key 创建后不可改+传输层大写蛇形 pattern;超管按 admin_username 判禁删/禁禁/禁剥 SUPER_ADMIN;角色/部门物理删除(前置校验+级联清绑定+审计),仅用户软删(username 唯一=部分索引软删感知);管理面写操作与业务同事务审计 | rbac + web-admin spec(007) |
| 动态数据源 | PG-only;凭证 AES-GCM(base64 iv‖ct+tag,prod 密钥 fail-closed,dev 固定密钥降级);key 小写蛇形+保留字 default 禁注册+创建后 key/租户归属不可改;管理端点全局视角+创建显式指定租户;非法 key 同形 400(不存在/禁用/越属,审计分类可溯);连接信息变更/启停/删除 commit 后定向 evict,仅改 name 不动池;物理删除;选项接口首位合成 default 项 | datasource + internal-meta + web-admin spec(008) |
| RAG/知识库 | rag 两表 baseline 既定(四元唯一键 tenant+ds+schema+table,1536 维+HNSW+tsv GIN);三路召回=向量+全文('simple')RRF k=60 top5 融合+query_history 独立强 few-shot(不进融合,≤3 条);tenant+ds 双过滤;召回零命中/失败回退实时内省;成功 SQL 回写(原始问句,最终 SUCCESS 判定);覆盖优先级 INTROSPECTION<AI_GENERATED<MANUAL_UPLOAD<MANUAL_EDIT<FEEDBACK_REFINED(自动不覆盖人工;reindex 显式清空例外);embedding 独立配置(缺省回退 llm_*,上游 028 教训)不随主模型路由;rag schema 显式限定名执行面拒读(末段名不进清单防误伤);缓存 emb 24h/ctx 10min,写操作 commit 后 SCAN 精确失效,空结果不写缓存 | rag + knowledge + nl2sql-agent + sql-execution + web-admin spec(009) |
| 会话体验 | 标题两阶段:初始=首问前 30 字符,首个 SUCCESS 轮后 LLM 改写(BackgroundTasks+独立会话;守卫读首问截断值短路+条件写 WHERE title=expected,人工重命名永不覆盖;改写不刷 updated_at);EXPLAIN=message_id 引用(不收裸 SQL,服务端复检后拼 EXPLAIN (FORMAT JSON),永不 ANALYZE,数据源取会话快照);导出=传 columns+rows 不重跑 SQL,CSV BOM+CRLF+公式注入中和,xlsx 自算列宽,rows 传输层上限 10000;搜索=标题 ILIKE OR user 问句 EXISTS(共享 like_pattern 转义);重命名=原子归属 UPDATE;全端点复用 chat:execute 无新权限点 | chat spec(010 delta) |
| 图表增强 | 推断补双数值列→scatter(x=首列,y=首个数值列;temporal/pie/bar 既有规则不变);四类型手动切换=卡片本地状态、映射恒定、刷新/同卡片新结果回落建议类型;scatter 渲染按 x 列全数值探测分流(排除 null 防 Number(null)===0 误判),非数值退化类目点图不丢点;PNG=getDataURL pixelRatio 2+背景取 --card token,文件名「图表-<时间戳>.png」 | chat「图表可视化」+ web-nav「结果图表渲染」spec(011 delta) |
| 反馈闭环 | 提交=幂等三态(null 保持/空串清空/显式覆盖)+行锁串行+置 PENDING+重复提交清空处置三列;归属 403 同形(与 chat 404 惯例分立);仅 assistant;纠错先过防火墙(与 internal 同一规则集)整体拒,通过后复用 009 writeback 沉淀(会话快照数据源;与现值不同才写;清空不删已沉淀行;失败不阻断);处置限定已反馈 assistant,枚举四态,note 三态;涉及表=sqlglot 抽表(CTE 排除,末段小写),匹配不纳 schema 名;统计=Asia/Shanghai 固定分桶逐天补零;行级可见性=非 ALL 强制本人覆盖传值(ALL 判定=角色集 bool_or);messageId 打通=实时响应纯增 message_id/datasource_key+历史回显 rating/corrected_sql/feedback_comment;REJECTED/FAILED 卡片不挂赞踩入口(API 不拦);编辑知识跳 /admin/rag?table=<名> 定位;纠错永不回写 table_descriptions | feedback + feedback-refine + feedback-analytics + chat + knowledge spec(014 delta) |
| 评估体系 | Execution Match=gold 直连执行 vs 链路响应结果集,严格 multiset/宽松双路径(列等价+投影行近似;数值子集天然无序;纯文本回退严格等值)并行;宽松=黄金指标(基线对照/回归红线,劣化 ≥5pp 溯源),**宽松不惩罚序错**=既定语义;评估器=apps/api/evals/ 进程外工具(登录→/chat/query persist=false 与真实用户同路径,gold 直连 asyncpg),testpaths 门禁隔离+三级 marker(smoke~5/full/multiturn),无凭证整层 skip;评估集按租户分文件(sakila22+olist18+多轮 10 case+动态 4),gold 编写规范九条(tiebreaker/业务可读列/结果集 ≤1000/明细 LIMIT 100+问句显式排序键/外层 ORDER BY/避时间戳列/显式 schema/连带同步);Recall=间接口径(生成 SQL 表覆盖 expected_tables,sqlglot CTE 排除);匹配器纯函数单测进默认门禁;CI 不虚构评估运行(smoke=评估环境显式触发,基线记录 README 表);query_history 回写幂等治理(同题覆盖保最新+保旧向量+每租户源上限 500) | eval + ai-eval + rag spec(016 delta) |

## 下一步(路线图梯队,按序)

1. **prompt 护栏域**(016 评估基线的量化靶点:gen 统计题误加 LIMIT/
   ::numeric 舍入/投影丢列/指代消解错位/漏 DISTINCT 五类失败归因+
   摘要幻觉/规避写操作两类既有观察,016 评估集 full 层可前后对比验证;
   017 基线表为流式回归红线);
2. 其后按 roadmap 第三梯队(tool-calling/mcp-interop/workflow-orchestration/
   cloud-code-sandbox 等,开工时再立 spec);
3. 剩余挂账见「已知 TODO」各段,随对应域变更搭车评估。

## 已知 TODO(代码内标注,非缺陷)

**017 挂账(R2-R5 观察)**:①断线续存窗口内重进会话只有 USER 气泡无
「生成中」占位(spec 为终态后语义不违约,体验增强随 UX 域评估);②
_persist_touch_quietly 失败回滚连带吞掉已成功的 insert_message(mid 悬空
→前端反馈 404;既有行为非 017 引入,触发≈DB 故障窗口);③selfheal 的
stage running 帧 retry_count 为进入时旧值(done 帧为本次序号,前端只用
done 帧,语义已钉 spec);④summary 单条完整 delta 非 token 级流(上游
同款 Phase 5 挂牌,契约按逐事件追加立约零演进成本);⑤流式 error 帧
不携带业务上下文(reason_code 恒 null,传输层失败无分类需求);⑥
agent_timeout_sec 流式下为读间隔超时非链路总时长(design §8 已注明);
⑦标题改写 bare create_task 在进程关停窗口不被等待(BackgroundTasks
会;best-effort 语义可接受,R3-O);⑧api 重启进行中的流随 worker 退出
(与同步在途请求同类,断线续存不跨进程);⑨落库容错分支(罕见)合成
卡片 id 空串=反馈/执行计划入口不渲染(R3-B2 修复后守卫天然生效,
快照永不退位至下一轮提问——刻意兜底)。

**016 挂账(R1 观察+冒烟注记)**:①0007 存量去重无直证用例(测试基建
全新建库无存量可覆盖;SQL 经 R1 逐条论证+PG row-wise 比较 NOT NULL 列无
陷阱;补直证需中段迁移测试形态,成本大于收益);②exec_sql 每题建连
(全量 ~66 次本地握手可接受,finally close 零泄漏;statement_timeout 未设
——gold 慢查询挂死靠评估集自控);③matchers 模块双载形态(bare 名与
evals. 前缀各载一次,纯函数无行为差异,统一前缀更稳);④匹配器未测分支
(数值子集严格真子集放行/bool 与数值混排排序键/date-time 归一化——低风险
补充项);⑤full 统计 expect_reject 含 ±1 题偏置(strict 恒 False/recall
恒 True 占位语义,README 已脚注);⑥评估器 401 重登在并发参数化下共享
_token_cache(评估串行跑无竞态;并行跑评估需改造);⑦README 示例含 dev
库实况口令(与 HANDOFF 既有记载同面,非新泄漏);⑧评估集首轮 70%→修订
84% 的口径修订记录在 README 基线说明(评估集演进史即资产);⑨上游
compare_matchers/analyze_failures 辅助脚本未平移(当前失败量 13,人工溯源
够用,量级上来再平移)。

**015 挂账(R1 观察)**:①system:audit:view 同一权限点管「查询」与
「删数据/回灌」(归档 run/restore),未来细分 archive 权限点;②调度
UTC 03:00=北京 11:00,与「凌晨低峰」意图偏差(时区随部署评估);
③conftest ASGITransport 不跑 lifespan,归档调度路径零自动化覆盖
(仅冒烟);④空窗口日也产出两个空 gzip+manifest(微量存储);
⑤审计页 trace_id/action 输入无防抖(每键一查,内网管理页可接受);
⑥归档 run 在 HTTP 请求内跑全量(大表受客户端超时影响,中途取消有
幂等兜底);⑦manifest name 字段理论可带相对路径(前提=已有文件系统
写权限,无权限增益);⑧多 worker 部署归档锁需 PG advisory lock
(单 worker 前提,008 挂账②同款);⑨oper 区 user 筛选接口已备 UI 未做;⑩归档调度时区为 UTC 03:00(北京
   11:00,与低峰意图偏差,spec 仅立约「每日定时」未钉时区,部署时评估);
   ⑪run_restore 对表结构演进无防御(归档后加 NOT NULL 列/删列再重导旧档
   会 500,库内无副作用,可接受边界);⑫--proxy-headers 采信 XFF 最左值,
   反代须覆盖而非追加 XFF(部署面);⑬归档 tasks 5.3 措辞「fetchmany 分批」
   与实现 LIMIT/OFFSET 分批不符(行为等价,工件勘误——归档封存不改,
   以本条为准)。

**014 挂账(R1 观察 O4-O12)**:O4 弹窗预填原 SQL 直接提交会把原 SQL
重复沉淀(被 009 成功回写天然稀释,随 eval 域 query_history 治理);
O5 overview 滚动 24h 窗 vs trend 日历日口径微差(边界差 1);
O6 recent_user_question_before 严格小于 created_at(手插同刻数据漏问句,
生产跨事务必晚);O7 insert+touch 同事务 commit 失败时 mid 悬空(仅 DB
故障窗口);O8 行锁无真实并发捶打(PG 语义成熟);O9「初检」措辞 vs
internal 分段(规则集合一致,仅措辞);O10③ days 传输层 422 vs spec
「钳 90」解读差(实现取 422);O11 REJECTED/FAILED 消息 API 层仍可
反馈(前端 SUCCESS 才挂入口,方向放宽无风险,收口需 submit 加 status
判定+spec 补句);O12 前端时间范围只作用统计卡/趋势不过滤明细表
(API 的 start_time/end_time 参数前端未用,days 可映射为明细表
start_time 随体验深化)。
**012 挂账**:①启停开关无确认弹窗(二元可逆+失败回弹,设计决策;
自禁/超管有 409 兜底);②行内错误只在最后操作的行保留(单槽 toggleId
归因;R2 后 pending 已全局互斥,无并发竞态,错误归因单行足够);
③禁用即吊销的 Redis 不可用窗口与改密吊销同语义(007 挂账既有);
④行内错误无自动清除路径(换行再操作/刷新即清,挂账)。
**011 挂账**:①getDataURL 在入场动画(~1s)进行中点击可能导出不完整帧
(用户看到图到点下载的间隔大概率已过,观察不设卡);②手动切换类型不跨
消息记忆(spec 立约回落建议类型,偏好记忆随 personalization 远期);
③pixelRatio=2 印刷/大屏倍率配置化随真实诉求再加;④pie 高基数手动
切换扇区爆炸不可读(spec 允许自由切换;默认推断的基数门槛仍在,手动
选择不设卡);⑥web 无单测基座
(同 010 挂账⑭,Playwright 冒烟覆盖);⑦option useMemo 依赖不稳定对象
引用每次重算(echarts-for-react 内部 fast-deep-equal 挡板,无视觉后果,
006 既有形态);⑧toggle-group 的 gap-[--spacing(var(--gap))] 任意值类
编译产物存疑(本仓未传 spacing 恒 0,惰性;未来消费方传非零时回头查)。
**(⑤temporal 轴标签、⑨散点 tooltip 已由 013 清零)**

**013 挂账(R1 观察)**:O2 rollback 不转译后外层 except 少一次重复兜底
(终态同为 500,ports docstring 明示决策,仅记录);O3 formatTemporalLabels
时间判零只看 HH:mm(如 00:00:30 截为纯日期丢 30 秒;类目轴按秒取数
极罕见,design D5 截断纪律内)。

**010 挂账(R1/R2 评审观察项)**:①用户手动把标题改成与首问截断值逐字相同时,
改写保护失效(守卫/条件写都按值判定;概率极低,影响=一次自动改写);
②AgentClient.rewrite_title「永不抛」的理论小孔——httpx client 构造遇非法
base_url 抛非 HTTPError(title_tasks 外层 except Exception 已兜底);
④分组函数固定毫秒日界,
DST 切换日偏 1 小时(zh-CN 无 DST,记录);⑥RenameDialog 打开态遇
他端并发改名 refetch 会冲掉编辑中输入(窗口极小);⑦导出审计不落
sys_oper_log(数据外发可见性,随 audit 域评估);⑧搜索 ILIKE 全扫不加索引
(个人侧栏量级;规模化评估 tsv/GIN);⑨标题改写仅新会话首个 SUCCESS 轮触发,
老会话回填策略随运营需要另议;⑩export_max_rows 调大超 10000 需同步
ExportIn.rows 传输层上限(design R1 注记);⑪同会话连续成功轮存在双调 LLM
窗口(守卫读时序;条件写兜底内容无害,代价仅重复 token,R2 探针实证);
⑫执行计划展开态不跨会话切换保留(useState 随卸载,重展开重拉一次,幂等);
⑬租户切换保留搜索词(所见即所滤,语义自洽,清空与否属产品取舍);
⑭时间分组/计划树渲染无前端单测(web 无测试基座,Playwright 冒烟覆盖);
⑮xlsx 数值 int 原生 vs 上游 doubleValue 显示差异(Kongming 更保真,有意);
⑯formatPlanNode 未移植上游 Node_Type 别名(PG 恒为空格形态,无缺口);
⑱导出按钮 columns>0 即显,空 rows 可导出仅表头文件(无害)。
**(③chat.service import-linter 契约、⑤删除确认 busy 取消、⑰重命名
strip 后校验已由 013 清零)**
**009 挂账(design Open Questions/复审 O 级)**:①datasource 删除后残留知识
条目不级联(召回需 ds 校验通过,天然 fail-closed;级联策略随 multitenant
管理变更再定);②fetch-metadata 同步端点大库(>100 表)时长不可控(异步化
只改执行形态不改契约);③query_history 无去重/上限/TTL(上游取舍,相似问句
靠余弦聚类;治理随 eval 域);④embedding 换模型需 bump emb 缓存版本前缀 v2
+维度迁移新迁移(类比 008 密钥轮换);⑤writeback 同步阻塞问答响应 P99
(emb 缓存命中近零,上游 afterCommit 同款);⑥fetch 的优先级读-判-写非原子
(管理低频操作,并发 fetch+upload 理论 TOCTOU);⑧datasource⇄rag
包级双向依赖(服务层已端口反转,router 适配层组合与 chat 同构,可后续上移
组合根);⑨rag 拒读若未来把 rag 加入任何执行 search_path,须重估裸名形态;
⑩HNSW+WHERE 过滤召回不足 topK(pgvector 已知:索引候选先于过滤,规模化后
   可能返回少于 LIMIT——规模化前评估 hnsw.iterative_scan 或监控召回数,
   /goal R2-B1);⑫中文全文路局限:'simple' 不切中文,tsv 与问句 token
   须整段一致才命中,中文场景全文路仅精确名/英文词有效(上游同构局限,已在
   009 design D3 声明;zhparser/自研切分挂账,/goal R2-S3);⑬emb 缓存与回调
   已有维度程序防线(rag_embedding_dim,漂移按未命中/降级),但换模型仍须
   bump v1 前缀+维度迁移。
   **(⑦列注释进润色、⑪reindex 先润色后删写已由 014 清零;
   ⑭AgentRagClient httpx 复用、⑮rag_retrieve 非 JSON 收敛已由 013 清零)**
**rag 单表知识编辑端点(rag-refine 域)未做**:MANUAL_EDIT/FEEDBACK_REFINED
枚举值位与优先级已留,人工精修现经上传链路;反馈精修随 feedback 域远期。
**008 挂账(design Open Questions)**:①密钥轮换无重加密通道——轮换
KM_DATASOURCE_ENCRYPT_KEY 后存量密文全不可解,以「编辑重填密码」逐源修复,
随运营事件评估专用工具;②多 worker 池失效——PoolEvictor 只清进程内池,
api 单 worker 为失效正确性前提(现状 Dockerfile 无 --workers),多 worker
部署需 Redis 失效广播;单进程内 evict 失败=旧池残留至进程重启(查询路径
不校验连接信息新鲜度,残留窗口随目标库凭证轮换收敛);③动态源账号只读
权限强校验挂账(同 004 km_readonly 裁定);④import-linter rag_admin 猜测名
随 rag 变更核对真实包名;⑤**会话-数据源绑定未立约(008 /goal R1-web)**:
会话列表不按数据源过滤,直达/侧栏点旧会话追问将按当前选中源执行并记入
原会话(切换器注释已去过度承诺;fail-closed 方向不破,语义收敛随 chat 域
后续变更立约)。
**datasource_key 与租户归属不可改 scenario 现挂主 spec「元数据抓取」
requirement 下**(上游平移布局),008 已兑现该强制——rag 变更落地
fetch-metadata 时顺手挪位到注册 requirement。
**部门树租户过滤(007 design Open Questions)**:部门带 tenant_id 但树查询
全量返回;按租户过滤部门树随 multitenant 管理变更再定。
**用户-部门跨租户绑定未校验(007)**:编辑用户时 dept 与 tenant 分属不同租户
不拦(design 已挂账);部门可迁租户。低风险,随 multitenant 域收口。
**软删用户的角色绑定残留**:软删不清理 sys_user_role(留审计语义,上游同款);
未来若做「恢复软删用户」需连带恢复/重建角色绑定。
**管理入口对单权限点组合的空隙(007 /goal R1 登记)**:自定义角色仅配
system:role:manage(无 user:manage/chat:execute)时侧栏无「管理」入口
(nav 单 perm 字段表达力限制)——/admin/roles URL 直达可用,非功能不可达;
彻底收敛需 nav 多权限点判定+/admin 落点动态重定向,随 web-nav 域演进评估。
**吊销 fail-open 窗口(007 /goal R2 观察)**:Redis 不可用时管理面改密
commit 后吊销抛 500,Redis 恢复后旧 refresh 在 TTL 剩余期内仍可续换——
与 auth 自助改密既有降级语义一致,后续可加吊销失败告警/补偿。
**摘要质量观察(005/数据集冒烟)**:① 诱导写操作时 LLM 可能生成 WHERE FALSE 规避 +
摘要谎称成功;② summarize 只喂前 5 行数据,聚合结果 >5 行时摘要可能以偏概全
(olist 状态分布实测说错最大值)——均属 prompt 调优范畴(上游同构行为),
随 query-streaming/prompt 护栏域评估。
**数值序列化(005 数据集冒烟修复)**:asyncpg numeric→Decimal 在 JSON 成字符串,
曾使图表推断/stats 判不出数值列;执行路由已加 _json_cell 归一化(Decimal→float)。
**执行路由已闭账(004)**:唯一遗留=专用只读登录角色(km_readonly 为 NOLOGIN 组角色,
现以连接级只读+防火墙双闸替代;随动态数据源变更评估,见 004 design Open Questions)。
**联邦验收全部关闭**:五路真凭证 e2e 均过(2026-08-17)。
**账号绑定(登录态追加 provider)未立约**:同一人经多家联邦会各自建号(国内三家无
verified email 可关联),需绑定时另起提案。
**企微联调环境注记**:企微控制台指向 cloudflared 快速隧道域名(无拦截页,可信域名
校验兼容;隧道重启换域名后,可信域名/回调域 + KM_WECOM_REDIRECT_URI 三处同步改);
应用「企业可信 IP」白名单随家宽 IP 漂移重配。ngrok 免费固定域名与企微校验不兼容
(拦截页),勿用于企微。频繁联调或正式部署再上正式域名 + named tunnel。

## 命令速查

```bash
make check          # 全门禁(api/agent ruff+mypy+lint-imports+pytest + web tsc+eslint)
make contracts      # 导出 OpenAPI + 生成前端类型(改 api 后必跑)
# 服务: apps/api → uv run uvicorn kongming.main:app --port 8000
#       apps/agent → uv run uvicorn kongming_agent.main:app --port 8001
#       apps/web → pnpm dev(5173,proxy /api → 8000)
# 迁移: apps/api → uv run alembic upgrade head
# 冒烟: curl 127.0.0.1:8000/healthz;curl 127.0.0.1:8001/healthz
```

## 铁律提醒(全局,详见 ~/.zcode/AGENTS.md)

- git 写操作(add/commit/push)逐次等用户明确指令,只做字面授权那一步。
- 旧项目容器/卷/网络禁碰;破坏性操作前二次确认。
- 安全规则类改动测试先行;文档注释一律简体中文。
