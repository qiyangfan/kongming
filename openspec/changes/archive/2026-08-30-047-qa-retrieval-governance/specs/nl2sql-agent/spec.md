# nl2sql-agent spec delta(047-qa-retrieval-governance)

## MODIFIED Requirements

### Requirement: Schema 智能选取

retrieve 节点 SHALL 从控制面获取 schema 提示,不维护硬编码全表提示。
提示源按序降级:①RAG 已落地——SHALL 经 `POST /internal/rag/retrieve`(内部
令牌)取三路召回结果,把提示收窄到相关表(≤topK),并同时携带历史成功 SQL
(few-shot);②表说明召回零命中(该租户+数据源知识库为空)或召回服务失败时
SHALL 回退实时内省兜底(005 过渡形态保留为兜底——新租户/未扫描知识的数据源
零质量回归);③内省亦失败时 SHALL 降级为空提示并提示 LLM「无法获取表结构」
——不误导、不硬编码全表,生成失败属可接受降级。历史成功 SQL SHALL 由
generate 节点以独立「参考历史成功 SQL」段拼入用户提示(仅非空时;系统提示
模板资产不变),其示例优先级高于表说明内嵌示例。**请求携带多轮 history 时,
SHALL 以最近一条历史问句独立执行一路向量+全文召回,将其命中按序合并进融合
结果(主路命中优先,重复 (schema,table) 去重,总量封顶 2×top-K)**——多轮
追问经 understand 改写后语义完整,但召回对改写问句仍可能丢失上轮依赖表
(047 实证:追问「他们各自的邮箱」召回缺排名依据表 rental,模型越界引用
清单外表/列);上轮命中表是本轮查询的确定性上下文,继承 SHALL 是结构性行为
而非依赖向量相似度。ctx 缓存键 SHALL 并入历史指纹(末条历史问句),同问句
不同上下文 SHALL NOT 互串缓存。history 为空(单轮)时行为与单路召回逐字节
同形。

#### Scenario: 过渡形态内省全量提示
- GIVEN 该租户+数据源知识库无任何表说明(新租户/未扫描),或召回服务不可用
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

#### Scenario: 多轮追问继承上轮依赖表(047)
- GIVEN 第一轮问句「查询租赁次数最多的前 5 位客户」命中 customer/rental
- WHEN 第二轮追问「他们各自的邮箱是什么」(history 携带第一轮问句),
  本轮主路召回未命中 rental
- THEN 合并结果包含 rental(继承路补入)
- AND 生成 SQL 不再引用『可用表结构』清单之外的表

#### Scenario: 继承不重复且封顶(047)
- GIVEN 主路与继承路命中大量重叠表
- WHEN 合并
- THEN 重复 (schema, table) 只保留主路位次,总量不超过 2×top-K

#### Scenario: 单轮零回归(047)
- GIVEN 请求不携带 history
- WHEN 召回
- THEN 与单路召回行为一致(不发起继承路,缓存键与现状相同)

#### Scenario: 同问句不同上下文缓存不互串(047)
- GIVEN 同一问句分别以 history=[A] 与 history=[B] 请求
- WHEN 召回
- THEN 两次使用不同缓存键,各自命中各自的上下文

### Requirement: selfheal 错误分类(省 LLM 配额)

selfheal 按错误类型分类:权限/防火墙拦截类直接终止不重试;语法/执行错误才重试,节省 LLM 配额。**generate 组装喂回时 SHALL 按 PG 错误形态附加针对性修正指令**(047:泛化「请修正」使机械错同因连败,实证 missing FROM-clause 重试 2 轮同错):`missing FROM-clause entry for table "X"` → 指示补 X 的 JOIN 或改用已定义别名、不得引用『可用表结构』之外的表;`column "…" does not exist` → 指示只用清单内列并核对拼写、缺口径列不得近似顶替(046 列级哨兵的喂回镜像,SHALL NOT 引向表哨兵出口);`relation "…" does not exist` → 指示核对清单、确无数据只输出 NO_RELEVANT_TABLE(043 出口收窄到关系类);其余形态维持泛化喂回。

