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

#### Scenario: 引用可溯源

- GIVEN 知识型回答引用了文档内容
- WHEN 渲染
- THEN 引用处标注 source_ref 形态

### Requirement: 知识分流(最小拓扑)

问答图 SHALL 在 retrieve 之后新增条件分流:知识型意图(规则词表:是什么/为什么/政策/流程/口径/定义/规范/制度/办法)且文档通道命中时,走 doc_answer 节点——LLM 直接按文档内容生成文本回答(不经 generate/validate/execute,无 SQL 执行面),引用经 citation 标注;schema 通道是否命中不影响分流判定(实施期实证:真实知识问题常有 schema 模糊命中,「schema 空」双条件过严,意图词表+文档命中已足够收窄);既有 8 节点路径逐字不变(零回归);误分流 SHALL 只影响答法,不产生任何数据副作用。

#### Scenario: 知识型问题分流

- GIVEN 问「退货政策是什么」且知识库无表说明命中、文档库有退货政策
- WHEN 问答链路执行
- THEN 直接按文档内容文本回答(带引用),不生成 SQL,消息 status=SUCCESS

#### Scenario: 数据问题不分流

- GIVEN 问「有多少部电影」且 schema 召回命中
- WHEN 执行
- THEN 走既有 SQL 链路,与 024 之前一致

### Requirement: 文档管理与隔离

管理端点(列表/上传/删除/详情)SHALL 要求权限点 system:document:manage(仅 SUPER_ADMIN 种子覆盖),按 tenant+datasource 双过滤;非超管 403;伪造 datasource_key 同形拒绝。

#### Scenario: 非超管拒绝

- GIVEN 角色不含 system:document:manage
- WHEN 访问文档管理端点
- THEN 返回 403
