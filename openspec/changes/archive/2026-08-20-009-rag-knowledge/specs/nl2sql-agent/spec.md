# nl2sql-agent 能力域 delta(009)

## MODIFIED Requirements

### Requirement: Schema 智能选取

retrieve 节点 SHALL 从控制面获取 schema 提示,不维护硬编码全表提示。
提示源按序降级:①RAG 已落地——SHALL 经 `POST /internal/rag/retrieve`(内部
令牌)取三路召回结果,把提示收窄到相关表(≤topK),并同时携带历史成功 SQL
(few-shot);②表说明召回零命中(该租户+数据源知识库为空)或召回服务失败时
SHALL 回退实时内省兜底(005 过渡形态保留为兜底——新租户/未抓取知识的数据源
零质量回归);③内省亦失败时 SHALL 降级为空提示并提示 LLM「无法获取表结构」
——不误导、不硬编码全表,生成失败属可接受降级。历史成功 SQL SHALL 由
generate 节点以独立「参考历史成功 SQL」段拼入用户提示(仅非空时;系统提示
模板资产不变),其示例优先级高于表说明内嵌示例。

#### Scenario: 过渡形态内省全量提示
- GIVEN 该租户+数据源知识库无任何表说明(新租户/未抓取),或召回服务不可用
- WHEN retrieve 节点执行
- THEN 回退内部元数据通道获取表/列/注释(表数截断保护),拼入 generate 的 schema 提示
- AND 不硬编码任何表名清单于 Agent 面代码或提示模板

#### Scenario: 内省失败的空提示降级
- GIVEN 回退内省亦失败(网络/租户非法/超时)
- WHEN retrieve 节点降级
- THEN 返回空 schema 提示,generate 提示 LLM「无法获取表结构」
- AND 可能生成失败 SQL 是可接受的降级(不误导,不硬编码全表)

#### Scenario: 多数据集场景提示收窄
- GIVEN RAG 已落地,数据集有 11 张表,问题只涉及 2 表
- WHEN retrieve 节点拿到 RAG Top-K 召回(K=5)
- THEN 喂给 generate 的 schema 提示只含相关表(≤5 张),SQL 通过率不降

#### Scenario: RAG 完全失败的安全降级
- GIVEN RAG 召回服务不可用(网络/超时/5xx)
- WHEN retrieve 节点降级
- THEN 回退实时内省;内省亦失败时返回空 schema 提示,提示 LLM「无法获取表结构」
- AND 可能生成失败 SQL 是可接受的降级(不误导,不硬编码全表)

#### Scenario: 历史成功 SQL 注入 generate
- GIVEN 召回结果携带历史成功 SQL(该租户+数据源下语义相似的历史问句)
- WHEN generate 节点拼接用户提示
- THEN 含「参考历史成功 SQL」段(位于表结构提示之后),系统提示模板不变
- AND 无历史命中时该段不出现,提示形态与 005 一致
