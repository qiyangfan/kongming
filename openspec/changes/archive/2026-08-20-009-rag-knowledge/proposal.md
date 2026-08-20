# 009 — RAG 三路召回 + 知识库

## Why

005 上线问答链路时 schema 提示源为「实时内省过渡形态」:每问全量拉表结构、
INTROSPECTION 裸注释对中文业务库信息量不足,且大库 50 表截断后召回质量不可控。
008 已把执行面/内省面/选项面按 datasource_key 动态化,本变更兑现路线图第二梯队
rag/knowledge 域:知识条目(润色表说明)成为 schema 提示的第一来源,检索从
「全量内省」收窄为「三路混合召回」,并以成功 SQL 回写形成 few-shot 自增强闭环。

## What Changes

- **知识库落地(pgvector)**:rag 两表为 baseline 既有资产(0001 整合上游最终
  态一次建全:rag schema、1536 维向量 + tsvector、HNSW + GIN、四元唯一键
  `(tenant_id, datasource_key, schema_name, table_name)`);新迁移 0006 做增量
  演进——source_type CHECK 枚举对齐 knowledge spec 五级优先级(MARKDOWN →
  INTROSPECTION/MANUAL_UPLOAD,空表瞬时)+ 召回过滤辅助索引
  `(tenant_id, datasource_key[, status])`。
- **执行面 rag 拒读防线(评审 B2)**:sql 防火墙保留表判定扩容——表引用的
  显式 schema 限定名为 `rag` 即拒(两表末段名不进清单:rag 不在任何执行
  search_path 内裸名无通路,且 query_history 等通用名是常见业务表名会误伤);
  009 灌入真实问句/SQL/表结构前闭合该面(sql-execution delta 立约,测试先行)。
- **api 新域包 `kongming/rag/`**(宪法分层):Markdown `## 表名` 切块解析(纯函数)、
  PDF/Word 解析为纯文本后复用同链路、幂等 upsert(embedding 文本=表名+表说明+字段说明)、
  三路召回(向量余弦 + tsvector 全文 + query_history 问句向量)、RRF 融合(k=60,
  表说明两路进融合;query_history 为独立强 few-shot 通道不进融合,上游同构)、
  Redis 双层缓存(emb 24h/ctx 10min,写操作 SCAN 精确失效)。
- **fetch-metadata + reindex(knowledge 域)**:对目标数据源内省全部表,逐表经
  Agent 面 `/internal/describe-table` LLM 润色(中文表/列说明 + schema 限定示例
  SQL;失败降级纯结构不阻断整批),按覆盖优先级 upsert;reindex=清空重建(幂等)。
  端点落位 `/system/rag/*`(租户视角 + system:rag:manage),datasource spec 原
  `POST /system/datasource/{key}/fetch-metadata` 路径表述随本变更加以修订。
- **内部召回端点** `POST /internal/rag/retrieve`(内部令牌,agent 回调):
  tenant+datasource 双过滤,embedding 失败降级纯全文路;表说明两路全空返回空结果。
- **agent 侧两个内部端点**:`/internal/embedding`(text→向量,零凭证纪律下
  LLM key 仅 Agent 面持有,api 回调获取)、`/internal/describe-table`(表结构→
  润色 JSON,输出防御清洗:真实列过滤/SELECT-only/长度截断)。
- **retrieve 节点切换**:schema 提示源从实时内省切换为「召回优先,零命中回退
  实时内省」——新租户/未抓取知识的数据源零质量回归(修订 rag spec 原空提示
  scenario);generate 的 user 模板追加「参考历史成功 SQL」段(SQL_GEN_SYSTEM
  资产不动)。
- **成功 SQL 回写**:chat 域查询 SUCCESS 且有结果时回写 query_history(问句向量
  复用 emb 缓存),失败/拦截不回写,回写失败不阻断主流程。
- **web 管理页**:`/admin/rag` 第五子页(知识列表/上传/删除/fetch-metadata/
  reindex),类型走 contracts 生成。
