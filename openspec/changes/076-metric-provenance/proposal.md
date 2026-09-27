# 076-metric-provenance 提案:答案口径依据透出(语义层命中可见)

## Why

语义层(023)是一笔**治理投资**:指标口径按租户+数据源集中定义、三元唯一约束、
超管权限管理、`sql_template` 永不执行只作提示。它确实在影响每一个数据答案——
但它对用户**完全不可见**。

对数据问答,"这个数用的是哪个口径"是审计时的第一个问题。当前答案给出的
SQL 与结果表只展示 mechanics(`SUM(a)/SUM(b)`),不展示语义映射:哪两列被
认定为收入与成本、依据哪条治理定义、该定义上次修订是什么时候。
运营者维护了语义层,用户却无从知道它被用上了——治理投资的价值链在最后一环断开。

**关键事实:结构化命中本来就算出来了,然后被丢。** 已逐行核实
`apps/api/kongming/semantic/service.py:107-120`:

```python
metric_rows = await self._repo.list_metrics(...)      # 已 SELECT id 与 updated_at
metrics = [MetricDef(...) for r in metric_rows]        # 已构造完整领域对象
hits = match_metrics(question, metrics)                # 已算出命中列表
return format_relations_hint(relations), format_metric_hints(hits)   # 命中列表被丢弃,只剩字符串
```

`match_metrics` 返回的 `list[MetricDef]` 里有 `metric_id`/`updated_at`
(仓储 SQL 已选,只是领域模型没接),算完之后只剩格式化字符串走出函数。
所以本变更**不是新增计算、不是新增检索、不是新增依赖、不新增故障面**——
它只是"别丢已经算出来的东西"。

**与并行变更的边界(先声明,避免重复造轮子)**:`068-doc-answer-citations` 拥有**文档来源**契约(`sources`)。
本变更只覆盖**指标口径**,不碰它们的任何字段;且不与其"数据问答来源恒空"的
裁决冲突——那条针对*文档*来源,而口径定义在 SQL 与结果表里任何地方都看不到,
两者不是同一类证据。

## What Changes

1. **`MetricDef` 接住已有字段**:领域模型增 `metric_id` 与 `updated_at`
   (仓储 SQL 已 SELECT,零新增查询)。
2. **`enrich` 不再丢弃命中**:返回结构化命中列表与派生字符串;注入提示词的
   字符串 SHALL 由结构化命中派生(单一来源,杜绝两处口径漂移)。
3. **召回响应纯增 `metric_hits`**:与 `metric_hints` 同源、同批,不新增检索路。
4. **终态与落库**:agent state 纯增 → QueryOut 纯增 → 终态 data 携带 →
   `result_json` 纯增键;同步/流式/回显三路径同源;**老消息按空回显,不重算**
   (口径是链路输入,不可由终态反推)。
5. **前端口径依据面板**:`AssistantCard` 数据答案下方渲染可折叠
   「口径依据(N)」——指标名 + 口径表达式 + 修订时间;无命中不渲染。
   面板内指标名可跳 `/admin/semantic` 语义层管理页定位定义。

## Non-goals

- **skill-pack 术语/黄金示例的来源透出**:需 skillpack 域把"提示词字符串"
  改为结构化命中,属另一能力域。**重估条件(写死)**:术语被用户质疑
  「这个别名哪来的」≥2 次。
- **`relations_hint` 透出**:JOIN path 已在 SQL 里逐字可见,展示即信息重复。
- **`history_sql`(历史成功 SQL few-shot)透出**:它是生成技巧而非答案依据,
  展示会被误读成"这条数据来自历史查询"。
- **指标命中与答案主张的逐句对应**:需要主张分解(LLM-as-judge 域),
  `069-chat-quality-observability` 已把该域整体划界外,不在此处另开入口。
- **指标定义编辑 / 版本对比 / 变更历史**:管理台 semantic 页已有 CRUD,
  本变更只做"用上了哪条"的可见性。
- **历史消息补算**:同 026 artifacts 重算不适用——口径不可由终态反推,
  补算等于伪造依据。

## Capabilities

### Modified Capabilities

#### semantic-layer

- 「生成链路注入」扩展:召回响应除既有文字段外,SHALL 纯增指标命中的
  结构化列表(标识、名称、口径表达式、修订时间);文字段 SHALL 由该列表派生。

#### chat

- 新增「口径依据持久化与回显」Requirement:终态与消息档纯增指标命中列表,
  三路径同源,存量档缺失回空且不重算。

#### query-streaming

- 「终态一致性」的终态字段清单纳入指标命中列表。

## Impact

- **落点**:`apps/api/kongming/semantic/models.py`(`MetricDef` 两字段)、
  `semantic/service.py`(`enrich` 返回结构 + 派生)、`rag/router.py`
  (`RetrieveOut` 纯增)、`rag/models.py`、`apps/agent/kongming_agent/`
  (`apiclient.py` 解析 + `graph/state.py` 态 + `nodes.py` 组包 +
  `main.py` `QueryOut`)、`apps/api/kongming/chat/`(终态 + 落库 + 回显)、
  `apps/web/src/components/chat/assistant-card.tsx`(面板)。
- **契约**:终态新增字段 → 必须 `make contracts` 并提交产物(ADR-005),
  CI 漂移门禁兜底;前端类型由生成获得,禁手写。
- **ADR**:不涉及。ADR-002 信任边界不变(透出的全是已在提示词里的数据,
  零新增出网面);ADR-004 无 schema 变更(`result_json` 既有 JSON 档纯增键)。
- **无新增检索、无新增模型调用、无新增失败模式**:命中列表是既有计算的产物。
- **与 073-prompt-fingerprint 的关系**:两者都往 agent 面纯增字段,但字段
  语义与落点完全不相交,无合并必要,也无冲突。
