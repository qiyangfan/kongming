# Design: add-answer-scoring

## Context

现态:SUCCESS 轮终态在 `ChatService._finalize_reply` 单点收口(同步/流式
共用),落库后已有两个后台任务先例——`title_tasks.rewrite_session_title`
(标题改写)与 `memory_tasks`(记忆提取),形态均为「asyncio.create_task +
短会话读写分段 + AgentClient 回调 agent /internal/* + 任何失败静默收敛」。
agent 侧 `/internal/rewrite-title`、`/internal/extract-memory` 已是
「api 发起、agent 执行 LLM、内部令牌鉴权」的成熟通道。LLM 调用面
`deps.get_llm().chat` 支持 task 路由(multi-model-routing)。质量台
(quality 域)直接查 chat_message,已有 summary/page 两端点与租户收敛
(data_scope=ALL 口径,audit 同款)。

## Goals / Non-Goals

- Goal:真实 SUCCESS 轮获得密集、可复现、可审计的答案质量分;主链路
  零侵入(零新增延迟、零契约变化);降级静默。
- Non-Goals:见 proposal(ReAct/Bot 轮、非 SUCCESS 轮、手动重评、
  自动处置、离线评估变更)。

## Decisions

### D1 职责分工:judge 只分解,分数代码重算(本变更的立意之本)

LLM 直接打分的不可复现、不可审计是业界通病:同一答案两次评分漂移数十分,
扣分依据不可追溯。本设计把 judge 限定为**定性结构化输出**(主张/重要度/
支撑/覆盖),分数由 api 侧纯函数按公式重算——同分解必同分,质量台展示的
每一次扣分都能落到具体主张上。这与项目已有的确定性哲学同源:离线评估的
Execution Match 也是「模型产 SQL、代码比结果集」。公式入口统一做主张净化
(非事实语句不参与依据分),避免「回答很礼貌」这种主观句抬高依据分。

### D2 公式(v1 常量,单源在纯函数模块)

- 主张重要度权重:critical=1.0 / normal=0.6 / minor=0.3;
- 支撑分:fully_supported=1.0 / partially_supported=0.5 / unsupported=0.0;
- 依据分 G = Σ(impW×supW) / Σ(impW)(critical 无支撑时 G 直接取 0 且
  总分封顶 40——硬伤封顶);
- 覆盖分 C = 已覆盖要点数 / 要点总数(要点数为 0 时 C 取 G,单要点轮
  不因拆解粒度吃亏);
- 总分 = round(100 × (0.6×G + 0.4×C)²):平方校准抑制 LLM 分解普遍
  偏宽导致的虚高(0.8 → 0.64),拉开好坏轮差距。
- 常量收口在一个纯函数模块(`kongming/chat/scoring.py`,零框架依赖,
  宪法 Article II),全部带单测;调参只改一处。

### D3 评分管线落点与信任边界

- **api 侧**(chat 域):`scoring_tasks.py` 后台任务(形态同
  title_tasks:守卫读→LLM 调用池外等待→条件写);触发挂点在
  `_finalize_reply` 成功落库之后(与标题改写并列 `asyncio.create_task`);
  `AgentClient.score_answer` 回调方法(降级契约同 rewrite_title:
  任何失败返回 None,永不抛)。
- **agent 侧**:`/internal/score-answer` 端点(X-Internal-Token 鉴权,
  rewrite-title 同款防线),输入=装配好的评审材料,输出=结构化分解
  JSON(复用 parse_summary_json 既有解析器形态);judge prompt 为
  agent prompt 资产(task="ANSWER_SCORING" 走模型路由,部署可指到
  便宜档)。
- **信任边界**(ADR-002):LLM 调用 100% 在 agent 面,api 零模型凭证;
  评审材料由 api 装配(证据取数是 api 的既有职责——result_json 与
  rag 摘录都在控制面),agent 无状态执行。
- 证据截断:行列 ≤50 行/8KiB,文档摘录每来源 ≤500 字、≤8 来源——
  防 prompt 爆炸,截断不影响「主张是否被证据支撑」的判定效力
  (超长明细轮的主张通常引用统计口径而非逐行)。

### D4 存储:独立表 `chat_turn_score`,不进 chat_message

- 评分分解 JSON 体量大(每主张带原文摘录)且查询特征孤立(只被质量台
  按消息取),放 chat_message 会拖累高频问答热路径的行宽——独立表
  1:1(message_id UNIQUE),质量台按需 JOIN。
- DDL 遵循宪法 Article VI / ADR-004:UUID v7 主键、message_id UUID
  UNIQUE NOT NULL REFERENCES chat_message(id) ON DELETE CASCADE、
  score/grounding/coverage SMALLINT CHECK(0-100)、decomposition
  JSONB NOT NULL、score_model VARCHAR(64)、scored_at TIMESTAMPTZ;
  新迁移一次到位,零既有表变更。
- 证据材料不落库(装配即用,分解里已含主张摘录)——表不留证据副本,
  避免与 result_json 双源漂移。

### D5 质量台展示:字段扩量,零新端点

summary/page 既有端点扩字段(均分/低分计数;行含 score/grounding/
coverage),分解明细不进列表载荷——新增按消息取评分详情的只读子端点
(`GET /system/chat-quality/score/{message_id}`,权限点与既有两端点
相同),详情抽屉打开时单取,避免列表页膨胀。响应模型扩字段 →
`make contracts`(ADR-005);未评分轮三字段 null,前端呈现「未评分」
而非 0(spec 场景锚定)。低分阈值 60 为代码常量(与公式同模块)。

### D6 开关与降级

`KM_ANSWER_SCORING_ENABLED`(缺省 true;false 时触发点直接短路,
逐字回到现态)。judge 失败路径全部收敛 None+日志(warning 一条),
无重试队列、无死信——评分是增值信号,丢失可接受,重试只会放大
成本与复杂度(幂等以 message_id 唯一约束兜底,重复投递 SKIP)。

## Risks / Trade-offs

- [每成功轮 +1 次 LLM 调用成本] 异步执行、可开关、模型可路由到便宜档;
  展示型项目轮次量级下成本可忽略。
- [judge 误判(如中文口径类主张判无支撑)] v1 定位为观测信号不驱动
  自动处置,误判只影响均分观感;分解可见,误判可人工定位。
- [长答案主张多导致分解 token 膨胀] prompt 限定主张数上限(如 ≤12),
  超出时 judge 按重要度取舍;输入侧证据截断已在 D3 收口。
- [两变更叠加改 quality 端点](与 deepen-turn-observation)——本变更
  先行合并;后者在其 delta 之上叠加,任务中注明 sync 顺序。

## Migration Plan

一张新表(Alembic 新迁移,down 精确逆);既有表/契约零变更;开关
缺省开启但只在「新成功轮」上生效,存量轮次不回补评分(Non-goal 口径
之外的明确决策:回补无决策价值,历史轮已有点踩/处置状态)。

## Open Questions

(无——公式/落点/存储/降级均已收口;权重常量属实现期可调参,
不影响契约。)