- **治理配套**:种子权限 `system:rag:manage`(排序 5);import-linter auth 防线
  猜测名 `kongming.rag_admin` 修正为真实包名 `kongming.rag` 并新增 rag 域包
  零框架契约;datasource spec「datasource_key 与租户归属不可改」scenario 从
  「元数据抓取」requirement 挪位到「数据源注册与凭证加密」requirement(008 挂账)。

## Capabilities

**New Capabilities**: 无(rag/knowledge 主 spec 已于平移期立库,本变更为其首次兑现)。

**Modified Capabilities**:
- `rag` — 上传格式与 source_type 命名落位、三路召回检索面契约细化、「空知识
  降级空提示」修订为「回退实时内省」、知识管理端点路径定形(manage 权限口径
  统一)。
- `knowledge` — fetch-metadata/reindex 端点路径与租户视角、覆盖优先级实现语义
  (AI_GENERATED 不覆盖人工精修)定形。
- `datasource` — 「元数据抓取」requirement 挪出至 rag 域(路径修订 + 引用),
  「key 与租户归属不可改」scenario 挪位到注册 requirement。
- `nl2sql-agent` — retrieve 节点提示源切换(召回优先+内省兜底)、generate 消费
  历史成功 SQL 段。
- `sql-execution` — 保留表拒读清单扩容(rag schema 两表)。
- `web-admin` — 管理区新增「知识库」第五子页。

## Impact

- **apps/api**:新 `kongming/rag/`(models/ports/service/repository/parser/router);
  internal 路由注册 retrieve;chat/service 回写接线;system seed 权限点;
  sqlfw 保留表判定扩容(rag 拒读);Alembic 0006(CHECK 枚举演进 + 辅助索引,
  手写迁移——autogenerate 不识别 vector/hnsw);pyproject import-linter 修正;
  conftest TRUNCATE 增表。**新依赖:pypdf、python-docx**(BSD/MIT,解析 PDF/Word
  为纯文本;不在 tech-stack 否决清单,选型理由见 design)。
- **apps/agent**:新内部端点 embedding/describe-table(llm.py 加 embed 方法);
  新 `describer.py`(润色 prompt 新资产,上游逐字移植纪律);graph retrieve/generate
  改造;apiclient 加 rag_retrieve;settings 加 embedding 模型配置。零新依赖
  (openai SDK embeddings API 已含)。
- **apps/web**:`/admin/rag` 页 + admin 子导航项;`make contracts` 重生成类型。
- **infra**:零改动(compose 与 CI 均已 pgvector/pg17 镜像,已核)。
- **信任边界(ADR-002)**:方向不变——api 持 DB 与向量库,agent 持 LLM key;
  api→agent 回调扩展两类(embedding/润色),延续既有内部令牌通道。
- **Schema(ADR-004)**:新表走新迁移 0006,列一次建全(含 FEEDBACK_REFINED 枚举
  留位);契约(ADR-005):新端点后 `make contracts` 重生成并同步。

## Non-goals

- **document-rag 文档问答通道**(父子分块 small-to-big、citation)——第三梯队
  远期域,本变更知识库仅覆盖 schema 通道(表说明)。
- **单表知识编辑端点**(rag-refine 域)——source_type 枚举留 MANUAL_EDIT/
  FEEDBACK_REFINED 值位与优先级,不建编辑端点;人工精修经上传链路覆盖。
- **语义层注入**(tableRelations/metricHints 提示字段)——semantic-layer 域远期。
- **ReAct 工具化检索**(上游 020 演进形态)——维持本仓 8 节点固定图。
- **旧 .doc 二进制格式**——仅支持 .docx;.txt/.md/.markdown 走 Markdown 链路。
- **query_history 去重/行数上限/TTL 清理**——沿用上游取舍(相似问句自然聚类),
  数据治理随 eval 域评估,挂账登记。
- **embedding 模型在线切换**——维度(1536)进迁移定死;换模型换维度需新迁移
  (类比 datasource 密钥轮换挂账),本期仅配置项化模型名。
- **会话-数据源绑定**(008 挂账⑤)——不变,随 chat 域后续变更。
