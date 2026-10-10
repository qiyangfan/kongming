# Kongming 路线图与能力域清单

> 来源:nl2sql-data-platform 的 41 个 specs(约 4300 行,经 38 个变更验证的产品蓝图)。
> 处置原则:**spec 只收有实现对应的行为契约**(openspec 同步可信原则),
> 未开工能力在此登记,开工时再立 spec——避免开局背 4300 行纸面债务。

## 第一梯队(已平移 ✅ 2026-08-16)

对话问答闭环:自然语言 → SQL → 执行 → 图表,反馈沉淀与评估。
12 个 spec 已改写入库(`openspec/specs/`):剥离旧实现绑定,对齐新 baseline
(UUID 主键、data_scope/sql_scope/feedback_status 字符串枚举、审计 verdict 语义)。

| 域 | 说明 |
|---|---|
| chat | 会话/消息持久化、多轮上下文、导出/EXPLAIN/图表 |
| nl2sql | 问答主链路、强制只读、自愈重试、内部接口鉴权 |
| nl2sql-agent | 多轮指代消解、生成质量规则、结构化洞察、schema 智能选取 |
| rag | 三路混合召回、文档上传、知识管理、成功 SQL 回写 |
| knowledge | 元数据 LLM 润色、reindex、覆盖优先级 |
| feedback ×3 | 提交/纠错沉淀 → 处置状态机 → 统计分析 |
| datasource | 动态数据源注册/路由(PG-only) |
| audit | 审计落库/查询/留存归档 |
| eval / ai-eval | Execution Match 匹配规则、基线管理、few-shot 自学习 |

## 第二梯队(rbac/multitenant/datascope/auth 全部入库 ✅)

| 域 | 说明 |
|---|---|
| rbac / multitenant / datascope | 已按新枚举(ALL/DEPT/SELF、SELECT_ONLY 等)改写入库,行为契约保留 |
| auth | 已与认证闭环实现同步入库(003/006 落地后随行 sync:账密登录/refresh
  轮换吊销/自助改密/联邦登录连接器/联邦账号关联) |

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

| 方向 | 地基(已落地) | 开工载体 |
|---|---|---|
| 问数智能体(王牌) | 一梯队 chat/nl2sql/nl2sql-agent + 三梯队 semantic-layer / query-streaming / cloud-code-sandbox | 088 报告生成——数据链路闭环到可交付报告 |
| 问答智能体 | rag 三路召回 / knowledge / document-rag / agent-memory / knowledge-qa | 093 知识侧深化(三态意图路由 + 融合问答)——已落地归档 |
| 通用智能体 | ReAct 内核 / agent-memory / multi-model-routing / prompt-guardrails / cloud-code-sandbox | 089 工具网关 + 090 任务内核(长任务)+ 094 web 检索域(联网搜索)——工具网关第二域已接入 |
| 多智能体 | workflow-orchestration(静态 DAG)/ custom-agent | 091 动态协作编排(宿主 = 090 任务内核)——选型指引:步骤可静态枚举→workflow;需运行时拆解/分歧裁决→collab(缺省深度 1,嵌套 opt-in) |
| 评测智能体 | eval / ai-eval(Execution Match、基线、few-shot 自学习)+ agent-observability / eval-agent | 092 评测智能体化(LLM-as-judge + 评测集自动构造)——已落地归档 |

依赖拓扑:088 / 089 / 090 相互独立、可并行;091 以 090 为宿主殿后。
**落地状态(2026-10-10)**:088–091 全部归档;092/093/094 已落地归档(问答=
三态意图路由+融合问答,评测=LLM-as-judge+评测集自动构造,通用=web 检索域
联网搜索;五方向全部有落地载体)。(091:拓扑配置面/协作账本/查询端点/
web 协作视图/端到端冒烟双场景)。任务/协作部署级上限缺省值见 docs/tech-stack.md
「任务/协作部署级上限」节。

### 089 工具网关:后续域接入清单(2026-10-09 登记更新 2026-10-10)

网关框架已就绪(域=清单+策略器+执行器;统一出站通道),新域接入=声明清单+实现策略器/执行器。
web 检索域已落地(094:供应商 tavily/bocha,端点与白名单代码单源派生,检索卡脱敏+
配额独立分桶)。剩余接入候选(开工时各自立 spec):HTTP 连接器域(用户自定义端点,
白名单/凭证治理面需先立约)、内部系统连接器(工单/日历等;写工具走既有确认流)。
MCP 出站维持不收编(mcp-interop 自有注册面,见 089 Non-goals)。

## 产品待办登记(backlog-settlement 终态化剥离,2026-08-25)

以下为产品路线待办(非技术债;触发=产品决策,开工时再立 spec):
STT/OCR 选型、真实支付通道 Channel 实现、可视化拖拽画布、MCP
resources/prompts 原语、Bot 版本化上架、个性化推荐、用户/TEAM 维度
配额、触发器失败重试策略、更多事件源(邮件/Slack)、中文全文 zhparser、
知识库(rag)团队归属(032①)。

## 已被治理文档覆盖,不再立 spec(D 层)

| 旧 spec | Kongming 对应 |
|---|---|
| contract-engineering | ADR-005 契约管线 + CI 漂移门禁 |
| stability | 宪法 Article III + tech-stack 工程纪律 |
| security-compliance | 宪法 Article IV(信任边界)+ ADR-002 |
| system-nav / web-responsive | web 主布局域开工时随行立 spec |

处置注记(2026-08-25 全量核对):
- **observability**:已作为第三梯队落地(038 agent-observability——用量/失败/延迟
  采集与看板+trace 时间线;「宪法 Article III 覆盖」的旧处置作废)。
- **rag-refine**:已被 037(单表知识编辑端点+alias)与 014(表名抽取/定位跳转)
  覆盖,不单独立 spec。
- **datasets**:由 008 动态数据源+009 rag+example-data 装载管线覆盖(第三梯队段)。
