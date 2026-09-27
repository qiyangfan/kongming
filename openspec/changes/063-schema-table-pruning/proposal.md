# 063-schema-table-pruning 提案:生成前相关表预筛

## Why

`meta_schema_max_tables=50` 是**上限不是裁剪**——agent 全仓无任何表选择逻辑(grep `prune` / `相关表` / `select_tables` 零命中)。对 50 张表的租户,每次 `generate` 都把全量 schema 注入提示词。

代价是双向的:

- **token 成本随表数线性增长**,且这部分成本对每一次问答都发生,是"按天出血"型浪费;
- **模型选表准确率随表数下降**(注意力稀释),而选错表直接导致错误 SQL 或 `NO_RELEVANT_TABLE` 误判——用户看到的是"未找到数据",真实原因是表太多挑花了眼。

现有缓解都是**事后兜底而非事前收敛**:043/046/051 的哨兵与两级语义补全、093 之前的 `meta_schema_max_tables` 上限、056 的 glossary/fewshot 资产。这些资产本身恰恰是**做表预筛的最佳输入**——术语、别名、指标名、人工黄金示例已经在仓里,只是没有反向用于缩小表范围。

## What Changes

1. **新增表级预筛**(纯函数、零 IO、零新增外部调用):输入 = 改写后问题 + skillpack 投影(glossary 术语/别名 + fewshot + metrics)+ 语义层指标,对表名、列名、表说明做加权打分,产出 top-N 注入 schema_hint(N 可配,缺省 12)。
2. **零命中保留既有哨兵路径**:预筛为空即维持现有 `NO_RELEVANT_TABLE` 行为,不新增终态、不进 selfheal;预筛 SHALL NOT 比全量注入更容易误判。
3. **可观测**:被裁表数、入选命中来源(命中哪条术语/指标)、阈值进退,随 trace 事件下发——使"为什么没选中我的表"可排查,而不是黑盒。
4. **开关 `KM_AGENT_SCHEMA_PRUNE`**(缺省开):关闭时退回全量注入(基线行为),用于对拍与应急回退。

## Non-goals

- **embedding 向量检索表结构**:需额外模型调用与索引治理;本轮优先用租户已有术语/指标资产(零新增依赖、零新增调用、零新增故障面)。
- **列级裁剪**:先表级;列级需考虑 SELECT 投影与 JOIN 链完整性,风险大于收益。
- **跨请求缓存 schema 摘要/打分**:不预建缓存层,命中率数据证明必要再立。
- **NL2Insight / 多步规划下的跨表组合优化**:属另一能力域。
- **动态自适应 N**:先固定可配值;自适应阈值属过度设计。
- **替换 sqlglot 或改 schema 内省通道**:schema 来源与解析不变,只改"取多少"。

## Capabilities

### Modified Capabilities

- nl2sql-agent:新增「相关表预筛」Requirement——生成环节的可用表结构上下文 SHALL 可经由问题驱动的表级预筛收敛,且带可观测、可回退、零命中不劣化三项约束。

## Impact

- **落点**:`apps/agent/kongming_agent/graph/nodes.py`(`format_schema_hint` 前置)、新预筛纯函数模块 + `settings.py` 一项、trace 事件字段。
- **agent 零凭证纪律不变**:预筛只用控制面已下发的 meta 与 skillpack 投影,**无新增回调、无新增出网**(ADR-002)。
- **无 schema 变更**(ADR-004);**无 OpenAPI 信封变更**——注入构成变化不改变响应结构;若新增 trace 事件字段则须 `make contracts`(ADR-005)。
- **依赖**:效果验证依赖 065 评估门禁(Execution Match / Recall@K 对拍),本变更不得在无对拍的情况下切 primary。
- **与 072 的分工**:本变更回答"注入哪些表"(收敛是**概率性**的),072 回答"拼出来不能超过多大"
  (上界是**可证明**的)。两者独立落地、独立回退,**不得合并**——本变更受 065 对拍门禁,
  而装配预算是纯安全闸,不该等对拍结果。
