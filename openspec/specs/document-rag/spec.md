## Purpose

让平台沉淀非结构化业务文档(政策/流程/口径/定义),经父子分块与 small-to-big 混合检索支撑知识型问答(chunk 级引用可溯源),与 schema 知识通道双隔离互不污染。

## Requirements

### Requirement: 文档上传与父子分块

系统 SHALL 支持上传 .md/.txt/.pdf/.docx 文档(大小上限沿用既有分档:文本类 1MB、二进制 10MB):父块 ~800 字符按标题层级切分,子块 ~200 字符滑窗(overlap ~50);仅子块计算 embedding;同名文档覆盖式更新(先删后插),content_hash(SHA-256)相同 SHALL 跳过重切(幂等);解析失败/超限 SHALL 400 不静默。

#### Scenario: 父子分块结构

- GIVEN 上传一份带多级标题的 Markdown 文档
- WHEN 分块完成
- THEN 父块按标题层级聚合,子块滑窗覆盖父块全文,子块 parent_id 指向父块

#### Scenario: 同内容幂等

- GIVEN 文档已上传且内容未变(content_hash 相同)
- WHEN 再次上传同名文档
- THEN 跳过重切,块数不变

### Requirement: 文档块存储结构

文档存 rag.documents(名称/数据源/租户/content_hash/块数);块存 rag.document_chunks(chunk_level=PARENT|CHILD,子块 parent_id 非空,source_ref=`{文档名} §{父块序号或标题}`,embedding 仅 CHILD);向量索引仅覆盖 CHILD 行;tenant_id+datasource_key 双维隔离;数据源删除 SHALL 级联清理其文档与块。

#### Scenario: 存储结构正确

- GIVEN 一份文档上传完成
- WHEN 查 document_chunks
- THEN PARENT 行无 embedding,CHILD 行有 embedding 且 parent_id 指父块,source_ref 可定位

### Requirement: 文档检索(small-to-big + 混合召回)

文档检索 SHALL:子块向量 top-K + 子块全文 top-K,RRF 融合(复用既有融合函数),按 parent_id 集合回填父块全文(父块数有界,缺省 top-5);与 schema 通道(table_descriptions)SHALL 物理+逻辑双隔离(文档命中不进 schema_hint,表说明不进文档段)。

#### Scenario: small-to-big 回填

- GIVEN 问句命中某子块
- WHEN 检索执行
- THEN 返回其父块全文(非孤立子块片段),并带 source_ref

### Requirement: chunk 级 citation(引用追踪)

检索命中段 SHALL 携带 source_ref;知识问答与数据问答的自然语言产出引用文档内容时 SHOULD 标注 `[文档名 §段落]`;前端可据 source_ref 展开原文(管理面文档详情)。

**稳定引用键(068)**:文档检索命中 SHALL 为每个回填父块派生稳定引用键(可读前缀 + 定长哈希后缀;由父块标识确定性派生)。该键 SHALL 在任何一次召回中派生结果恒定(与命中名次/路数/请求次序无关),SHALL NOT 因文档改名或重新分块而改变。

**正文标注(068)**:回答引用文档内容时 SHALL 在句末以引用键标注(【引用键】,与下发来源逐字一致);SHALL NOT 输出「来源：」类包装形态;SHALL NOT 自造未下发过的键。

**编造净化(068,确定性、零模型调用)**:答案产出后 SHALL 扫描全部引用标记并两级匹配——全等于本轮合法键即保留;末段哈希后缀全等于某合法键后缀即保留(容忍人读前缀被改写);两者皆不匹配的**键形态**标记 SHALL 降级为纯文本(剥离括号、保留其内文字,不改写句子其余部分);非键形态的普通中括号文本 SHALL NOT 被剥离。合法键集合 SHALL 取自截断前的完整命中集;合法键为空时 SHALL 整体跳过净化。被剥离标记数 SHALL 计入可观测。净化 SHALL 先于输出护栏与落库完成。

#### Scenario: 引用可溯源

- GIVEN 知识型回答引用了文档内容
- WHEN 渲染
- THEN 引用处标注 source_ref 形态

#### Scenario: 编造键被降级为纯文本

- GIVEN 回答出现本轮从未下发过的键形态标记
- WHEN 净化执行
- THEN 该标记括号被剥离、其内文字保留为普通陈述,句子其余部分不变
- AND 被剥离计数计入 trace

#### Scenario: 前缀被改写但后缀合法时保留

- GIVEN 模型把 `员工手册-A7F2` 写成 `手册-A7F2`
- WHEN 净化执行
- THEN 按末段哈希后缀命中合法键,保留为有效引用

#### Scenario: 无合法键时不净化

- GIVEN 本轮文档检索零命中或降级为空
- WHEN 回答产出
- THEN 不执行净化,行为与引入本机制前一致

### Requirement: 知识分流(最小拓扑)

问答图 SHALL 在 retrieve 之后按 knowledge-qa 域三态意图路由分流:知识态
且文档通道命中时,走 doc_answer 节点——LLM 直接按文档内容生成文本回答
(不经 generate/validate/execute,无 SQL 执行面),引用经 citation 标注;
schema 通道是否命中不影响分流判定;融合态走融合问答路径(knowledge-qa
域);数据态走既有 SQL 链路。路由判定来源(LLM 三态 + 词表兜底)以
knowledge-qa 域口径为准。既有 8 节点路径 SHALL 逐字不变(零回归);误分流
SHALL 只影响答法,不产生任何数据副作用。

#### Scenario: 知识型问题分流

- GIVEN 问「退货政策是什么」且路由判定 knowledge、文档库有退货政策
- WHEN 问答链路执行
- THEN 直接按文档内容文本回答(带引用),不生成 SQL,消息 status=SUCCESS

#### Scenario: 数据问题不分流

- GIVEN 问「有多少部电影」且路由判定 data、schema 召回命中
- WHEN 执行
- THEN 走既有 SQL 链路,与 093 之前一致

### Requirement: 文档管理与隔离

管理端点(列表/上传/删除/详情)SHALL 要求权限点 system:document:manage(仅 SUPER_ADMIN 种子覆盖),按 tenant+datasource 双过滤;非超管 403;伪造 datasource_key 同形拒绝。

#### Scenario: 非超管拒绝

- GIVEN 角色不含 system:document:manage
- WHEN 访问文档管理端点
- THEN 返回 403
