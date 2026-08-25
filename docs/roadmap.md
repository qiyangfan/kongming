# Kongming 路线图与能力域清单

> 来源:nl2sql-data-platform 的 41 个 specs(约 4300 行,经 38 个变更验证的产品蓝图)。
> 处置原则:**spec 只收有实现对应的行为契约**(openspec 同步可信原则),
> 远期能力在此登记,开工时再立 spec——避免开局背 4300 行纸面债务。

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

## 第二梯队(rbac/multitenant/datascope 已改写 ✅;auth 待与实现同步)

| 域 | 说明 |
|---|---|
| rbac / multitenant / datascope | 已按新枚举(ALL/DEPT/SELF、SELECT_ONLY 等)改写入库,行为契约保留 |
| auth | **与认证闭环实现同步编写**(ADR-003 自建认证流程与旧 Keycloak 契约根本不同,提前写必漂移) |

## 第三梯队(远期登记,开工时再立 spec,C 层)

数据 Agent 深化:tool-calling、mcp-interop、workflow-orchestration、cloud-code-sandbox
(NL2Insight 杀手锏)、agent-memory、multi-model-routing、document-rag、semantic-layer、
prompt-guardrails、custom-agent、query-streaming、conversation-artifacts、multimodal-input。

平台商业化:bot-store、developer-portal-billing、team-workspace、triggers-webhooks、datasets。

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

## 已被治理文档覆盖,不再立 spec(D 层)

| 旧 spec | Kongming 对应 |
|---|---|
| contract-engineering | ADR-005 契约管线 + CI 漂移门禁 |
| observability / stability | 宪法 Article III + tech-stack 工程纪律 |
| security-compliance | 宪法 Article IV(信任边界)+ ADR-002 |
| system-nav / web-responsive | web 主布局域开工时随行立 spec |
