# 024 document-rag:文档知识库(父子分块与知识问答)

## Why

既有 RAG(009)只覆盖结构化知识(表说明/历史 SQL);业务文档(政策/流程/
口径/定义类非结构化文档)无通道——「退货政策是什么」这类知识型问题
在 NL2SQL 链路里答不了。本变更落地文档 RAG:父子分块存储、small-to-big
混合检索、chunk 级引用追踪(citation),并在问答图加最小知识分流
(知识型问题直接文本回答,不生成 SQL),文档与 schema 双通道隔离互不污染。

## What Changes

- **文档上传与父子分块**:.md/.txt/.pdf/.docx(复用 009 docparse);
  父块 ~800 字符按标题层级、子块滑窗 ~200 字符 overlap ~50;仅子块
  embedding(1536 维);content_hash(SHA-256)相同跳过重切(幂等);
  同名覆盖式更新。
- **存储**(Alembic 0013,rag schema):documents(文档级)+
  document_chunks(chunk_level=PARENT|CHILD,子块 parent_id 指父块,
  source_ref 定位 `{文档名} §{父块序号/标题}`);部分向量索引仅 CHILD;
  tenant+datasource 双维;数据源删除级联(与 023 同口径)。
- **检索(small-to-big)**:子块向量 top-K + 子块全文 top-K,RRF 融合
  (复用 009 fuseRrf),按 parent_id 回填父块(top-5);文档通道与
  schema 通道物理+逻辑双隔离。
- **citation 注入**:检索命中段带 source_ref;知识回答与数据问答的
  summarize 均可引用 `[文档名 §段落]`。
- **知识分流(最小拓扑)**:retrieve 后新增条件边——知识型意图(规则词表:
  是什么/为什么/政策/流程/口径/定义等)且 schema 召回为零而文档命中时,
  走 doc_answer 节点(LLM 直接按文档内容文本回答,不经 generate/
  validate/execute);既有 8 节点路径逐字不变(零回归)。
- **管理端点与 UI**:文档列表/上传/删除(/system/document/*),
  权限点 system:document:manage(排序 18);/admin/rag 页加文档区
  (与表知识并列,009 页扩展)。

## Capabilities

### New Capabilities

- `document-rag`: 文档上传与父子分块、存储结构、small-to-big 检索、
  citation、知识分流、管理与隔离的行为契约。

### Modified Capabilities

- `nl2sql-agent`: retrieve 文档通道与 doc_answer 分流节点的契约(既有
  路径零回归条款)。
- `chat`: 无(问答响应契约零变化;知识回答走既有 SUCCESS 形态)。

## Non-goals

- 不做文档预览/在线编辑;不做文档级权限(全租户可见,行级权限随
  multitenant 深化);不做多模态文档(图片/表格抽取)。
- 不做 LLM 兜底分类器的二次确认(规则词表初判 + schema 零命中双条件,
  误分流只影响答法不毁数据);自定义词表随运营。
- 不改 009 三路召回与 schema 通道任何语义。

## Impact

- **代码**:api rag 域扩展(chunker 纯函数/documents 仓储/检索通道/
  管理端点);agent retrieve 文档通道 + doc_answer 节点(图加一条条件边);
  alembic 0013;种子权限点;web /admin/rag 页文档区。
- **契约**:/system/document/* 端点入 OpenAPI;/internal/rag/retrieve
  响应纯增 doc_hints 段。
- **依赖**:零新依赖(docparse 复用 009 pypdf/python-docx)。
- **ADR 引用**:ADR-002(embedding 经 agent 回调)、ADR-004、ADR-005。
- **安全**:上传类型/大小上限沿用 009 分档;租户隔离 fail-closed;
  知识分流不绕过 SQL 防火墙(不生成 SQL 即无执行面)。
