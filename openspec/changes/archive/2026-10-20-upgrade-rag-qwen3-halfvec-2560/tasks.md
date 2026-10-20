# Tasks

## 1. OpenSpec 与兼容性边界

- [x] 1.1 完成 proposal/design/spec deltas 并通过严格 OpenSpec 校验。
- [x] 1.2 确认所有向量读写路径均仍由 API 持有数据库连接,并更新相关行为契约。

## 2. 向量 Schema 迁移

- [x] 2.1 确认目标 PostgreSQL/pgvector 扩展支持 2560 维 halfvec HNSW;新增 Alembic 迁移,四张向量列切换为半精度 2560 维并重建余弦 HNSW 索引。
- [x] 2.2 明确 downgrade 恢复 baseline 的 vector(1536) 列类型/索引但不恢复旧派生向量。

## 3. 模型与写读路径

- [x] 3.1 Agent 使用 Qwen3.7,对每批显式传 2560 维并校验返回维度。
- [x] 3.2 API RAG 配置、内部回调校验、缓存版本、SQL cast/operator 全量同步。
- [x] 3.3 同步本地环境模板、模型文档及既有 embedding fixtures。

## 4. 全量向量重建与评估

- [x] 4.1 新增 API 侧可观测、按批提交、可重跑的全量向量重建命令。
- [x] 4.2 dry-run 已执行(dev 库 56 行:表说明 51+文档子块 5,分 4 批,零调用零写库);
      冒烟调用已通过:经 agent /internal/embedding 端点(直连百炼 compatible-mode,
      DASHSCOPE_API_KEY)实测 qwen3.7-text-embedding + dimensions=2560 返回精确
      2560 维(模型 ID 与维度契约双双实证)。
- [x] 4.3 全量重建已执行:dev 库四类存量 56 行(表说明 51+文档子块 5,另两表无存量)
      全部经 agent→百炼重建写回,库内 vector_dims 实测均为 2560;命令可重跑(幂等)。
- [x] 4.4 OpenSpec 严格校验(change 级)与静态检查(ruff/mypy/pyright/lint-imports)
      全绿;RAG 评估已执行:smoke 7/7、full 1/1(阈值判定通过)、multiturn 11/12
      (1 例 sakila_multiturn_001 为历史基线已记录的 LLM 多轮生成质量问题——
      DISTINCT+ORDER BY 方言错误,非本次回归;优于基线 10/12)。