#### Scenario: 权限拦截不重试
- GIVEN 生成的 SQL 被防火墙拦截(FORBIDDEN_DML)
- WHEN 路由决策
- THEN 直接终止,不进入 selfheal,不浪费 LLM 重试配额

#### Scenario: 语法错误仍重试
- GIVEN 生成 SQL 有语法错误(非权限类)
- WHEN execute 失败
- THEN 进入 selfheal 重试(retry_count < MAX_RETRY),重试耗尽才终止

#### Scenario: 未定义别名错误获得针对性指令(047)
- GIVEN 上一轮 SQL 执行失败,error 含 `missing FROM-clause entry for table "s"`
- WHEN generate 组装喂回
- THEN user 提示附加「补 JOIN 或改用已有别名」类修正指令
- AND SHALL NOT 附加 NO_RELEVANT_TABLE 哨兵出口(别名错可修,不是弃答出口)

#### Scenario: 列不存在不误引表哨兵(047)
- GIVEN error 含 `column "email" does not exist`
- WHEN generate 组装喂回
- THEN 指令为列纪律(只用清单内列、核对拼写、不得近似顶替)
- AND 不出现「只输出 NO_RELEVANT_TABLE」指引(表存在,出口是改列不是弃答)

#### Scenario: 表不存在保持哨兵出口(047)
- GIVEN error 含 `relation "public.staff" does not exist`
- WHEN generate 组装喂回
- THEN 指令含「核对清单;确无数据只输出 NO_RELEVANT_TABLE」
- AND 关系类以外的 does-not-exist 形态(函数/类型等)维持泛化喂回,
  SHALL NOT 误引表哨兵出口

### Requirement: 文档通道与知识分流节点

retrieve 节点 SHALL 在三路召回之外并行取文档通道检索结果(doc_hints:父块全文+source_ref 列表;零命中为空);分流判定(规则词表+schema 空+文档非空)成立时进入 doc_answer 节点直接产出文本答案(summary=回答,无 SQL),否则走既有链路。doc_answer 的提示 SHALL 只含问句与文档段(全新撰写资产),答案 SHALL 以文档内容为准并标注 source_ref;文档段超限时按序截断。既有节点链路与 prompt 资产 SHALL NOT 变化;文档通道失败 SHALL 降级为空(既不影响 schema 通道)。**分流 SHALL 具多轮门槛:会话历史在场(history 非空)时,若问题仅由泛系系词「是什么」命中知识意图词表(强文档词——政策/流程/口径/定义/规范/制度/办法/怎么规定/为什么——零命中)SHALL NOT 分流,回 generate**(047 实证:多轮数据追问「这些影片各自的分类是什么」被文档通道劫持,用户得到「文档中未提及」;会话在场本身即数据语境,泛系系词不足以证明文档意图)。无历史(单轮)时分流行为与 024 语义逐字一致;036④ 附件分流条件不变。

#### Scenario: 分流节点零 SQL 面

- GIVEN 分流条件成立
- WHEN doc_answer 执行
- THEN 终态无 generated_sql、无执行回调,summary 为文档答案

#### Scenario: 文档通道失败降级

- GIVEN 文档检索服务异常
- WHEN retrieve 执行
- THEN doc_hints 为空,链路按无文档处理(与 024 之前一致)

#### Scenario: 多轮数据追问不被文档劫持(047)
- GIVEN 租户已有文档命中能力,会话存在上轮数据问答
- WHEN 当前问题为「这些影片各自的分类是什么」(仅「是什么」命中)
- THEN 路由 generate,生成 SQL 回答(不进 doc_answer)

#### Scenario: 多轮强文档词照旧分流(047)
- GIVEN 会话存在历史,文档命中非空
- WHEN 当前问题为「这些政策的适用口径是什么」(命中强词 政策/口径)
- THEN 路由 doc_answer

#### Scenario: 单轮 024 语义不变(047)
- GIVEN 无历史,文档命中非空
- WHEN 问题为「退货政策是什么」
- THEN 路由 doc_answer(与 024 行为一致)
