# Kongming 路线图与能力域清单

> 来源:nl2sql-data-platform 的 41 个 specs(约 4300 行,经 38 个变更验证的产品蓝图)。
> 处置原则:**spec 只收有实现对应的行为契约**(openspec 同步可信原则),
> 未开工能力在此登记,开工时再立 spec——避免开局背 4300 行纸面债务。

## 第一梯队(已平移 ✅ 2026-08-16)

对话问答闭环:自然语言 → SQL → 执行 → 图表,反馈沉淀与评估。
12 个 spec 已改写入库(`openspec/specs/`):剥离旧实现绑定,对齐新 baseline
(UUID 主键、data_scope/sql_scope/feedback_status 字符串枚举、审计 verdict 语义)。

| 域             | 说明                                                    |
| -------------- | ------------------------------------------------------- |
| chat           | 会话/消息持久化、多轮上下文、导出/EXPLAIN/图表          |
| nl2sql         | 问答主链路、强制只读、自愈重试、内部接口鉴权            |
| nl2sql-agent   | 多轮指代消解、生成质量规则、结构化洞察、schema 智能选取 |
| rag            | 三路混合召回、文档上传、知识管理、成功 SQL 回写         |
| knowledge      | 元数据 LLM 润色、reindex、覆盖优先级                    |
| feedback ×3    | 提交/纠错沉淀 → 处置状态机 → 统计分析                   |
| datasource     | 动态数据源注册/路由(PG-only)                            |
| audit          | 审计落库/查询/留存归档                                  |
| eval / ai-eval | Execution Match 匹配规则、基线管理、few-shot 自学习     |

## 第二梯队(rbac/multitenant/datascope/auth 全部入库 ✅)

| 域                             | 说明                                                                                                             |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------- |
| rbac / multitenant / datascope | 已按新枚举(ALL/DEPT/SELF、SELECT_ONLY 等)改写入库,行为契约保留                                                   |
| auth                           | 已与认证闭环实现同步入库(003/006 落地后随行 sync:账密登录/refresh 轮换吊销/自助改密/联邦登录连接器/联邦账号关联) |

## 第三梯队(全部落地 ✅ 2026-08-25,019–038)

数据 Agent 深化:tool-calling、mcp-interop、workflow-orchestration、cloud-code-sandbox
(NL2Insight 杀手锏)、agent-memory、multi-model-routing、document-rag、semantic-layer、
prompt-guardrails、custom-agent、query-streaming、conversation-artifacts、multimodal-input、
agent-observability(038,用量/失败/延迟可观测+trace 时间线)。

平台商业化:bot-store、developer-portal-billing、team-workspace、triggers-webhooks、
payment-gateway(mock 闭环)、datasets(由 008+009+example-data 管线覆盖,不单独立 spec)。

### 第三梯队分批计划(2026-08-22 定,按上游依赖拓扑排序)

上游依赖拓扑:tool-calling(022)被 6 域引用为内核前置;prompt-guardrails(030)
被 4 域引用、上游自定位为 tool-calling 伴生;custom-agent(025)← mcp-interop /
bot-store / developer-portal-billing;workflow(024)← triggers-webhooks(032);
artifacts(027)⇄ cloud-code-sandbox(029);agent-memory(026)← team-workspace(034)。

- **第一波(Agent 内核)**:019 tool-calling → 020 prompt-guardrails-security
  (安全向:injection 检测/PII 脱敏/输出审核/工具意图审核/sys_guardrail_audit,
  与 018 质量向护栏不同域);
- **第二波(独立增强,互不依赖,可并行推进)**:multi-model-routing(纯 Python
  侧最友好)/ agent-memory / semantic-layer / document-rag(后两者依托既有 rag 链路);
- **第三波(深化,依赖第一波内核)**:conversation-artifacts / custom-agent /
  cloud-code-sandbox / mcp-interop / workflow-orchestration / multimodal-input;
- **第四波(商业化/自动化)**:team-workspace(归属基础)→ bot-store /
  developer-portal-billing / triggers-webhooks;
- **datasets 域**:上游语义(数据导入+表说明登记+白名单授权三件套)已由
  008 动态数据源 + 009 rag + example-data 装载管线覆盖(sakila/olist 两租户在跑),
  不再单独立 spec;新增数据集(如天池)走既有管线,无需新域。

参考源:`/Users/solvela/nl2sql-data-platform/openspec/specs/<域>/spec.md`
(剥离 Java/COLA/Keycloak 实现引用后取其行为场景;schema 契约以 Kongming baseline 为准)。

