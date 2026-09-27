# 070-feedback-sql-promotion-gate 实施任务

## 1. 测试先行

- [ ] 1.1 提交 correctedSql 后状态为 PENDING、反馈字段与审计正常，且 query_history 不新增或启用该 SQL
- [ ] 1.2 SQL 防火墙拒绝仍整体回滚；既有执行样本与反馈样本状态不变
- [ ] 1.3 重复提交将反馈重置为 PENDING，并停用 messageId 关联的旧样本；null/空串三态合并语义保持
- [ ] 1.4 ADOPTED + 非空 correctedSql 幂等晋升；ADOPTED + 空 SQL 不创建样本
- [ ] 1.5 PENDING/REJECTED/DISMISSED 均不召回；从 ADOPTED 转出后样本停用
- [ ] 1.6 embedding 晋升失败返回 503，反馈状态、审计和 query_history 无部分提交
- [ ] 1.7 同问句执行样本与反馈样本并存；召回只返回优先样本且最多一条
- [ ] 1.8 tenant + datasource 隔离、缓存精确失效及缓存失效故障降级均有覆盖

## 2. Schema 与存量迁移

- [ ] 2.1 新建 Alembic 迁移：query_history 来源类型、反馈 messageId、召回启用状态和必要 CHECK/索引；不改 baseline
- [ ] 2.2 将原唯一索引改为仅约束执行成功样本；新增反馈 messageId 幂等索引，覆盖同问句异来源并存
- [ ] 2.3 对存量行做精确来源恢复：匹配成功对话的行恢复为执行样本；精确匹配当前反馈的行按处置状态恢复；其余标记 LEGACY 并停用
- [ ] 2.4 迁移测试覆盖 UUID v7、约束、索引、来源恢复、歧义隔离和 downgrade 行为
- [ ] 2.5 Apply 记录迁移前后总量、执行样本、ADOPTED 反馈样本、LEGACY 停用行数量

## 3. 反馈处置与 RAG 实现

- [ ] 3.1 feedback 域经窄 Protocol 调用样本启停能力；领域 service 不直接依赖 SQLAlchemy/Redis
- [ ] 3.2 提交路径保留现有校验和反馈行为，移除即时 query_history 回写；重提时停用旧反馈样本
- [ ] 3.3 处置路径将样本启停与反馈状态、操作日志纳入同一数据库事务
- [ ] 3.4 RAG 执行成功回写显式标记来源，保持现有同问句覆盖语义
- [ ] 3.5 RAG 历史路仅召回可用来源，按问句优先选最近采纳的反馈样本，并在无反馈样本时回退执行成功样本
- [ ] 3.6 晋升或停用提交后复用 ContextCache 按租户+数据源精确失效

## 4. 契约与收口

- [ ] 4.1 处置端点声明 embedding 晋升失败的 503 错误；请求与成功响应字段保持不变
- [ ] 4.2 执行 make contracts，提交 OpenAPI 与生成类型产物；确认无非预期契约漂移
- [ ] 4.3 更新 feedback、feedback-refine、rag spec delta 与相应文档
- [ ] 4.4 手工冒烟：提交待审→检索不可见→采纳后可见→驳回/重提后不可见；同问句执行样本不被覆盖
- [ ] 4.5 运行 make api-check 与 make check，记录结果和受影响的评估集对拍
