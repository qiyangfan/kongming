# chat 能力域 delta(014)

## MODIFIED Requirements

### Requirement: 问答查询端点

系统 SHALL 提供用户面问答端点:JWT 认证 + 权限点 `chat:execute` 校验 +
X-Tenant-Id 租户白名单校验(缺省取用户默认租户,未绑定拒绝,SUPER_ADMIN
不豁免)。请求 SHALL 携带 question;SHOULD 携带 session_id(缺省自动新建会话,
标题规则见「会话持久化」requirement 的两阶段生成)、persist(缺省 true)、
history(显式多轮上下文)、datasource_key(缺省 default)。系统 SHALL 为每次问答
生成 trace_id 并贯穿 Agent 面与执行审计。响应 SHALL 携带 session_id、success、
verdict、summary、insights、stats、columns、rows、sql、chart(图表配置建议)、
retry_count;失败时 SHALL 携带友好 error,不暴露内部堆栈。响应 SHALL 纯增携带
message_id(assistant 落库消息 id,供反馈提交引用;persist=false 或无会话调用
为 null)与 datasource_key(本次执行的数据源快照)——纯增字段,老调用方不受
影响(feedback spec「messageId 打通」)。消息落库 status 三态映射:
verdict=PASS → SUCCESS;verdict=BLOCKED → REJECTED;verdict=ERROR 或重试
耗尽 → FAILED。Agent 面不可达/超时时 SHALL 返回友好错误并落 FAILED 消息
(persist=true 时),不裸 500。

#### Scenario: 有权限用户提问成功
- GIVEN 已登录用户角色含 chat:execute,租户 default 已绑定
- WHEN 提交问题(无 session_id)
- THEN 自动新建会话并返回 session_id,user 问句与 assistant 结果各落一条消息
- AND 响应含 success=true、verdict=PASS、summary/columns/rows/sql/chart
- AND 响应含 message_id(assistant 消息 id)与 datasource_key
- AND 执行审计的 trace_id 与本次问答 trace_id 一致

#### Scenario: 无 chat:execute 权限拒绝
- GIVEN 已登录用户角色不含 chat:execute
- WHEN 提交问题
- THEN 返回 403,不新建会话、不落消息、不回调 Agent 面

#### Scenario: 未认证拒绝
- GIVEN 请求无有效 JWT
- WHEN 提交问题
- THEN 返回 401

#### Scenario: 未绑定租户拒绝
- GIVEN 请求 X-Tenant-Id 指向用户未绑定的租户
- WHEN 提交问题
- THEN 返回 403,不回调 Agent 面

#### Scenario: 权限类拦截落 REJECTED
- GIVEN 问答链路最终 verdict=BLOCKED(生成 SQL 被防火墙拦截且按契约不重试)
- WHEN 响应返回
- THEN success=false,assistant 消息 status=REJECTED,响应含友好提示

#### Scenario: Agent 面不可达
- GIVEN Agent 面进程宕机或调用超时
- WHEN 提交问题
- THEN 返回友好错误(不暴露内部地址/堆栈),assistant 消息 status=FAILED
- AND 不向上抛裸 500

#### Scenario: 图表配置建议推断
- GIVEN 查询结果返回
- WHEN 生成响应
- THEN 按列特征给出 chart 建议:首列日期/时间类型→line;低基数分类(≤8 类)
  +数值→pie;类别列+数值列→bar;明细或无数值列→none

### Requirement: 会话列表与消息历史端点

系统 SHALL 提供会话列表端点:JWT 认证 + 权限点 `chat:execute` 校验 +
租户白名单校验(X-Tenant-Id,缺省取用户默认租户,语义与问答端点一致);
返回当前用户在当前租户下的会话(id/标题/更新时间),按更新时间倒序,
SHOULD 支持条数上限参数(缺省 50)。列表 SHALL 只含本人本租户会话,
不含他人或他租户会话(过滤而非报错)。

系统 SHALL 提供会话消息端点(路径含 session_id):同一鉴权与租户约束;
返回该会话全部消息按创建时间升序,user 消息含问句,assistant 消息
SHALL 携带结构化应答要素(状态/摘要/SQL/结果列/行/洞察/统计/图表建议),
要素缺失时以缺省形态呈现(如无 SQL 则空);assistant 消息 SHALL 纯增携带
rating(null=未反馈)、corrected_sql(null=无纠错)与 feedback_comment
(null=无留言)反馈状态字段供回显(feedback spec「messageId 打通」;
comment 为点踩弹窗预填源,R1-S3),user 消息不携带。归属校验:会话不存在、
非本人、非当前租户三种情形 SHALL 统一返回 404,不区分语义
(不泄露会话存在性)。

系统 SHALL 提供会话删除端点(路径含 session_id):同一鉴权与租户约束;
删除会话并级联删除其全部消息;归属校验语义同上(404)。删除幂等语义:
重复删除同一会话返回 404(第二次已不存在)。

#### Scenario: 列表只含本人本租户会话
- GIVEN 用户 A 在租户 T1 有会话 S1,用户 B 在 T1 有 S2,A 在 T2 有 S3
- WHEN 用户 A 以租户 T1 请求会话列表
- THEN 返回含 S1 不含 S2/S3

#### Scenario: 列表按更新时间倒序
- GIVEN 用户有会话 S1(3 天前更新)与 S2(刚刚更新)
- WHEN 请求会话列表
- THEN S2 排在 S1 前

#### Scenario: 越权取他人会话消息统一 404
- GIVEN 会话 S 属于用户 B
- WHEN 用户 A 请求 S 的消息
- THEN 返回 404,响应不区分「不存在」与「无权」

#### Scenario: 跨租户取会话 404
- GIVEN 会话 S 属于用户 A 但租户为 T2
- WHEN 用户 A 以租户 T1 请求 S 的消息
- THEN 返回 404

#### Scenario: 消息回显保序且要素完整
- GIVEN 会话含 3 轮问答(其中 1 轮被拦截)
- WHEN 请求该会话消息
- THEN 按创建时间升序返回;被拦截轮 assistant 消息 status=REJECTED
  且携带拦截文案,成功轮携带摘要/SQL/结果/图表建议
- AND 每条 assistant 消息含 rating/corrected_sql/feedback_comment 反馈状态字段

#### Scenario: 删除会话级联清消息
- GIVEN 会话 S 含多轮消息
- WHEN 持有者删除 S
- THEN S 从列表消失,再请求 S 的消息返回 404

#### Scenario: 无权限访问列表 403
- GIVEN 已登录用户角色不含 chat:execute
- WHEN 请求会话列表/消息/删除任一端点
- THEN 返回 403

#### Scenario: 未认证访问 401
- GIVEN 请求无有效 JWT
- WHEN 请求任一新端点
- THEN 返回 401