## 第四梯队(AI 五大方向定调,owner 2026-10-09 确认,开工中)

战略定调见 ADR-010「通用为骨、数据为刃」:五大方向是同一执行底盘上的
**能力归口,不是五个系统**;方向定位与决策依据以 ADR-010 为单源,此处只登记
地基与开工载体。

本梯队是**能力展示清单,不是时间表**:本项目为个人学习与能力展示项目,
未开工方向一律登记为「待立项」并按展示价值排期补齐,不设「远期」档
(owner 2026-10-10 定调)。

**维度注记**:本表按展示口径压平了两个维度——问数/问答/评测是**能力**,
通用是**底盘**,多智能体是**执行形态维**(单实体执行 ↔ 协作编排,宿主 =
任务内核 091),不是并列的第五种 agent;运行时唯一 agent 实体 = custom-agent
域的 Bot 注册行。架构口径以 ADR-010 为单源。

| 方向             | 地基(已落地)                                                                                   | 开工载体                                                                                                                                                                                                           |
| ---------------- | ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 问数智能体(王牌) | 一梯队 chat/nl2sql/nl2sql-agent + 三梯队 semantic-layer / query-streaming / cloud-code-sandbox | 088 报告生成——数据链路闭环到可交付报告                                                                                                                                                                             |
| 问答智能体       | rag 三路召回 / knowledge / document-rag / agent-memory / knowledge-qa                          | 093 知识侧深化(三态意图路由 + 融合问答)——已落地归档;事件-实体检索扩展(SAG 对照借鉴)——已落地归档(首版实体路对照负收益已经入池门槛调优消除,对照持平见 evals/README 检索基线节)                                       |
| 通用智能体       | ReAct 内核 / agent-memory / multi-model-routing / prompt-guardrails / cloud-code-sandbox       | 089 工具网关 + 090 任务内核(长任务)+ 094 web 检索域(联网搜索)——工具网关已接入检索/HTTP 连接器/工单等域                                                                                                             |
| 多智能体         | workflow-orchestration(静态 DAG)/ custom-agent                                                 | 091 动态协作编排 + 115 圆桌对等拓扑(宿主 = 090 任务内核)——选型指引:步骤可静态枚举→workflow;运行时拆解/并行分治→collab 分工形态;立场对辩/交叉质疑(共识判定+分歧如实保留)→collab 圆桌形态(黑板轮次制,名册声明期锁定) |
| 评测智能体       | eval / ai-eval(Execution Match、基线、few-shot 自学习)+ agent-observability / eval-agent       | 092 评测智能体化(LLM-as-judge + 评测集自动构造)——已落地归档;检索质量评测 multi-hop Recall@k(SAG 对照借鉴)——已落地归档(retrieval 层进 nightly,基线见 evals/README)                                                  |

