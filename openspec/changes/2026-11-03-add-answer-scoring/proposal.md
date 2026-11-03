# Proposal: add-answer-scoring(在线逐轮答案异步评分)

## Why

线上真实问答轮次目前只有两类质量信号:四态 `status`(结果形状:成功/无数据/
拒绝/失败)与稀疏的用户点踩——「答案内容答得对不对」没有任何模型判分。
离线评估(ai-eval/evalj)只覆盖预制评估集,LLM judge 抽检的 answer 对象
以 persist=false 现场生成,不评真实用户轮次;点踩率分母大、信号稀疏,
质量台无法回答「最近答得准不准」这类内容质量问题。对标调研(FoxESS
ai-chatbot 的图外异步评分系统)验证了「LLM 只做主张分解、分数由代码
确定性重算」的成熟形态:零侵入主链路、同分解必同分、可审计可复现,
且 Kongming 已具备全部落点(`resolved_question` 列、`/internal/*` 回调
通道、title/memory 后台任务范式)。

## What Changes

- **异步评分管线**:SUCCESS 轮终态落库后,后台任务(与标题改写/记忆提取
  同范式)组装评审输入并回调 Agent 面 LLM judge;judge **只输出结构化
  主张分解**(事实主张及支撑分级、子问题要点覆盖标记,不输出分数),
  控制面按确定性公式重算 0-100 分并落库——同分解必同分,LLM 输出的
  分数字段一律忽略。
- **评分公式**(代码单源):依据分 = 按主张重要度加权的支撑率,覆盖分 =
  已覆盖要点占比,总分 = 两维加权后经校准函数收敛;critical 主张无支撑
  时总分封顶(防幻觉轮虚高)。具体权重与常量属实现细节(见 design),
  spec 只锚定「确定性重算」行为。
- **证据装配**:数据轮以 `result_json` 行列集为证据(截断预算内),
  文档/融合轮以引用来源对应的文档摘录为证据(按 `source_ref` 取数);
  证据不足装配的轮次不评分。
- **降级与幂等**:judge 不可达/超时/输出不可解析 → 不落分仅日志,
  主链路零感知;每消息至多一条评分记录(重复触发幂等跳过)。
- **质量台可见**:轮次明细分页与详情携带评分与维度分解;汇总新增
  平均分与低分轮计数;评分作为既有租户收敛口径下的观测字段。
- **新表** `chat_turn_score`(消息 1:1,UUID v7 主键,分解 JSONB)——
  走新 Alembic 迁移(ADR-004);质量台端点响应扩字段后跑
  `make contracts` 同步前端类型(ADR-005);LLM 调用经 api→agent
  `/internal/*` 回调通道,控制面不持 LLM 凭证(ADR-002)。

## Capabilities

### New Capabilities

(无——评分是轮次质量观测的深化,归入既有 observability 能力域,
不另立新域。)

### Modified Capabilities

- `observability`:新增「答案异步评分」Requirement(触发范围/分解-重算
  分工/证据装配/降级幂等/开关);「轮次质量观测」Requirement 扩展
  评分字段的查询面(明细列/详情分解/汇总均分与低分计数)。

## Impact

- `apps/api`:chat 域新增评分后台任务与触发挂点(`_finalize_reply`
  终态落库后)、`AgentClient.score_answer` 回调方法、评分公式纯函数
  模块、`chat_turn_score` 仓储与迁移;quality 域端点扩字段。
- `apps/agent`:新增 `/internal/score-answer` 端点(内部令牌鉴权,
  复用 `/internal/rewrite-title` 同款防线)与 judge prompt 资产;
  主图零改动(评分完全图外)。
- `apps/web`:质量台列表评分列、详情抽屉分解展示。
- `contracts/openapi.json`:quality 端点响应扩字段,生成同步。
- 依赖:零新增第三方库。
- 迁移:一张新表,零既有表变更;`KM_ANSWER_SCORING_ENABLED=false`
  一键回零行为。

## Non-goals

- **ReAct/Bot 公开对话轮评分**:ReAct 轮证据形态为工具输出而非
  结构化行列/文档摘录,判据不公平;待证据装配方案明确后另行提案。
- **非 SUCCESS 轮评分**:NO_DATA/REJECTED/FAILED 轮无完整答案可评,
  不纳入(persist=false 的临时对话轮同样不评)。
- **拒答/改写型低质答案的语义检测**:依赖评分管线但属独立行为,
  不搭车;若后续立项另行提案。
- **管理台手动重评/评分阈值配置面**:v1 评分为一次性落库,低分阈值
  为代码常量;不建重评端点与阈值运营界面。
- **评分驱动的自动处置**:不告警、不工单、不回写反馈闭环;v1 只做
  观测信号,处置决策留给人(质量台点踩/纠错既有通道)。
- **离线评估体系变更**:ai-eval/evalj 契约零变化,两套互不感知。
