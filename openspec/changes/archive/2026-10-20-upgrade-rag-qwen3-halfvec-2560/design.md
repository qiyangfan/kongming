# Design

## Context

现有 embedding 经 apps/agent 的 `/internal/embedding` 服务,API 将结果写入 schema 描述、query_history、document_chunks 与 user_memory。四处目前均使用 1536 维全精度向量;向量缓存键未区分模型。API 持有数据库连接,Agent 持有模型凭证,该信任边界必须保持。

本变更采用用户确认的本地个人项目策略:保留所有原始文本与元数据,丢弃旧派生向量,整库重新生成 2560 维向量。

## Goals / Non-Goals

**Goals:**

- 所有向量数据与查询向量具有完全相同的 2560 维度。
- 数据库存储和 ANN 索引统一使用半精度向量。
- 迁移和重建失败时可诊断、可重跑;不静默留下无法识别的未完成状态。
- API 与 Agent 的密钥和数据库信任边界不变。

**Non-Goals:**

- 在线双模型/双维度过渡、旧向量恢复、零停机发布。
- 向量库更换、稀疏向量、embedding 输入或召回算法改造。

## Decisions

1. **使用 Qwen3.7 文本 embedding,显式传 `dimensions=2560`。** 模型默认维度不作为契约;Agent 对供应商结果校验每条向量长度,API 回调再按 2560 做第二道校验。
2. **四张向量列统一为 `halfvec(2560)`。** 当前本地数据可廉价重建,半精度显著降低向量列和 HNSW 索引空间;无需额外保存全精度列。HNSW 索引与查询统一使用 `halfvec_cosine_ops` / 余弦距离。
3. **单次迁移清空旧 embedding 并换类型/索引。** 迁移保留每条原文和所有业务字段。升级时将四列改为 `halfvec(2560)` 并保持 NULL;降级时恢复 baseline 的 `vector(1536)` 类型并保持 NULL。两个方向都不尝试转换旧向量,因为升级后旧值维度不匹配且降级无法还原已清空的派生数据;要恢复召回应重新运行对应模型的重建命令。
4. **使用 API 侧重建脚本通过 `AgentRagClient` 调用现有内部 embedding 回调。** 脚本不持有模型凭证;Agent 不获得数据库访问。脚本按表分批读取原文、生成向量、写回对应行,记录行数/失败位置,每批事务提交;重复执行覆盖同一行向量。
5. **重建脚本支持 `--dry-run` 与按表筛选,但默认覆盖全部四类。** 每批写入前验证向量条数、维度及目标行数;遇到错误立即失败并保留此前成功批次,可安全重跑。
6. **新 embedding 缓存使用全新版本前缀。** 旧 Redis 缓存无须遍历删除,不会命中新模型请求。
7. **不预先变更相关度阈值。** 新模型可能改变相似度分布,按现有 eval 套件重新评估后再单独决策阈值。
8. **继续使用 PostgreSQL + pgvector。** 当前 `vector` 的 HNSW 索引上限为 2000 维,不能索引 2560 维;`halfvec` 的 HNSW 上限为 4000 维。阿里云 Qwen3.7 的 2560 维向量因此可沿用当前数据库与索引方案,无需引入第二套向量存储。

## Risks / Trade-offs

- 全量切换会使本地旧数据库在迁移后暂时没有可用向量,直到重建完成;全文路不受影响。
- 半精度可能影响近邻排序;需用现有评估集测 Recall/Execution Match,并据结果判断。
- Qwen3.7 的兼容端点必须支持 `dimensions`;若当前端点不支持,Agent 应明确报错而不是接受默认维度。
- 迁移 downgrade 不恢复旧向量内容;原文保留,回滚需要重建而不是依赖 Alembic 还原派生数据。
- 全量重建会把现有 RAG 文本和用户记忆发往已配置的百炼 Workspace;须使用现有 Agent 凭证与受控出站路径,不得把模型密钥移入 API。

## References

- `docs/adr/001-tech-stack.md`
- `docs/adr/002-trust-boundary.md`
- `docs/adr/004-schema-baseline.md`
- `openspec/constitution.md`
- 阿里云百炼模型详情: https://help.aliyun.com/zh/model-studio/qwen3-7-text-embedding (说明 `qwen3.7-text-embedding` 支持 256–2560 维)
- 阿里云百炼同步接口: https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api (OpenAI 兼容请求显式传 `dimensions=2560`,该维度仅适用于此模型;北京区域每批最多 20 条)
- pgvector 官方 README: https://github.com/pgvector/pgvector/blob/master/README.md (`halfvec` 支持最多 4000 维)
- pgvector 官方 HNSW 测试: https://github.com/pgvector/pgvector/blob/master/test/expected/hnsw_halfvec.out (HNSW 接受 `halfvec(4000)`,拒绝 `halfvec(4001)`)
