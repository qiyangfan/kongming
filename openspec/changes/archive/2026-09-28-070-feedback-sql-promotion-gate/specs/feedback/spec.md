# feedback spec delta（070-feedback-sql-promotion-gate）

## MODIFIED Requirements

### Requirement: 反馈提交

系统 SHALL 提供 POST /chat/messages/{messageId}/feedback 端点，接收 rating/comment/correctedSql；权限点 chat:feedback:submit；JWT + X-Tenant-Id 租户白名单复校。归属校验 fail-closed：消息必须属于当前调用者及当前租户；未命中返回 403 同形，不泄露消息是否存在。仅 assistant 消息可反馈，user 消息返回 400；重复反馈幂等覆盖。

传输层约束：rating 仅 UP/DOWN；comment ≤500；correctedSql ≤4000。

提交 SHALL 将 rating、feedback_comment、corrected_sql、feedback_time 落库并置 feedback_status=PENDING。重复提交 SHALL 清空处置者、处置时间和处置说明。操作日志 feedback.submit 与反馈变更 SHALL 同事务提交，日志 SHALL 记录 messageId 和 rating，不记录 comment/correctedSql 内容。

correctedSql 非空时 SHALL 在提交前通过与问答主链路相同的 SQL 防火墙校验。校验失败 SHALL 整体拒绝，不落反馈字段，不改变已晋升样本。

#### Scenario: 提交反馈并记操作日志

- GIVEN 调用者持有 chat:feedback:submit 且目标是其本人 assistant 消息
- WHEN 提交合法 rating、comment 和 correctedSql
- THEN 反馈字段落库，feedback_status 置 PENDING
- AND feedback.submit 与反馈变更同事务提交，日志不含 comment/correctedSql
- AND 本次提交的 correctedSql 不进入可召回 few-shot

#### Scenario: 跨租户/跨用户消息反馈被拒

- GIVEN 消息所属会话的 user 或 tenant 与当前调用者不匹配
- WHEN 提交反馈
- THEN 返回 403 同形错误且不写入数据

#### Scenario: 对 user 消息反馈被拒

- GIVEN 目标消息 role=USER
- WHEN 提交反馈
- THEN 返回 400 且不写入数据

#### Scenario: 重复反馈幂等覆盖

- GIVEN 同一消息已有反馈和处置结果
- WHEN 用户再次提交反馈
- THEN rating 和 feedback_time 以最新值为准；comment/correctedSql 的 null 表示保持现值，显式空串表示清空
- AND 状态重置为 PENDING，处置三列清空
- AND 同一事务停用该消息关联的已晋升样本
- AND 不因重复提交把 correctedSql 写入可召回样本

#### Scenario: 无权限点提交被拒

- GIVEN 调用者不持有 chat:feedback:submit
- WHEN 提交反馈
- THEN 返回 403

### Requirement: 纠错 SQL 沉淀 few-shot

correctedSql SHALL 先作为反馈候选保存在 chat_message。提交反馈本身 SHALL NOT 将候选写入可召回 query_history，也 SHALL NOT 调用 embedding。只有反馈处置状态为 ADOPTED 且 correctedSql 非空时，系统 SHALL 将其晋升为可召回的反馈样本。

样本问句 SHALL 取同会话内该 assistant 消息之前最近一条 user 问句；tenant_id 与 datasource_key SHALL 取消息所属会话快照。样本身份 SHALL 与执行成功 SQL 区分，并关联来源 feedback messageId；同问句反馈样本 SHALL NOT 覆盖执行成功样本。

#### Scenario: 点踩并提交修正 SQL 沉淀

- GIVEN correctedSql 非空
- WHEN 用户提交反馈
- THEN correctedSql 通过与问答主链路相同的 SQL 防火墙校验
- AND 校验通过后仅保存在反馈候选字段，状态为 PENDING，不写入可召回样本
- AND 校验失败整体拒绝，不落反馈字段，不改变已晋升样本

#### Scenario: 提交 DELETE 作 correctedSql 被防火墙拒绝

- GIVEN correctedSql 为写语句
- WHEN 提交反馈
- THEN 防火墙拒绝，反馈字段和已晋升样本均无变化

#### Scenario: 同一消息重复提交相同 correctedSql

- GIVEN correctedSql 与该消息现值相同
- WHEN 用户再次提交反馈
- THEN 反馈按幂等覆盖规则更新并重置为 PENDING
- AND messageId 关联样本停用，不重复创建可召回样本

#### Scenario: 沉淀写入失败不阻断反馈

- GIVEN embedding 或 query_history 写入能力不可用
- WHEN 用户提交 correctedSql
- THEN 反馈仍按 PENDING 成功落库，不调用 embedding 或 query_history 写入
- AND 样本晋升仅在管理员后续采纳时发生

#### Scenario: 沉淀记录被召回用作 few-shot

- GIVEN 反馈状态为 ADOPTED 且关联样本已启用
- WHEN 相似问题触发 RAG 历史路召回
- THEN 纠错 SQL 作为强 few-shot 返回
- AND PENDING、REJECTED、DISMISSED、重提待处置和 LEGACY 样本均不返回