依赖拓扑:088 / 089 / 090 相互独立、可并行;091 以 090 为宿主殿后。
**落地状态(2026-10-13)**:088–091 全部归档;092/093/094 已落地归档(问答=
三态意图路由+融合问答,评测=LLM-as-judge+评测集自动构造,通用=web 检索域
联网搜索;五方向全部有落地载体)。(091:拓扑配置面/协作账本/查询端点/
web 协作视图/端到端冒烟双场景)。**095–114 批亦已全部落地归档**(097 探针
面板/098 中文分词/099 HTTP 连接器/100 工单/106 召回闸恒生效/110 指标可见性/
114 语音问数等;明细单源=openspec/changes/archive 工件与 git log)。
任务/协作部署级上限缺省值见 docs/tech-stack.md「任务/协作部署级上限」节。
**2026-10-18**:ai-platform 对齐调研(docs/ai-platform-alignment.md)实证剩余
差距后,净缺口立项(owner 裁决:求职学习项目以学习与展示价值为准,不做
「等触发信号」式挂账,个人项目等不到信号)——
①`add-graph-checkpoint`(图级检查点+图内挂起,HITL 第三形态)已立项开工;
②向量规模化(Milvus)原排序其后——**2026-10-19 ADR-012 收口:否决立项,
pgvector 为终局选型**(量级余量 >10x,翻转条件登记于该 ADR;owner 同日
定调裁决收口),净缺口收敛为熔断一项。
**2026-10-18(同日第二项)**:ruoyi-ai 对齐调研(docs/ruoyi-ai-alignment.md,
开源万星级 Java AI 平台三路实证调研)结论=孔明治理面全面领先(凭证加密/Alembic/
引用溯源/测试门禁/双进程边界,详见该文 §3),净缺口三项吸收:①`add-doc-rerank`
(document-rag 检索精排层 rerank,问答智能体方向)当日立项,**同日已落地归档**(工件在 openspec/changes/archive/);
②MCP 运行时熔断(mcp-interop 域 delta,连续失败自动冷却)——2026-10-19
Milvus 收口后排序阻塞解除,`add-mcp-breaker` 当日立项落地(工件在
openspec/changes/archive/)。
③音视频生成(原列 media-generation 域扩展)随域否决一并落空(owner 同日裁决
不需要 media-generation,登记于下方否定裁决表)。否决五项(短剧流水线/SSE 跨
节点广播/MCP 市场导入/xlsx 单独立项/多厂商 OSS 抽象)登记于下方否定裁决表。
**2026-10-18(同日第三项)**:全栈刷新调研(docs/stack-refresh-2026-10.md,
registry 实查)裁决=ADR-011,立项两变更:①`adopt-mcp-sdk2`——MCP 规范
2026-07-28 无状态修订(会话握手废除)使双向 689 行手写 JSON-RPC(028 出站
519 行/入站 170 行)维护被规范废弃的概念,且手搓动机「零新依赖」已失效
(mcp SDK 早是 dev 依赖),收编官方 SDK 2.x(dev→prod,<2→>=2.3,<3),
mcp-interop 域 delta(跨修订协商+当前修订声明);②`stack-refresh-2026-10`
(skip_specs 行为保持)。**同日 apply 终态:两变更均已落地归档**(SDK 双向收编
+四批刷新,实施事实见 ADR-011 与 archive/2026-10-18-stack-refresh-2026-10 归档工件)。四批:后端 major(SQLAlchemy 2.1/openai 3.35/
anthropic 1.15)+前端 major(Vite 8 Rolldown/vitest 5/eslint 10/
react-router 8/echarts 6/react 19.3+React Compiler 1.0 替代手写 memo
增量)+基础设施(compose pg17→pg18 对齐开发机、Python 3.14 可选档)+
下限漂移清偿(langgraph 下限 0.2.60 vs 实锁 1.2.11 的反面教材)。
暂缓/否决:TypeScript 7(typescript-eslint #10940 不兼容,等 7.1 世代)、
PG19(RC 刚过)、Python 3.15(等补丁周期)、uuid7.py 换库(ORM 侧生成
主键,PG18 原生 uuidv7() 仅 DB 侧 DEFAULT 场景,替换为负收益)。
**2026-11-24**:`add-chat-turn-cancel`(问答轮真取消)落地——对话页的「停止」从只脱离
接收改为真取消(Agent 面显式中止端点 + 控制面在途轮登记与取消编排 + 消息第五态
CANCELLED);实时语音通话(`add-voice-call`,提案已备、未开工)以本变更为前置
(通话中的打断依赖真取消)。

### 089 工具网关:后续域接入清单(2026-10-09 登记,2026-10-13 更新现态)

网关框架已就绪(域=清单+策略器+执行器;统一出站通道),新域接入=声明清单+实现策略器/执行器。
已落地域:web 检索(094:供应商 tavily/bocha/zhipu/alibaba_ai_search,端点与白名单代码
单源派生,检索卡脱敏+配额独立分桶)、HTTP 连接器(099:租户级注册,凭证掩码落库
+白名单+SSRF 出站双落点)、内部系统连接器·工单(100:部署级条件注册,
「最小工单 API 约定」门面)。剩余接入候选(开工时各自立 spec):其他内部系统连接器
(日历/CRM 等;写工具走既有确认流,等真实对接对象出现再立项)。
MCP 出站维持不收编(mcp-interop 自有注册面,见 089 Non-goals)。

## 产品待办登记(backlog-settlement 终态化剥离,2026-08-25)

初版待办五项(STT/OCR 选型、真实支付通道、个性化推荐、Slack 事件源、zhparser
切默认)已全部处置:STT 落地 114、个性化推荐早已存在(039),OCR/Slack/
zhparser/真实支付四项见下方否定裁决登记。**现开放产品待办=零**,立项候选仅剩
工具网关后续域(见 089 节,等真实对接对象)。

**2026-10-12 现态**:下列原待办已落地归档——可视化拖拽画布(107)、MCP
resources/prompts(109)、Bot 版本化上架(104)、用户/TEAM 维度配额(103)、
触发器失败重试策略(101)、邮件事件源(108)、中文全文 zhparser 配置化(098)、
知识库团队归属(102)、**指标(metric_definitions)可见性按 data_scope 过滤**
(110:定义侧 ALL/DEPT 档位+归属部门,注入面按调用者 data_scope 收窄
[ALL 全量/DEPT 系=ALL 档∪部门集合/SELF 与无身份=仅 ALL 档,CUSTOM 空集合
读面降级],管理面保持全量视角)。MCP 真实 server 集成验证(113)同日落地。

### 否定裁决登记(2026-10-12 起,owner 委托架构裁决:值得做即立项,否则登记于此)

以下各项经评估**明确不做**,登记理由防止反复重提;除非前置条件实质变化
(如真实凭证到位、消费场景出现),不再作为待办挂账:

| 项                                       | 裁决                                | 理由                                                                                                                                                                                                                                                                                                                                                                                                          |
| ---------------------------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Slack 事件源                             | 否决                                | 与 108 邮件触发器同构换协议,学习边际价值低;接入形态可复用 108 模式,凭证到位即可,无工程欠债                                                                                                                                                                                                                                                                                                                    |
| zhparser 切默认部署档                    | 否决                                | ①缺省 simple=任意 PG 零依赖可起;切 zhparser=所有部署必须装扩展否则启动自检拒启,部署门槛系统性抬高;②中文部署按 HANDOFF 配方显式启用即是正确形态,spec 已钉「缺省 simple 零回归」契约                                                                                                                                                                                                                            |
| OCR 引擎                                 | 否决                                | 独立 OCR 已被 vision 路吸收(030 图片问数+注册表 qwen-vl 现成),再挂引擎=系统内两条平行「图→文」抽取路径;重依赖(PaddleOCR 系拖 paddlepaddle,Tesseract 中文弱)且与 114 本地可选模型模式同构,学习边际价值低(同 Slack 裁决逻辑)。扫描件 PDF 现态=pypdf 文本层;真实需求出现时载体=上传管线 vision 抽取(复用 qwen-vl),仍非 OCR 引擎(2026-10-13 owner 采纳)                                                           |
| 真实支付通道                             | 维持既有裁决                        | 2026-10-11 用户裁决:支付通道 mock 即终态,不再重提                                                                                                                                                                                                                                                                                                                                                             |
| ticket 真实凭证                          | 已退役(2026-10-20)                  | ticket 工单域随 remove-ticket-domain 整体移除(无真实消费对象的专用协议);需要工单访问的部署走租户级通用 HTTP 连接器显式配置,不再作为待办                                                                                                                                                                                                                                                                       |
| 短剧流水线(剧本→分镜→配图→配音→合成)     | 否决                                | 方向外:ADR-010 五方向无内容创作归口,「数据为刃」禁横向摊薄;重依赖 FFmpeg/音频合成,学习边际价值低(ruoyi-ai 对齐调研 §5,2026-10-18)                                                                                                                                                                                                                                                                             |
| SSE 跨节点广播(Redis pub/sub)            | 否决                                | 单节点部署形态无场景;双进程 relay 已覆盖现有拓扑,为不存在的规模付复杂度(同上 §5)                                                                                                                                                                                                                                                                                                                              |
| MCP 市场一键导入                         | 否决                                | 供应链高危面(ruoyi-ai 自身风险点:外部元数据一键导入即得 Stdio 执行权);人工注册+出站白名单已是更优形态(同上 §5)                                                                                                                                                                                                                                                                                                |
| xlsx 知识文档解析单独立项                | 否决(搭车注记)                      | document-rag 域内小 delta(格式清单+1),单独立项价值不足;随该域下次变更搭车(同上 §5)                                                                                                                                                                                                                                                                                                                            |
| 多厂商 OSS 抽象(S3 SDK/MinIO)            | 否决                                | 本地盘+既有落盘管线已覆盖,无对象存储对应场景;「规模化前置」反模式(同上 §5)                                                                                                                                                                                                                                                                                                                                    |
| media-generation(图像/音视频/音乐生成域) | 否决(owner 直接裁决,2026-10-18)     | 多模态输出侧与「数据为刃」五方向无归口,对齐调研的立项排序被 owner 推翻;域变更(add-media-generation 四工件)已删,不作为待办挂账;前置条件=真实消费场景出现(报告配图等)再议                                                                                                                                                                                                                                       |
| SAG 引擎整体引入(zleap-sag,SQL 检索增强) | 否决(2026-11-07,与借鉴路线拆分处置) | 同域已实现且 spec 化(rag/document-rag/knowledge-qa 三域,三路召回+RRF+rerank+召回闸契约),引擎自有存储所有权与租户/可见性/召回闸过滤契约冲突;每 chunk 一次 LLM 事件抽取=摄入成本翻倍级,买的是未实测的多跳收益;论文 2026-06、引擎 0.15.0 pre-1.0、benchmark 厂商自报,不达骨干依赖门槛。重开条件=社区独立复现+引擎过 1.0+过滤扩展点满足 Kongming 契约(三者同时);借鉴三件套不在本否决内,见第四梯队 2026-11-07 登记 |

**2026-10-18 追注**:原拟在此登记的 media-generation 与向量规模化(Milvus)
不适用本表——二者已按 owner 裁决直接立项(见第四梯队 2026-10-18 段),
「等触发信号」式挂账自即日起废弃。(同日再裁:media-generation 被 owner
直接否决,回登本表;**Milvus 于 2026-10-19 经 ADR-012 正式否决收口**
——量级测算 pgvector 余量充分,独立向量库在该量级是纯运维负债,理由
全文见该 ADR,同口径不再重提。)

**勘误(同日,owner 指正「无场景」理由不成立——求职项目无需求方,场景
等不来)**:对原否定项重裁并勘测现状,两项发现**早已落地**(裁决时未查
现状,引以为戒——「要不要做」之前先问「是不是已经有」):

- **图片问数**:已存在(030 vision 链路:图片附件→vision 描述→进问数,
  含测试),无需立项。
- **个性化推荐**:已存在(039 chat-suggestions:LLM 按 schema 生成建议
  问题,缓存+防线+端点+独立 spec),无需立项。
- **STT 语音问数**:真缺口,已立项并落地(114;本地 faster-whisper,
  api 面,零凭证不触信任边界,不走 muniu 桥——ASR 非桥出口,改桥=
  跨项目变更)。
- **websearch 真实凭证**:撤出否定表——094 已接四家供应商(含智谱检索
  API 复用本地已有 bigmodel key,零额外注册),配 key 即真实生效,本就
  不该挂账。

**2026-11-07**:SAG 对照调研评估(论文 arXiv:2606.15971「SQL-Retrieval
Augmented Generation:事件-实体索引 + 查询时动态超边」,Zleap-AI 定位为
取代 RAG/GraphRAG 的第三架构)裁决=**登记模式,拆三件处置**:
①**引擎整体引入(zleap-sag)= 否决**,理由与重开条件登记于否定裁决表;
②**借鉴路线不否决、转候选排期**(按学习增量/展示效果/成本比例/定位一致性
四标准,守 2026-10-18「不等信号」口径,借鉴类候选同样不设「量出缺口才动」
闸门;三件 2026-11-07 当日立项并全部落地归档):

- 检索质量评测(评测智能体方向):multi-hop 评测集 + Recall@k,给 eval 域
  补检索质量档——**已落地**(变更 add-retrieval-quality-eval;retrieval
  层进 nightly,基线见 evals/README 检索质量基线节);
- 事件-实体检索扩展(问答智能体方向):实体伴表 + 查询时实体 join 做多跳
  扩展;pgvector 单库 SQL 形态,ADR-012 同哲学,零新增中间件——**已落地**
  (变更 add-doc-event-entity-recall;部署级缺省关);同日 A/B 对照首版
  实体路净负(泛频实体挤占融合池),经入池门槛(共享实体数≥2,仅真桥接
  入池)调优收口为对照持平(变更 tune-entity-recall-pool);部署保持
  缺省关;
- chunking 句界校准(事件完整性原则的落地形态)——document-rag 域内搭车,
  **已随上件落地**(部署开关缺省关,关闭=既有切分逐字一致);
  ③SAG-Benchmark 复现(官方公开复现脚本)可作 Kongming 主线外独立学习
  实验,不占能力清单登记位。

## 已被治理文档覆盖,不再立 spec(D 层)

| 旧 spec                     | Kongming 对应                          |
| --------------------------- | -------------------------------------- |
| contract-engineering        | ADR-005 契约管线 + CI 漂移门禁         |
| stability                   | 宪法 Article III + tech-stack 工程纪律 |
| security-compliance         | 宪法 Article IV(信任边界)+ ADR-002     |
| system-nav / web-responsive | web 主布局域开工时随行立 spec          |

处置注记(2026-08-25 全量核对):

- **observability**:已作为第三梯队落地(038 agent-observability——用量/失败/延迟
  采集与看板+trace 时间线;「宪法 Article III 覆盖」的旧处置作废)。
- **rag-refine**:已被 037(单表知识编辑端点+alias)与 014(表名抽取/定位跳转)
  覆盖,不单独立 spec。
- **datasets**:由 008 动态数据源+009 rag+example-data 装载管线覆盖(第三梯队段)。
