# Proposal: tune-entity-recall-pool

## Why

同日 A/B 对照(add-doc-event-entity-recall 落地实测,基线见 evals/README
检索质量基线节)抓到首版实体路净负:实体开 0.938 / 实体关 1.000(-6.2pp)。
机制由检索试跑端点逐路归因:泛频实体(「逾期费」)以单一实体 join 出 6 个
子块挤占 RRF 融合池,把正确父块挤出 top-5。单实体命中只是词法共现,不是
SAG 语义的动态超边(超边=多实体共享才实例化的关联),放行其入池是首版的
过度并入口。owner 指令:调优,调优无效则下掉扩展路。

## What Changes

- 实体扩展路增设**入池门槛**(纯域语义,部署级开关与配额不变):仅共享
  ≥2 个查询实体的子块并入融合候选池;单一查询实体命中不并入(词法兜底与
  LLM 识别合并后实体数 <2 时整路短路为空,零出站零 join)。
- join 查询按共享实体数门槛过滤(仓库层 HAVING,截断配额语义不变)。
- 试跑端点实体路逐路展示不变(门槛过滤后为空=该路无候选,归因面仍可见)。
- evals retrieval 层复测对照:调优后实体开形态分数进 README 基线表新行
  (与实体关行同环境同条件),验证负收益消除。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `document-rag`: 「实体扩展召回与降级」requirement 增补入池门槛 SHALL
  (共享实体数 ≥2 才并入;单实体命中不并入);其余 requirement 不动。

## Impact

- `apps/api/kongming/rag/`: documents_repo(join 查询 HAVING)、
  documents_service(门槛常量+短路)。
- `apps/api/tests/`: join 门槛与短路测试更新/新增。
- `apps/api/evals/`: README 基线表调优后对照行。
- 契约产物零变化(无路由/模型改动);agent 进程零改动(ADR-002 不动)。

## Non-goals

- 不做实体路降权(RRF 加权需改共享融合纯函数签名,波及 nl2sql 三路,
  门槛已达目的则不引入)。
- 不做「两路候选不足才补缺」的条件执行形态(门槛过滤后扩展路已收敛为
  桥接语义,条件执行属另一档机制)。
- 不动抽取/落库/enrich 面(本变更只收召回并入口)。
- 调优后仍净负则转下掉处置,不在本变更内混合交付。
