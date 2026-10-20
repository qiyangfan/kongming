# Proposal

## Why

当前四类向量数据固定为 1536 维,而阿里云新 embedding 模型可生成 2560 维向量。项目处于本地个人使用阶段,向量均可从保留的原文低成本重建,因此直接统一升级模型与存储维度,避免继续被旧维度限制。

## What Changes

- 将 schema 描述、历史问句、文档子块和用户记忆使用的 embedding 统一为 2560 维。
- 将四类向量列切换为半精度向量,并按半精度余弦距离索引与查询。
- 将 Agent embedding 请求固定为 Qwen3.7 文本向量模型并显式请求 2560 维。
- 提供 API 侧可重跑的批量重建命令,从现存文本重新生成所有派生向量并报告进度。
- 升级 embedding 缓存版本,避免新旧模型结果混用;重跑现有 RAG 评估,仅在结果证明必要时另行调整阈值。
- Schema 迁移只清空四个 embedding 列,保留知识文本、文档、记忆及其归属元数据。

## Non-goals

- 不替换 PostgreSQL/pgvector,不引入 Elasticsearch 或 Milvus。
- 不修改 embedding 输入文本、分块策略、召回融合策略、权限过滤或租户边界。
- 不修改 apps/agent 的数据库访问边界,Agent 仍只经 API 内部回调获取向量服务。
- 不为旧 1536 维向量提供在线兼容、双写或自动回滚恢复。
- 不调整 RAG 阈值,除非本地评估结果证明现值不再满足现有验收目标。

## Capabilities

### New Capabilities

<!-- 无新增行为能力。 -->

### Modified Capabilities

- `rag`: 表说明与历史问句 embedding 统一为 2560 维。
- `document-rag`: 文档子块 embedding 统一为 2560 维。
- `agent-memory`: 用户记忆及其召回查询 embedding 统一为 2560 维。

## Impact

- Schema: 新增 Alembic 迁移,引用 ADR-004;迁移清空旧派生向量,保留原文并通过重建恢复向量召回。
- Trust boundary: 保持 ADR-002 既有 API/Agent 边界,不引入 Agent 数据库凭证。
- API/Agent: embedding 模型、输出维度、请求批量上限、缓存版本与 SQL 类型转换需要同步。
- 数据流: 全量重建会把现存 RAG 文本和用户记忆发送至已配置的百炼 Workspace embedding 接口,沿用现有 Agent 出站路径。
- 运维: 迁移后需执行 API 侧重建命令;重建未完成前向量召回为空,全文召回仍可用。
- 评估: 新模型与半精度可能改变相关度分布,需要重跑已有 RAG 评估并决定是否重新标定闸门。
