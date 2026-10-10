# Proposal

## Why

现行评测体系(eval/ai-eval)只能对**确定性产出**打分:Execution Match 双路径、
Recall@K、基线红线。但 088 报告、090 任务结论、091 协作裁决、知识问答的文本
回答这类**非确定性产出**没有任何质量闸门——它们是平台对外交付物的一半,却
完全游离在回归防线之外;同时评估集纯手写(15-20 题/租户),扩题成本高,
知识侧(093)落地后文本回答的评估缺口会进一步放大。ADR-010 将评测智能体
定为五大方向之一(全方向质量闸门),本变更是该方向的开工载体。

## What Changes

- 新增 **LLM-as-judge 评分器**:对非确定性产出(报告摘要/洞察、任务最终
  结论、协作 worker 裁决、知识问答文本回答)按声明式 rubric 做结构化评分
  (verdict + 分项分数 + 依据摘录),judge 结果落库并可进基线对照。
- 新增 **评测集自动构造管线**:LLM 从 schema 知识/文档知识自动生成候选题
  (question + gold_sql + expected_tables + category),生成侧自洽验证
  (gold 可执行、编写规范校验、语义去重)后落**候选集目录**,人工审核
  通过才并入正式评估集(生成-验证-人审三段闸)。
- **定时门禁扩展 judge 抽检层**:ai-eval 既有定时门禁在 smoke Execution
  Match 之外,对非确定性产物抽样跑 judge,通过率落 artifacts 并与上次
  可比,劣化红线同口径适用。
- judge 与构造的 LLM 调用走评估 harness 侧 openai 兼容客户端
  (与 agent 面同协议、同路由),不新增进程、不新增部署单元。

## Capabilities

### New Capabilities

- `eval-agent`: 评测智能体域——LLM-as-judge 评分器(rubric 声明、结构化
  verdict、judge 运行记录与基线维度)、评测集自动构造(候选生成、自洽
  验证、人审并入)、judge 层定时门禁抽检。

### Modified Capabilities

- `ai-eval`: 「CI smoke 守护」需求扩展——定时门禁在既有 smoke Execution
  Match 层之外增加 judge 抽检层,judge 凭证缺失显式失败口径与既有
  LLM 凭证一致。

## Non-goals

- **不做 SQL 正确性的 LLM 判定**:SQL 对错仍走 Execution Match 确定性口径,
  judge 只评非确定性产出——两套口径不混用,防 judge 幻觉污染黄金指标。
- **不做无人审自动入集**:自动构造的候选题必须经人审并入,不做全自动
  评估集膨胀(题目质量是基线可信的前提)。
- **不做独立评测平台/SaaS**(ADR-010 否决项):评测智能体内嵌本平台,
  服务其余四方向,不对外售卖。
- **不做竞技场/多模型 pairwise 对打排名**:后续演进,本期仅单 judge
  rubric 评分。
- **不新增进程/消息队列**(宪法 Article I、ADR-002):judge 编排为
  harness 进程内执行 + DB 持久化,评估执行继续走真实链路真实端点。
- **不做 web 评审 UI**:人审第一期走候选集目录文件 + git 提审,
  产品化人审面待有真实使用负担后再立项。

## Impact

- **代码**:apps/api/evals(harness 扩 judge runner、自动构造管线、门禁
  编排);apps/api 新增 judge 运行记录持久化(新表,走 Alembic 新迁移,
  ADR-004);无 agent 面改动(judge 不经业务链路)。
- **LLM 凭证**:评估 harness 复用既有 LLM 环境变量(dashscope 直连同款),
  judge 缺凭证显式失败(与 ai-eval 既有口径一致)。
- **schema**:judge_run/judge_result 与构造候选记录新表(ADR-004:UUID v7、
  状态列 VARCHAR+CHECK、timestamptz)。
- **依赖**:无新第三方库;LLM 客户端沿用既有 openai 兼容实现。
- **关联**:eval 域评估集结构与 gold 编写规范**零修改**——自动构造产物
  以符合 eval 域既有结构为验收条件(引用不修改);093 落地后其文本回答
  自然进入 judge 评审对象清单。
