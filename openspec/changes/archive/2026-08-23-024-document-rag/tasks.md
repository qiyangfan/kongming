# 024 document-rag 实施任务

## 1. Schema 与种子

- [x] 1.1 alembic 0013:rag.documents + rag.document_chunks(design D2;HNSW 部分索引 CHILD)
- [x] 1.2 权限点 system:document:manage(排序 18);种子断言;datasource 级联扩展(023 cleaner 扩两域)

## 2. 分块与存储(测试先行)

- [x] 2.1 失败测试:chunker 纯函数(标题层级父块/滑窗子块/overlap/空文档拒)
- [x] 2.2 失败测试:上传幂等(content_hash 跳过/同名覆盖)/类型与大小上限
- [x] 2.3 实现 chunker.py + repository(documents/chunks CRUD+级联)
- [x] 2.4 上传端点(复用 009 docparse/postForm 形态)

## 3. 检索与注入

- [x] 3.1 retrieve_docs 通道(向量+全文 RRF+父块回填 top-5;降级空)
- [x] 3.2 /internal/rag/retrieve 响应纯增 doc_hints;agent RagContext 扩字段+retrieve 透传
- [x] 3.3 检索测试(双通道隔离断言:文档命中不进 schema_hint)

## 4. 知识分流(agent 图)

- [x] 4.1 doc_answer 节点(DOC_ANSWER_SYSTEM 新撰写资产)+retrieve 后条件边+streams summary 特判扩展
- [x] 4.2 agent 测试:分流成立/不成立(doc_hints 空/schema 命中)两路;分流终态 SUCCESS 无 SQL

## 5. 管理面与验证

- [x] 5.1 管理端点(列表/上传/删除/详情)+403;make contracts
- [x] 5.2 web /admin/rag 页文档区(上传/列表/删除)
- [x] 5.3 真 LLM 冒烟(全过):上传 6 块幂等跳过;知识问答分流(无 SQL+引用标注[km-policy.md §2])+数据问答零回归;实施期修订=分流判定放宽(意图+文档命中即分流,schema 命中不作条件——真实知识问题常有 schema 模糊命中);评估复验随 5.5
- [x] 5.4 R1 独立评审:1 高(H1 embedding 无分批>64 子块静默死)+5 中(M1 数据源校验/M2 扩展名分档恒假/M3 ts_rank/M4 403 映射/M5 response_model)+10 低全处置;直证断言无条件化(ASCII 词全文路)
- [x] 5.5 HANDOFF + 挂账 + 归档
