# ai-platform 对齐研究:差距反转与运行时裁决

> **本文定位**:回答一个问题——姊妹项目 `ai-platform`(Java,~/workspace/java/ai-platform)与孔明
> 是什么关系,差距在哪,运行时要不要换 AgentScope。调研日期 2026-10-18,结论基于两边仓库的
> 代码与 spec 实证(jar/源码级),非印象。
>
> **一句话结论:差距方向与直觉相反——不是"ai-platform 有而孔明没有",而是孔明 51 个 spec 已覆盖
> ai-platform 31 项提案的约 90%;真正的剩余差距只有图级 checkpoint 等两三项;运行时保留 LangGraph,
> 不迁 AgentScope Python(独立论证见 §5,含可证伪的换向触发条件)。ai-platform 建议冻结为预案。**

---

## 1. 背景

ai-platform 是 2026-10 立项的 Java 企业 Agent 平台(模块化单体 + DDD + openspec 宪法),路线图
0002–0032 共 31 个变更提案。立项时双方的分工假设是"孔明=问数产品,ai-platform=平台"。
本次调研以 ai-platform 的路线图为清单,逐项核对孔明 `openspec/specs/` 的实现现状,修正该假设。

## 2. 认知误判的来源

孔明 README 只展示问数王牌场景,导致"平台宽度"被系统性低估。实际上:

- `openspec/specs/` 现有 **51 个 spec**(roadmap.md 记载的 41 个 2026-08 基线后又新增),
  第三梯队 019–038 已于 2026-08-25 全部落地;
- 多智能体协作持续深化中:115 圆桌对等拓扑(2026-10-14)、091 分工名册(2026-10-15)均已归档;
- 治理体系完备:constitution、10 个 ADR、openspec 全流程、import-linter 信任边界守护——
  "ai-platform 带来纪律增量"的假设不成立。

## 3. 能力对照(ai-platform 31 项 → 孔明现状)

| ai-platform 提案 | 孔明对应 spec | 状态 |
| --- | --- | --- |
| 0002 platform(认证/RBAC/租户/审计) | auth、rbac、multitenant、datascope、audit、web-admin | ✅ 已落地 |
| 0003 model-catalog(模型目录/计量) | multi-model-routing、observability、docs/model-providers | ✅ |
| 0004 knowledge-rag | rag、knowledge、document-rag、external-knowledge | ✅ |
| 0005 tool-mcp-core | tool-calling、tool-gateway、mcp-interop | ✅ |
| 0006 conversation-messaging | chat、chat-suggestions、conversation-artifacts、multimodal-input | ✅ |
| 0007 agent-runtime(事件流/审批/恢复) | nl2sql-agent、query-streaming、**task-kernel**、runtime-resilience | ✅(不同实现,见 §4) |
| 0008 prompt-governance | prompt-guardrails | ✅(版本化部分较薄) |
| 0009 eval-honest | eval、ai-eval、eval-agent | ✅ |
| 0010 sql-guard(五层纵深) | sql-execution、nl2sql | ✅(孔明为源头) |
| 0011 data-agent | nl2sql、nl2sql-agent、report-generation、semantic-layer、knowledge-qa | ✅ |
| 0012 multi-agent | multi-agent-orchestration(圆桌/名册/critic 重派) | ✅ 持续深化 |
| 0013 workflow-orchestration | workflow-orchestration | ✅ |
| 0014 media-generation | — | ❌ 缺(owner 2026-10-18 裁决不做,见 §6) |
| 0015 skill-pack-market | skill-pack + bot-store | ✅ |
| 0016 trigger-center | triggers-webhooks | ✅ |
| 0017 governed-tool-gateway | tool-gateway | ✅ |
| 0018 content-safety | prompt-guardrails | ✅~ |
| 0019 llm-observability | observability(038) | ✅ |
| 0020 model-egress-governance | multi-model-routing + ADR-008 multi-protocol-egress | ✅~ |
| 0021 vector-scaleout(Milvus) | —(pgvector 单栈) | ❌ **缺**(登记候选) |
| 0022 developer-portal | developer-portal-billing | ✅ |
| 0023 interop-protocols | mcp-interop(平台 Bot 即 MCP Server 已生产验证) | ✅ |
| 0024 control-plane(AgentScope Service) | 不适用(Java/AgentScope 专属形态) | ➖ 无关 |
| 0025 feedback-loop | feedback ×3 | ✅ |
| 0026 knowledge-qa-fusion | knowledge-qa | ✅ |
| 0027 agent-memory | agent-memory | ✅ |
| 0028 semantic-layer | semantic-layer | ✅ |
| 0029 autonomous-task | task-kernel(自主任务宿主) | ✅ |
| 0030 custom-agent-store | custom-agent、bot-store | ✅ |
| 0031 team-workspace | team-workspace | ✅ |
| 0032 code-sandbox | cloud-code-sandbox(实现在 `apps/api/kongming/sandbox/`,控制面持凭证) | ✅ |

**净缺口:向量规模化(0021)一项 + JVM 交付形态本身。**
media-generation(0014)owner 裁决不做(2026-10-18),不再挂账;
0024 对孔明无意义(AgentScope Service 是 Java 侧概念)。

## 4. 孔明真正的剩余差距(诚实清单)

之前在 ai-platform 侧认定的"净新增能力"(审批、沙箱、多 Agent、可恢复),孔明**不是没有,
而是用了不同的实现形态**,且已进入 spec 契约面:

