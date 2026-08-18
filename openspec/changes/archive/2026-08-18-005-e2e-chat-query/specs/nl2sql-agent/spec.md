# nl2sql-agent 能力域(005 delta)

## MODIFIED Requirements

### Requirement: Schema 智能选取

retrieve 节点 SHALL 从控制面获取 schema 提示,不维护硬编码全表提示。
来源分两阶段:RAG 落地前(过渡形态)SHALL 经内部元数据通道取租户 schema
实时内省结果(表数截断保护,截断标注传给 generate);RAG 落地后 SHALL 用
召回结果把提示收窄到相关表。schema 来源不可用(内省或 RAG 失败)时 SHALL
降级为空提示并提示 LLM「无法获取表结构」——不误导、不硬编码全表,
生成失败属可接受降级。

#### Scenario: 过渡形态内省全量提示
- GIVEN RAG 未落地,租户 schema 内表数未超内省上限
- WHEN retrieve 节点执行
- THEN 经内部元数据通道获取表/列/注释,拼入 generate 的 schema 提示
- AND 不硬编码任何表名清单于 Agent 面代码或提示模板

#### Scenario: 内省失败的空提示降级
- GIVEN 内部元数据通道调用失败(网络/租户非法/超时)
- WHEN retrieve 节点降级
- THEN 返回空 schema 提示,generate 提示 LLM「无法获取表结构」
- AND 可能生成失败 SQL 是可接受的降级(不误导,不硬编码全表)

#### Scenario: 多数据集场景提示收窄
- GIVEN RAG 已落地,数据集有 11 张表,问题只涉及 2 表
- WHEN retrieve 节点拿到 RAG Top-K 召回(K=5)
- THEN 喂给 generate 的 schema 提示只含相关表(≤5 张),SQL 通过率不降

#### Scenario: RAG 完全失败的安全降级
- GIVEN RAG 召回服务不可用
- WHEN retrieve 节点降级
- THEN 返回空 schema 提示,提示 LLM「无法获取表结构」
- AND 可能生成失败 SQL 是可接受的降级(不误导,不硬编码全表)
