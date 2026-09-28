# 069-chat-quality-observability 提案:轮次级问答质量观测面

## Why

README 把「诚实评估、严格口径」当产品卖点,但**线上真实质量今天不可观测**。已核实的现状矩阵:

| 观测面 | 覆盖 | 缺口 |
|---|---|---|
| observability 域(035/038) | LLM 用量:调用/token/延迟/trace 时间线 | 只看成本与调用,不看问答结果质量 |
| feedback-analytics | 用户点赞点踩计数 + correctedSql 沉淀 | 滞后、覆盖率低(多数用户不评分)、无内容归因 |
| ai-eval / 065 | 离线金标准 Execution Match 回归 | 只覆盖评估集题目,不代表线上分布 |
| audit 域 | SQL 执行审计(PASS/BLOCKED/ERROR) | **NO_DATA 轮无执行、无审计行,任何形态下都不可见** |

具体盲区(均已代码核实):

1. **结果形态无切片**:`chat_message.status` 四态(SUCCESS/REJECTED/NO_DATA/FAILED,044/047 已建)零聚合消费——「哪个租户、哪个数据源 NO_DATA 率高」只能人肉翻库。
2. **selfheal 强度不可见**:`QueryOut.retry_count` 一路传到 `_finalize_reply`(chat/service.py:844)后被丢弃,不落库。
3. **多轮改写不可回放**:understand 节点的 `resolved_question` 只存在于 graph state(nodes.py:53-73),不落库;用户报障「它理解错了」时无据可查,多轮 eval 坏例也只能事后猜。
4. **prompt 无版本归因**:prompt 资产是 agent 侧代码常量,轮次与「哪版 prompt 产出」无关联;即便 065 定时门禁落地,线上质量切片也无法按 prompt 版本对比。

对照参考项目 ai-chatbot:其最成熟资产正是「轮次级分析日志 + 公式化评分 + 统一观测中心」——线上每一轮的质量可复核、可按渠道/身份切片。Kongming 的 NL2SQL 场景里执行类信号(verdict/重试/空结果)是比 RAG grounding 更强的主导质量信号,且**原料已全部在手**(`_finalize_reply` 单点已拿到 verdict/retry/trace),缺的只是落库与聚合两层。

## What Changes

1. **轮次终态落库四个质量信号**(`chat_message` 新列,一次 Alembic 迁移):`trace_id`(三表关联唯一键)、`retry_count`(selfheal 次数,现状丢弃)、`resolved_question`(指代消解改写结果,仅多轮改写实际发生时非空)、`prompt_version`(agent  prompt 资产指纹,agent 启动时计算、随 QueryOut 携带)。四字段为管理面诊断信号,**不进用户响应与 SSE**(query-streaming「逐字段同构」契约不破)。
2. **新能力域 `quality`**(router→service→repository,遵循分层铁律)提供两个只读端点:
   - `GET /system/chat-quality/summary`:总览(轮次四态占比、NO_DATA 率、拦截率、失败率、selfheal 率、点踩率、平均 token)+ 按天趋势(≤90 天,Asia/Shanghai 逐日补零,口径与用量趋势一致)+ 数据源切片 + prompt 版本切片;租户范围按权限收敛。
   - `GET /system/chat-quality/page`:轮次明细分页(时间/问题/改写问题/状态/重试次数/评分/trace_id),按状态、数据源、时间范围筛选——NO_DATA 轮次首次拥有可见形态。
3. **权限点 `system:chat-quality:view`**(rbac 域同款形态,SUPER_ADMIN 种子覆盖);前端新增管理页 `/admin/quality`(lazy,图表库体积治理同款)+ `ADMIN_NAV` 入口 + `PermRoute` 守卫。明细行动作 = 复制问题 / 跳转知识库管理页(NO_DATA 知识补全闭环的最小操作面,原 068-turn-status 的行动作并入此处,不再另立观测页)。
4. **契约**:新管理端点进 OpenAPI,`make contracts` 提交产物(ADR-005);agent→api 内部 DTO(QueryOut)纯增两字段,不触达用户面契约。

范围声明:本变更**同时覆盖**「轮次终态消费面」与「质量信号落库+切片」——两者共享同一数据事实源(`chat_message`)、同一管理页、同一权限点,拆成两个 change 会造出两套观测端点与两页重复 UI。原并行提案 `068-turn-status-observability` 的聚合/明细/NO_DATA 可见性目标已整体并入本变更,该目录已删除。

## Non-goals

- **LLM-as-judge 洞察评分(依据/覆盖/执行三维)**:明确划界外。理由:没有确定性基线的评分公式是把手段当目的,权重只能拍脑袋(ai-chatbot 文档里 16/57/72/90 分误伤史即证据)。**再进入条件(写死)**:本变更上线后,确定性切片证明「执行成功但用户点踩」的残差值得投入,且已积累 ≥30 例 Kongming 自有 badcase 可校准权重。届时方法论(LLM 只定性分解、分数代码重算、异步 fail-soft)整体迁移,数值不照搬。
- **ES / 独立分析库 / outbox 投影机制**:Kongming 单 PG 同步写没有乱序与写者竞争问题,引入即新增 moving parts,违背 tech-stack 宪法。
- **按用户行级切分与用户问责**:与 observability 域既定边界一致(管理面按租户/数据源聚合,不做行级按用户拆分)。
- **质量告警 / 趋势看板自动化**:人工看页即可,告警通道另立。
- **反馈处置与 correctedSql 沉淀流程**:feedback 域已闭环(含 few-shot 反哺),不动。
- **离线评估回归扩展**:065-eval-ci-gate 范畴,与本变更互补不重叠。

## Capabilities

### Modified Capabilities

- observability:新增「轮次质量观测」Requirement——轮次质量信号落库、四态聚合、明细分页、租户隔离与观测隔离边界;observability 域口径从「LLM 用量可观测」扩展为「用量 + 问答质量可观测」。
- chat:「问答查询端点」Requirement 修订——assistant 消息落库契约扩展四字段(存量 null/0 零回算),用户响应与 SSE 契约显式不变。
- web-admin:「管理面路由与页面守卫」扩展(/admin/quality 以 system:chat-quality:view 守卫)+ 新增「问答质量观测页」Requirement(标准件组装、DataView 双形态、行行动作)。

## Impact

- **迁移**:`apps/api/alembic/versions/0031_chat_quality_signals.py`(chat_message 四列 + created_at 聚合索引;ADR-004 同仓约定,与 060 无冲突——060 只动镜像构建与 Dockerfile)。
- **agent 面**:`kongming_agent/main.py`(QueryOut 纯增 resolved_question/prompt_version)、prompt 资产指纹计算(启动期一次)。
- **api 面**:新域包 `apps/api/kongming/quality/`(router/service/repository + import-linter 契约补条)、`chat/service.py` `_finalize_reply` 单点写四字段(同步/流式共用,零分叉)、`auth/seed.py` 权限种子、`main.py` 挂载。
- **前端**:`apps/web/src/pages/admin/QualityPage.tsx`(lazy)、`lib/admin-nav.ts`、`main.tsx`(PermRoute)、`lib/quality-api.ts`。
- **契约**:`make contracts` 提交产物(ADR-005)。
- **无新 ADR**:不改变信任边界(ADR-002)、认证(003)、schema 基线约定(004,既有表加列)、契约管线机制(005)。observability 域 Purpose 措辞随 spec sync 更新。
