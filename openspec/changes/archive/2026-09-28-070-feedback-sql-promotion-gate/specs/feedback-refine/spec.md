# feedback-refine spec delta（070-feedback-sql-promotion-gate）

## ADDED Requirements

### Requirement: 处置状态控制纠错样本准入

反馈处置 SHALL 同步控制 correctedSql 是否参与历史 SQL few-shot。状态 ADOPTED 且 correctedSql 非空时，系统 SHALL 启用与当前反馈 messageId 关联的样本；状态为 PENDING、REJECTED 或 DISMISSED 时 SHALL 停用该样本。ADOPTED 但 correctedSql 为空时不创建样本。

状态变更、样本启停和 feedback.handle 操作日志 SHALL 在同一数据库事务内提交。事务失败时 SHALL 保持原反馈状态和样本状态。样本状态变更提交后 SHALL 精确失效相同 tenant + datasource 的 RAG 检索上下文缓存。

#### Scenario: 采纳纠错 SQL

- GIVEN 目标反馈状态为 PENDING 且 correctedSql 非空
- WHEN 管理员将状态设为 ADOPTED
- THEN 反馈状态、处置审计和关联样本启用同事务提交
- AND 后续 RAG 历史路可召回该样本

#### Scenario: 驳回或忽略纠错 SQL

- GIVEN 目标反馈存在已启用样本
- WHEN 管理员将状态设为 REJECTED 或 DISMISSED
- THEN 反馈状态、处置审计和样本停用同事务提交
- AND 目标检索上下文缓存被失效

#### Scenario: 重置为待处置

- GIVEN 目标反馈曾处于 ADOPTED 且样本已启用
- WHEN 用户再次提交反馈，状态重置为 PENDING
- THEN 已启用样本在同一反馈事务中停用，直至再次采纳

#### Scenario: ADOPTED 但没有纠错 SQL

- GIVEN 反馈存在但 correctedSql 为空
- WHEN 管理员将状态设为 ADOPTED
- THEN 反馈处置成功且不创建或启用历史 SQL 样本

#### Scenario: 晋升 embedding 失败

- GIVEN 管理员将带 correctedSql 的反馈设为 ADOPTED
- WHEN 既有 embedding 能力不可用
- THEN 处置返回 503
- AND feedback_status、操作日志和样本状态均保持变更前状态
- AND 管理员可重试处置

#### Scenario: 重复采纳幂等

- GIVEN 同一 messageId 已处于 ADOPTED 且样本已启用
- WHEN 管理员再次提交 ADOPTED
- THEN 样本按 messageId 幂等更新，不产生重复样本
