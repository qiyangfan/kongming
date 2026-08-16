# rag 能力域

## Purpose

RAG 检索体系:文档上传 → embedding → pgvector 召回,替代 schema 硬编码提示。
覆盖:文档上传索引、三路混合召回(表说明向量 + 全文 + query_history)、知识库管理、成功 SQL 回写。

受影响权限层级: 接口级(system:rag:manage)+ 租户级(按 tenant 隔离检索)

## Requirements

### Requirement: 文档上传与索引

上传 Markdown 表说明,按 `##` 切块、生成 embedding(1536 维)后写入 rag.table_descriptions;重复上传幂等(upsert);无权限或超限被拒。

#### Scenario: 上传表说明 Markdown 成功索引
- GIVEN 用户已登录,角色有 system:rag:manage 权限,当前租户为 sakila
- WHEN 上传含「## customer」表说明的 Markdown
- THEN 按 `##` 切分解析出表块,生成向量写入 rag.table_descriptions(tenant/datasource/schema/table 维度)
- AND 返回 200 + 文档 id 列表

#### Scenario: 同表重复上传覆盖更新(幂等)
- GIVEN table_descriptions 已有 customer 表记录
- WHEN 再次上传含 customer 的文档
- THEN 触发唯一约束后改走 UPDATE,更新说明/示例/向量/时间戳,不产生重复行

#### Scenario: 无权限用户上传被拒
- GIVEN 用户角色无 system:rag:manage
- WHEN 上传
- THEN 返回 403

#### Scenario: 上传内容过大被拒
- GIVEN 上传文档 > 1MB(配置上限)
- WHEN 上传
- THEN 返回 400(文档过大)

### Requirement: 三路混合召回

retrieve 链路并行执行三路召回:①表说明向量召回(pgvector 余弦 topK)②表说明全文检索(tsvector,BM25 近似)③query_history 历史成功 SQL 向量召回;用 RRF(Reciprocal Rank Fusion)融合排序;跨租户+跨数据源隔离;各路无命中时逐级降级不报错。

#### Scenario: 表说明向量召回
- GIVEN 租户已有 customer/film/rental 表说明(已向量化)
- WHEN Agent retrieve 节点收到问句「查询所有客户」
- THEN 经控制面 /internal/rag/retrieve(内部令牌)做向量召回 topK(默认 5)
- AND customer 表说明相似度最高

#### Scenario: 精确表名经全文检索修正排序
- GIVEN 有「bed_bath_table」表说明,纯向量召回因语义偏移不准
- WHEN 提问「查询 bed_bath_table 数据」
- THEN 全文检索路精确命中该关键词
- AND RRF 融合后该表排名优于纯向量召回

#### Scenario: 历史成功 SQL 作为强 few-shot
- GIVEN query_history 有「查询客户邮箱」的成功 SQL
- WHEN 新提问「客户邮箱列表」(语义相似)
- THEN query_history 路召回该 SQL,作为强 few-shot 喂给 generate 节点(优先级高于 sample_queries)

#### Scenario: 跨租户+跨数据源隔离
- GIVEN sakila 与 olist 租户/数据源都有同名表说明
- WHEN sakila 上下文召回
- THEN 三路召回都强制按 tenant + datasource_key 双过滤,不返回他租户/他数据源内容

#### Scenario: 各路空命中降级
- GIVEN 某租户知识库为空(新租户)
- WHEN 召回
- THEN 对应路返回空,RRF 融合退化为可用路,不报错;retrieve 降级用空 schema 提示

### Requirement: PDF/Word 文档解析(扩展上传格式)

上传格式扩展支持 PDF/Word,解析为纯文本后复用 `## 表名` 切块 → embedding → upsert 链路;加密/损坏或超大文件被拒。

#### Scenario: 上传 PDF 解析成功并索引
- GIVEN 用户有 system:rag:manage 权限
- WHEN 上传含表说明文本的 .pdf
- THEN 服务端解析为纯文本,复用切块→embedding→upsert 链路,结果与 Markdown 上传一致

#### Scenario: 加密/损坏文件解析失败报错
- GIVEN 上传的 PDF 加密或损坏
- WHEN 解析异常
- THEN 返回 400(含原因),不写入知识库

#### Scenario: 超大文件被拒
- GIVEN 上传文件 > 10MB(PDF/Word 上限,含格式开销大于 Markdown 的 1MB)
- WHEN 上传
- THEN 返回 400(文件过大)

### Requirement: 知识库管理

提供当前租户知识条目的列表查询、删除(硬删)、重建索引能力。

#### Scenario: 列出当前租户的表说明
- GIVEN 用户已登录
- WHEN 分页查询知识列表
- THEN 返回当前 tenant(+可选 datasource 筛选)的条目(table_name/table_comment/source_type/updated_at)

#### Scenario: 删除某表说明
- GIVEN 用户有 system:rag:manage
- WHEN 删除某条
- THEN 硬删该行(知识库无需审计追溯),后续召回不再命中

#### Scenario: 重建索引
- GIVEN 知识有数据但向量过时(如换 embedding 模型后)
- WHEN 触发重建索引
- THEN 遍历当前租户+数据源所有条目,重新生成向量更新

### Requirement: 成功 SQL 回写 query_history

成功的查询回写 rag.query_history(tenant/datasource/question/sql_text/embedding);失败/拦截查询不回写,避免错误 SQL 被召回;回写失败不阻断主流程。

#### Scenario: 成功查询自动回写
- GIVEN 用户提问成功(status=SUCCESS)且执行有结果
- WHEN 审计落库
- THEN 同步回写 query_history(tenant, datasource_key, question, sql_text, status=SUCCESS, embedding 复用 question 向量,不二次调用 embedding)
- AND 回写失败不阻断主流程(仅告警日志)

#### Scenario: 失败/拦截查询不回写
- GIVEN 查询失败或被拦截
- WHEN 审计落库
- THEN 不回写 query_history(只记成功 SQL,避免错误 SQL 被召回污染 few-shot)
