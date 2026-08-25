# 024 document-rag 设计

## Context

既有:009 rag 域(docparse pdf/docx、三路召回、RRF 融合、缓存);008
数据源校验;018 prompt 资产纪律。约束:既有 8 节点路径零回归(分流=
新增节点+条件边,不动既有边)、双通道隔离、零新依赖。

## Goals / Non-Goals

Goals:上传分块/存储/检索/citation/知识分流/管理端点+rag 页文档区。
Non-goals 见 proposal。

## Decisions

### D1: 分块器(纯函数,rag/chunker.py)

按标题层级切父块(^(#{1,6}\s) 标题行聚合,~800 字符软上限——超出按段落
再切);子块滑窗(200 字符步进 150=overlap 50);source_ref=
`{文档名} §{父块序号}{父块标题前 20 字}`;空文档/纯空白 400。

### D2: 表(0013,rag schema)

rag.documents(id uuidv7, tenant_id, datasource_key, doc_name, content_hash
VARCHAR(64), block_count INT, created_at;UNIQUE(tenant_id, datasource_key,
doc_name));rag.document_chunks(id uuidv7, document_id FK ON DELETE
CASCADE, tenant_id, datasource_key, chunk_level VARCHAR(8) CHECK
(PARENT/CHILD), parent_id UUID NULL, seq INT, source_ref VARCHAR(200),
content TEXT, tsv tsvector GENERATED(tsvector 与 009 同款 simple 配置),
embedding vector(1536);HNSW 部分索引 WHERE chunk_level='CHILD';GIN tsv)。
级联:document_id FK CASCADE + 数据源删除复用 023 SemanticCleaner 通道
(扩为 DomainCleaner 清两域)。

### D3: 检索(documents 通道,rag service 扩)

retrieve_docs(tenant_id, datasource_key, question)→embedding(agent 回调)
→子块向量 topK + tsv plainto_tsquery topK → rrf_fuse → parent_id 集合
回填父块(top-5)→ [{source_ref, content}]。失败降级空(不抛)。

### D4: /internal/rag/retrieve 响应纯增 doc_hints

RetrieveOut 加 doc_hints: list[{source_ref, content}] = [];响应组装调
retrieve_docs(agent retrieve 节点透传 state.doc_hints)。

### D5: 知识分流(agent 图最小拓扑)

graph/build.py:retrieve 后条件边——`_route_after_retrieve(state)`:
doc_hints 非空 and schema_hint 空 and 意图词表命中 → "doc_answer";
否则 "generate"(既有边改条件边,既有路径不变)。doc_answer 节点
(nodes.py 新函数,新 prompt 资产 DOC_ANSWER_SYSTEM 全新撰写):
LLM 按 doc_hints 回答,summary=答案;终态 execution=None——
_build_query_out 的 success=False?!——分流终态需要 success=true 无 SQL。
处理:doc_answer 写 execution=ExecutionResult(verdict="PASS", columns=[],
rows=[]) + summary;SUCCESS 语义(022 纯文本同款先例)。
意图词表(models 常量):是什么/为什么/政策/流程/口径/定义/规范/制度/
办法/怎么规定的。
流式:doc_answer 挂 stage_wrapper(自动 stage 事件;summarize 事件?
doc_answer 完成发 summary 事件——stage_wrapper 的特判只对 summarize。
doc_answer 加入特判或复用:stage_wrapper 的 summary 特判改
name in ("summarize", "doc_answer")——streams.py 小改)。

### D6: 管理端点

GET /system/document/documents?datasource_key=(列表)+ POST
/system/document/upload(multipart,复用 009 postForm)+ DELETE
/system/document/documents/{id}+ GET /system/document/documents/{id}/chunks
(详情含 source_ref 定位);system:document:manage 排序 18;web 在
/admin/rag 页加「文档」区(上传/列表/删除)。

### D7: 迁移与种子

0013 两表;权限点 system:document:manage;datasource 删除级联扩展
(SemanticCleaner→DomainCleaner:加 documents/document_chunks 清理;
023 的 cleaner 调用点不变,实现扩展)。

## Risks / Trade-offs

- [分流误判(知识词表命中数据问题)] → 双条件(schema 空+文档非空)
  收窄;误判只影响答法无数据副作用;词表观察迭代。
- [分块质量(中文标题层级缺失)] → 父块退化为纯长度切分;语义完整性
  靠 overlap;观察。
- [tsv simple 中文局限(009⑫同款)] → 向量路为主,全文路补充;登记。
- [doc_answer 无数据源上下文(文档是租户+ds 维度)] → 检索已带维度;
  回答不涉数据,无泄漏面。

## Migration Plan

1. 0013 两表(零存量);2. 种子权限点;3. 滚动重启(api 先起,agent
   旧版忽略 doc_hints 字段零影响);4. 回滚=表留库,分流永不触发
   (doc_hints 空)。

## Open Questions

- O1:文档与 schema 双命中时的融合优先级(v1:schema 优先不分流;
  融合策略随观察)。
- O2:大文档(>100 块)上传时长(同步端点;异步化随 009②)。