1. **执行中审批与暂停恢复:已有,任务步骤级。** `task-kernel` spec 实现了八态状态机
   (PLANNING / RUNNING / **AWAITING_APPROVAL** / PAUSED / INTERRUPTED / DONE / FAILED / CANCELLED)、
   审批门(步骤声明 `require_confirm` → 挂起 → 归属人审批 → 幂等/过期 SKIPPED)、
   检查点支撑暂停/恢复/重启续跑、预算耗尽诚实披露部分完成。
2. **图级 checkpoint:没有,且暂无必要。** NL2SQL 图(`graph/build.py`:understand → retrieve →
   generate → validate → execute → selfheal → summarize)是请求内跑完的确定性管线,
   未接 LangGraph checkpointer(`langgraph>=0.2.60`,仅用 StateGraph + 自定义流事件)。
   任务级的暂停恢复由 task-kernel 的状态机承担,粒度是任务步骤,不是图超步。
   **是否需要图级恢复,取决于产品是否出现"单步骤内长时执行、中断损失不可接受"的场景**——
   目前没有证据。
3. **图内 interrupt(HITL 挂起图本身):没有。** 问数的"人工把关"以 UX 形态存在
   (SQL 全透明可编辑再执行)与任务级审批门形态存在;图内挂起是第三种形态,登记候选。
4. 向量规模化:见 §3;media-generation 已否决(owner 2026-10-18 裁决)。

## 5. 运行时裁决:保留 LangGraph,不迁 AgentScope Python

**结论:不换。** 以下论证独立于现有架构成立(按"该推倒就推倒"的标准重新推演过):

**AgentScope Python 现状(2026-10,诚实承认其强)**:已进化为"Claude Code 式运行时"——
权限模式与规则、`RequireUserConfirmEvent` 的 HITL 确认、类型化事件总线、AgentState 持久化
(JSON 入 Redis/SQL,跨进程重建)、Goal Pipeline、Agent Service FastAPI 部署、Studio。
它是好框架,但它的形态是**自由 ReAct agent 产品**。

**不换的理由(需求相对,非惯性)**:

1. **形态不同构。** 孔明的核心是确定性审计管线:路由铁律(重试必经 selfheal)、权限类直通终态、
   错误分类计数、诚实空收口——这是"受治理的流水线",不是"给模型工具让它自己跑"的 ReAct 形态。
   AgentScope 的核心资产(ReActAgent/权限模式/自由工具循环)在这条产品线上用不上;
   用上即意味着把 41+ 变更验证过的行为契约重新证明一遍。
2. **平台能力已在自有契约面内实现。** 审批门/暂停恢复/预算/诚实披露都在 task-kernel,
   多 Agent 在 collab 域(圆桌/名册),事件流在 query-streaming——换运行时零新增用户可见能力,
   迁移面 = graph/ + collab 装配 + 全量 spec 回归,以周月计,纯风险。
3. **LangGraph 的差异化(checkpointer/interrupt)孔明尚未用到;若要用是增量接入**
   (`compile(checkpointer=AsyncPostgresSaver(...))` + `interrupt()`,天级),不必为此换框架。
4. **生态位**:LangGraph 是 Python agent 编排事实标准(1.x 稳定线、社区与文档厚度、
   LangSmith 可观测);AgentScope Python 更年轻,API 仍在演进。

**可证伪的换向触发条件**(出现任一才重启评估,否则不再讨论):

1. 产品转向**自由自主 agent**(开放目标、长程自由工具循环,超出任务步骤清单形态)
   → 届时同时评估 AgentScope Python 与 LangGraph prebuilt ReAct;
2. LangGraph 生态变动(OSS 授权收紧、破坏性 API churn 失控);
3. 步骤级恢复被生产证明不够(步骤内中断损失不可接受)→ **先接 checkpointer,而不是换框架**。

## 6. 给孔明的行动建议(登记不背债)

> **2026-10-18 owner 裁决覆盖本节**:本项目为求职学习项目,学习与展示价值即
> 立项依据;「登记不背债/待触发条件」姿态废弃——个人项目永远等不到触发信号。
> 下表「触发条件」列仅作历史记录保留,实际处置以 roadmap 第四梯队
> 2026-10-18 段为准:净缺口两项立项(图级检查点 → 向量规模化,工程依赖序;
> Milvus 开工先写 ADR)。media-generation(owner 同日再裁)**不做**,登记
> roadmap 否定裁决表。

| 项 | 动作 | 触发条件 |
| --- | --- | --- |
| 图级 checkpoint + 图内 interrupt | 登记第四梯队;开工时立 spec | 自主任务出现步骤内长时执行,或问数图需要挂起审批 |
| media-generation 域 | **否决**(owner 2026-10-18) | —(曾登记「有真实场景(报告配图?)」;裁决=不做,场景出现再议) |
| 向量规模化(Milvus 切换) | 登记评估 | pgvector 规模/QPS 触顶 |
| collab 域深化 | 按现有节奏(115/091 之后) | 无新条件 |
| 运行时 | **不动** | 仅 §5 触发条件出现时重启 |

## 7. ai-platform 的处置建议

1. **冻结为预案仓库**:停止投入,保留 openspec/宪法/ADR(尤其 ADR-0002 的
   Spring AI / LangChain4j / AgentScope Java 三框架对比,是 JVM 场景的有效研究);
2. **登记不背债**:若未来出现"Java 企业交付"的真实需求(JVM 技术栈团队集成),
   以 ai-platform 为种子重启,届时孔明 §3 对照表即为需求输入;
3. **不再向 ai-platform 平移需求**:本次对照证明其路线图与孔明重合约 90%,
   平行投入只会制造第二个影子项目。
