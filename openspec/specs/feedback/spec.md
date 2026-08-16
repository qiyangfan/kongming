# feedback 能力域

## Purpose

用户反馈闭环:对话消息可评级/留言/提交纠错 SQL(归属校验 fail-closed、重复提交幂等覆盖),纠错 SQL 沉淀为 few-shot 反哺生成质量,messageId 打通消息与反馈。

## Requirements

### Requirement: 反馈提交

`POST /chat/messages/{messageId}/feedback` 接收 rating/comment/correctedSql;权限点 chat:feedback:submit;归属校验 fail-closed;仅 assistant 消息可反馈;重复反馈幂等覆盖。

#### Scenario: 提交反馈并记操作日志
- GIVEN 用户持有权限点 chat:feedback:submit
- WHEN 提交 {rating: 'UP'|'DOWN', comment?(≤500), correctedSql?(≤4000)}
- THEN 反馈落库(rating/feedback_comment/corrected_sql/feedback_time)
- AND 操作记操作日志

#### Scenario: 跨租户/跨用户消息反馈被拒
- GIVEN 消息所属会话的 user + tenant 未同时匹配当前调用者
- WHEN 提交反馈
- THEN 返回 403(fail-closed),不区分「不存在」与「无权」,不泄露信息

#### Scenario: 对 user 消息反馈被拒
- GIVEN user 角色消息
- WHEN 提交反馈
- THEN 返回 400,仅 assistant 消息可反馈

#### Scenario: 重复反馈幂等覆盖
- GIVEN 同一消息已有反馈
- WHEN 再次提交
- THEN rating/feedback_time 以最新为准
- AND comment/correctedSql 传 null(rating-only 提交)保持现值,显式传值才覆盖
- AND correctedSql 显式空串表示清空纠错
- AND 并发提交同一消息由行锁串行
- AND 纠错沉淀在事务提交后执行
- AND 清空纠错不删除已沉淀的 query_history 行

#### Scenario: 无权限点提交被拒
- GIVEN 角色无 chat:feedback:submit
- WHEN 提交
- THEN 返回 403

### Requirement: 纠错 SQL 沉淀 few-shot

correctedSql 非空时必须通过 SQL 防火墙校验;校验通过且与现值不同时写入 rag.query_history;沉淀记录被三路召回用作 history_sql few-shot;embedding/写入失败不阻断反馈提交。

#### Scenario: 点踩并提交修正 SQL 沉淀
- GIVEN correctedSql 非空
- WHEN 提交
- THEN correctedSql 必须通过 SQL 防火墙校验(SELECT 只读约束同问答主链路)
- AND 校验失败整体拒绝(不落任何反馈列,不写 query_history)
- AND 校验通过且与该消息现值不同时:取同会话内该消息之前最近一条 user 问句,生成向量后写入 rag.query_history(status=SUCCESS,datasource_key=消息结果快照值,缺省=租户标识)

#### Scenario: 提交 DELETE 作 correctedSql 被防火墙拒绝
- GIVEN correctedSql 为写语句
- WHEN 提交
- THEN 防火墙拒绝,反馈列与 query_history 均无变化

#### Scenario: 同一消息重复提交相同 correctedSql
- GIVEN correctedSql 与现值相同
- WHEN 再次提交
- THEN 反馈列覆盖,query_history 不重复新增

#### Scenario: 沉淀写入失败不阻断反馈
- GIVEN embedding/query_history 写入失败
- WHEN 反馈提交流程
- THEN 不阻断反馈提交(告警日志),反馈列已落库即算成功

#### Scenario: 沉淀记录被召回用作 few-shot
- GIVEN 已沉淀纠错 SQL
- WHEN 相似问题再提问
- THEN 修正 SQL 被三路召回用作 history_sql(强参考),召回/生成链路零改动

### Requirement: messageId 打通

实时问答响应含 messageId 与 datasourceKey(纯增字段,老调用方不受影响);历史消息接口回显反馈状态;前端两路径保留 messageId。

#### Scenario: 实时问答响应含 messageId
- GIVEN 实时问答响应
- THEN 含 messageId(无会话调用为 null)与 datasourceKey,老调用方不受影响

#### Scenario: 历史消息回显反馈状态
- GIVEN 历史消息接口
- THEN 每条 assistant 消息含 rating/correctedSql 字段供回显反馈状态

#### Scenario: 前端两路径保留 messageId
- GIVEN 前端历史/实时两渲染路径
- THEN 均保留后端 messageId,无 messageId 的消息不渲染反馈入口

### Requirement: 前端交互

assistant 消息操作行展示赞/踩入口(图标用组件库图标,禁 emoji);已反馈消息回显反馈状态。

#### Scenario: 点赞立即提交
- GIVEN assistant 消息操作行展示赞/踩
- WHEN 点赞
- THEN 立即提交

#### Scenario: 点踩弹窗收集 comment 与 correctedSql
- GIVEN 点踩
- WHEN 弹窗收集
- THEN 预填已提交值或当前 SQL,可改可清空(清空提交即撤销纠错/留言)

#### Scenario: 已反馈消息回显状态
- GIVEN 已反馈消息
- WHEN 回显
- THEN 历史消息来自服务端字段,实时消息来自本地状态

### Requirement: 不变式约束

问答主链路、审计、导出、EXPLAIN、RAG 召回逻辑零改动;纠错不回写 table_descriptions(知识精修不受反馈影响)。

#### Scenario: 主链路零改动
- GIVEN 反馈功能引入
- THEN 问答主链路、审计、导出、EXPLAIN、RAG 召回逻辑零改动
- AND query_history 正常写入路径(成功问答自动沉淀)不变

#### Scenario: 纠错不回写知识库
- GIVEN 纠错 SQL 沉淀
- THEN 只进 query_history,不回写 table_descriptions
