# feedback 能力域 delta(014)

## MODIFIED Requirements

### Requirement: 反馈提交

系统 SHALL 提供 `POST /chat/messages/{messageId}/feedback` 端点,接收 rating/comment/correctedSql;
权限点 `chat:feedback:submit`(种子排序 6,DEVELOPER 推荐绑定,只增不摘);
JWT + X-Tenant-Id 租户白名单复校(与问答端点同构);归属校验 fail-closed
(消息 JOIN 会话,user_id + tenant_id 双匹配,未命中 403 同形——不区分
「不存在」与「无权」,不泄露信息;与 chat 域 404 同形惯例不同,本域立约
403);仅 assistant 消息可反馈(user 消息 400);重复反馈幂等覆盖。
传输层约束:rating 仅 'UP'/'DOWN';comment ≤500;correctedSql ≤4000。
提交落库:rating/feedback_comment/corrected_sql/feedback_time=now,
并将 feedback_status 置 PENDING(状态机 NONE→PENDING,见 feedback-refine);
重复提交=重新待处置(R1-O2):SHALL 同步清空处置三列(handler/handle_time/
handle_note),防「新 PENDING + 陈旧处置者」组合;
操作日志 `feedback.submit` 与业务同事务落 sys_oper_log(动作清单含
messageId 与 rating,不记 comment/correctedSql 内容——内容可经明细分页查)。

#### Scenario: 提交反馈并记操作日志
- GIVEN 用户持有权限点 chat:feedback:submit
- WHEN 提交 {rating: 'UP'|'DOWN', comment?(≤500), correctedSql?(≤4000)}
- THEN 反馈落库(rating/feedback_comment/corrected_sql/feedback_time),
  feedback_status 置 PENDING
- AND 操作记 sys_oper_log(feedback.submit,同事务)

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
- AND 重复提交重置 PENDING 并清空处置三列(重新待处置)
- AND 并发提交同一消息由行锁串行(SELECT ... FOR UPDATE 读现值后覆盖写)
- AND 纠错沉淀在事务提交后执行
- AND 清空纠错不删除已沉淀的 query_history 行

#### Scenario: 无权限点提交被拒
- GIVEN 角色无 chat:feedback:submit
- WHEN 提交
- THEN 返回 403

### Requirement: 纠错 SQL 沉淀 few-shot

correctedSql 非空时 SHALL 通过 SQL 防火墙校验(与问答主链路初检同一规则集:
只读 SELECT 根 + 保留表拒读 + 危险函数拒用 + 租户 schema 边界,租户上下文
取反馈消息所属会话的租户;不执行、不改写,仅校验);校验失败整体拒绝
(400,不落任何反馈列,不写 query_history);校验通过且与现值不同时写入
rag.query_history;沉淀记录被三路召回用作 history_sql few-shot;
embedding/写入失败不阻断反馈提交。

沉淀数据口径:question=同会话内该消息之前最近一条 user 问句(created_at
倒序首条;无问句则不沉淀);sql_text=correctedSql;status=SUCCESS;
datasource_key=消息所属会话落库 datasource_key(会话快照,与 EXPLAIN 同源;
spec「缺省=租户标识」为上游无会话形态,Kongming 消息必属会话,快照恒存在);
写通道复用 009 RagService.writeback(embedding 由该通道负责),召回链路零改动。

#### Scenario: 点踩并提交修正 SQL 沉淀
- GIVEN correctedSql 非空
- WHEN 提交
- THEN correctedSql 必须通过 SQL 防火墙校验(SELECT 只读约束同问答主链路)
- AND 校验失败整体拒绝(不落任何反馈列,不写 query_history)
- AND 校验通过且与该消息现值不同时:取同会话内该消息之前最近一条 user 问句,
  经 writeback 通道写入 rag.query_history(status=SUCCESS,
  datasource_key=会话快照值)

#### Scenario: 提交 DELETE 作 correctedSql 被防火墙拒绝
- GIVEN correctedSql 为写语句
- WHEN 提交
- THEN 防火墙拒绝(400),反馈列与 query_history 均无变化

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

实时问答响应 SHALL 含 assistant 消息的 messageId 与 datasourceKey(纯增字段,
老调用方不受影响);历史消息接口回显反馈状态;前端两路径保留 messageId。

响应模型纯增:`POST /chat/query` 响应补 `message_id`(assistant 落库消息 id;
persist=false 或无会话调用为 null)与 `datasource_key`(本次执行数据源快照)。
历史消息 `GET /chat/sessions/{id}/messages` 每条 assistant 消息补
`rating`(null=未反馈)、`corrected_sql`(null=无纠错)与 `feedback_comment`
(null=无留言)回显字段(comment 为点踩弹窗预填源,R1-S3)。

#### Scenario: 实时问答响应含 messageId
- GIVEN 实时问答响应
- THEN 含 message_id(无会话调用为 null)与 datasource_key,老调用方不受影响

#### Scenario: 历史消息回显反馈状态
- GIVEN 历史消息接口
- THEN 每条 assistant 消息含 rating/corrected_sql/feedback_comment 字段供回显反馈状态

#### Scenario: 前端两路径保留 messageId
- GIVEN 前端历史/实时两渲染路径
- THEN 均保留后端 messageId,无 messageId 的消息不渲染反馈入口

### Requirement: 前端交互

assistant 消息操作行 SHALL 展示赞/踩入口(lucide-react 图标,禁 emoji;shadcn
Button ghost 形态);已反馈消息回显反馈状态。点踩弹窗(shadcn Dialog)
收集 comment 与 correctedSql。无 chat:feedback:submit 权限的用户不渲染
反馈入口(与消息操作行既有权限语义一致)。

#### Scenario: 点赞立即提交
- GIVEN assistant 消息操作行展示赞/踩
- WHEN 点赞
- THEN 立即提交(rating=UP,comment/correctedSql 不传=保持现值)

#### Scenario: 点踩弹窗收集 comment 与 correctedSql
- GIVEN 点踩
- WHEN 弹窗收集
- THEN 预填已提交值或当前 SQL,可改可清空(清空提交即撤销纠错/留言)

#### Scenario: 已反馈消息回显状态
- GIVEN 已反馈消息
- WHEN 回显
- THEN 历史消息来自服务端字段,实时消息来自本地状态

### Requirement: 不变式约束

问答主链路、审计、导出、EXPLAIN、RAG 召回逻辑 SHALL 零改动;纠错 SHALL NOT 回写
table_descriptions(知识精修不受反馈影响)。

#### Scenario: 主链路零改动
- GIVEN 反馈功能引入
- THEN 问答主链路、审计、导出、EXPLAIN、RAG 召回逻辑零改动
- AND query_history 正常写入路径(成功问答自动沉淀)不变

#### Scenario: 纠错不回写知识库
- GIVEN 纠错 SQL 沉淀
- THEN 只进 query_history,不回写 table_descriptions
