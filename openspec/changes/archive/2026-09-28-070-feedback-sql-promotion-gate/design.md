# 070-feedback-sql-promotion-gate 设计

## 当前链路

反馈提交通过 SQL 防火墙后先提交 chat_message，状态变为 PENDING；随后 FeedbackService 调用 RagService.writeback，把 correctedSql 作为 SUCCESS 写入 query_history。query_history 用 (tenant_id, datasource_key, md5(question)) 唯一键覆盖同问句样本。RAG 历史路只检查 SUCCESS 和 embedding，不识别样本来源。处置端点只改 chat_message 状态，不触碰 query_history。

## 决策

保留 query_history 作为唯一 few-shot 召回入口，在其中显式区分执行成功样本、已采纳反馈样本和来源未知的存量样本。反馈提交保留候选 SQL；处置 ADOPTED 才晋升，处置为其他状态或重新提交反馈时停用已晋升记录。

不新增候选表或第二套检索管线。chat_message 仍是反馈内容与处置状态的事实源；query_history 只保留执行成功样本和已采纳纠错样本的检索投影。

## 样本身份与唯一性

query_history 新增：

- source_type：VARCHAR + CHECK，取 EXECUTION_SUCCESS、FEEDBACK_CORRECTION、LEGACY。
- source_feedback_id：可空 UUID；FEEDBACK_CORRECTION 行必须关联唯一 feedback message。
- retrieval_enabled：BOOLEAN。执行成功样本默认启用；反馈样本只在 ADOPTED 时启用；LEGACY 行停用。

新增迁移把现有唯一索引 uq_query_history_tdq 改为仅约束 EXECUTION_SUCCESS 行；FEEDBACK_CORRECTION 行按 source_feedback_id 幂等 upsert。正常成功查询仍按现有 (tenant, datasource, question) 语义保留最新 SQL。反馈样本与同问句执行样本可并存，彼此不覆盖。

反馈样本 question 仍取同会话内该 assistant 消息之前最近一条 user 问句；tenant_id 与 datasource_key 取消息所属会话快照。ADOPTED 每次重新晋升都刷新样本 SQL、embedding、retrieval_enabled 和晋升时间。

## 提交与处置流程

### 用户提交

1. 校验消息归属和角色。
2. correctedSql 非空时执行现有 SQL 防火墙校验；校验失败时不写反馈、不改变样本。
3. 锁定反馈消息，按现有三态合并规则更新 rating/comment/corrected_sql，状态置 PENDING，清空处置者信息。
4. 同一事务停用该 messageId 对应的反馈样本并写 sys_oper_log。
5. 提交后失效目标租户+数据源的检索上下文缓存。此路径不调用 embedding，反馈提交不依赖 Agent 可用性。

### 管理员处置

1. 将目标反馈行加锁，取得当前 corrected_sql、会话问句、tenant 和 datasource 快照。
2. ADOPTED 且 corrected_sql 非空时，经既有 EmbeddingPort 为原始问句计算向量；embedding 失败则返回 503，反馈状态和 query_history 均保持原值，允许管理员重试。
3. 将反馈状态、操作日志和 query_history 样本变更放在同一 PostgreSQL 事务中提交。ADOPTED 对应幂等 upsert 并启用样本；PENDING、REJECTED、DISMISSED 对应停用样本。ADOPTED 但 corrected_sql 为空时只更新反馈状态，不创建样本。
4. 事务提交后精确失效目标租户+数据源的检索上下文缓存。缓存服务不可用时沿用现有 fail-open 与 TTL 上限。

同一 feedback message 重复 ADOPTED 不产生重复样本。用户再次提交会重置为 PENDING，并在同一事务内停用此前采纳的版本；新版本只有再次 ADOPTED 后才启用。

## RAG 召回

历史路只选择 status=SUCCESS 且 retrieval_enabled=true 的样本，并继续强制 tenant_id + datasource_key 双过滤。执行成功样本和已采纳反馈样本按问句去重：

1. 同问句存在启用的反馈纠错样本时，选择最近一次 ADOPTED 的版本。
2. 没有启用反馈样本时，选择该问句最近一次执行成功样本。
3. 对去重后的问句样本按 embedding 相似度排序后取现有 topK。

反馈样本仍作为独立强 few-shot 通道，不参与表说明两路 RRF；sample SQL 与 source_kind 不进入用户可见响应。

## 存量数据迁移

来源无法从现有 query_history 字段本身推断，迁移禁止把所有旧行直接宣称为执行成功：

1. 先将旧行标为 LEGACY 且 retrieval_enabled=false。
2. 对每条旧行，按 tenant、datasource、原始问句和 sql_text 精确匹配成功 assistant 消息及其前序 user 问句；唯一匹配到时恢复为 EXECUTION_SUCCESS 并启用。
3. 无成功执行匹配时，精确匹配当前 corrected_sql、前序问句、租户/数据源和处置状态；唯一匹配到时恢复为 FEEDBACK_CORRECTION，只有 feedback_status=ADOPTED 才启用。
4. 冲突或无法精确证明来源的行保留为 LEGACY 且停用；不做模糊匹配。apply 输出各分类行数，供回归与容量观察。

后续成功查询和新采纳反馈继续自然重建可信样本。现有 query_history 总量上限仍约束全部来源；停用 LEGACY 行按现有最旧优先淘汰策略逐步清理。

## 契约与边界

- 反馈请求字段和响应字段不变；处置端点新增 embedding 失败的 503 错误响应。
- 数据库变更走 Alembic 新迁移，遵守 ADR-004 的 VARCHAR + CHECK、UUID v7 和时间列约定。
- 503 纳入 OpenAPI 导出和生成类型，遵守 ADR-005。
- 修改只发生在 api 控制面；Agent 仍不持数据库凭证。Embedding 继续由 api 调用既有 Agent 内部端点。
