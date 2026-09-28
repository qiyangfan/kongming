# 070-feedback-sql-promotion-gate 提案：纠错 SQL 审核后晋升

## Why

当前用户提交 correctedSql 后，系统只做只读性、危险对象和租户 schema 校验，随即把 SQL 写成 SUCCESS 并加入 query_history。反馈状态此时是 PENDING。管理员后续设为 REJECTED 或 DISMISSED 时，只更新反馈状态，已进入召回的 SQL 不会撤回。

query_history 按租户、数据源和问句幂等覆盖。同一问句的反馈 SQL 可能覆盖实际执行成功的 SQL；query_history 又是高优先级 few-shot 源，后续同租户、同数据源的问答会持续受到影响。SQL 防火墙验证安全边界，不验证业务语义是否正确。

这使 PENDING / REJECTED / DISMISSED 与线上生效状态脱节。ai-chatbot 的问答观察结果先进入 FAQ 草稿，经过人工检查和发布校验后才进入线上索引；Kongming 的纠错 SQL 也应先成为候选，采纳后再影响生成。

## What Changes

1. 提交 correctedSql 时保留现有 SQL 防火墙校验和反馈落库，将反馈置为 PENDING；提交路径不再写入可召回的 query_history 样本。
2. 只有管理员将反馈设为 ADOPTED 时，纠错 SQL 才进入 few-shot 召回；设为 PENDING、REJECTED、DISMISSED，或用户重新提交反馈时，关联样本均停止召回。
3. query_history 增加来源和反馈消息关联信息。执行成功样本和反馈纠错样本使用独立幂等键，允许同问句同时保留两种来源，避免纠错覆盖执行成功样本。
4. RAG 对同问句候选去重：优先最新仍处于 ADOPTED 的反馈纠错样本；没有可用纠错样本时回退执行成功样本。
5. 晋升或停用后复用现有租户+数据源检索缓存失效机制。反馈状态、审计记录和样本准入在同一数据库事务内提交。
6. query_history 存量行只有在能从成功对话记录或当前 ADOPTED 反馈中精确证明来源时才继续召回；来源无法证明的行保留为 LEGACY 并退出召回。

## Non-goals

- 不新增反馈管理页面、评测中心、独立知识库或检索引擎。
- 不自动执行 correctedSql，不引入 LLM 评分或 SQL 语义验证器。
- 不改变正常成功查询的 query_history 回写、RAG 三路排序、NL2SQL 主链路或 Agent 信任边界。
- 不改反馈提交和处置端点的请求字段、响应字段及既有权限点。
- 不追溯恢复无法从现有对话记录证明的历史反馈样本；无法证明的存量行按 LEGACY 停用。

## Capabilities

### Modified Capabilities

- feedback
- feedback-refine
- rag

## Impact

- API 域：feedback service/repository、RAG service/repository/ports、检索缓存失效。
- Schema：对 rag.query_history 增加来源类型、反馈消息关联和召回启用状态；调整同问句唯一索引。按 ADR-004 使用新的 Alembic 迁移，不回写 baseline。
- OpenAPI：端点字段不变；ADOPTED 晋升的 embedding 失败返回 503，须按 ADR-005 更新并提交 contracts/openapi.json 与生成类型。
- 历史数据：迁移将未能证明来源的存量行标记为 LEGACY 并停用；apply 记录各来源迁移数量及剩余 LEGACY 数量。
- 不改变 apps/agent 的凭证边界；embedding 继续由 api 经既有 Agent 回调使用。
