# Spec Delta(chat)

## MODIFIED Requirements

### Requirement: 问答查询端点鉴权与请求字段

系统 SHALL 提供用户面问答端点:JWT 认证 + 权限点 `chat:execute` 校验 +
X-Tenant-Id 租户白名单校验(缺省取用户默认租户,未绑定拒绝,SUPER_ADMIN
不豁免)。请求 SHALL 携带 question;SHOULD 携带 session_id(缺省自动新建会话,
标题规则见「会话持久化」requirement 的两阶段生成)、persist(缺省 true)、
history(显式多轮上下文)、datasource_key(缺省 default)。系统 SHALL 为每次问答
生成 trace_id 并贯穿 Agent 面与执行审计。请求 SHALL 纯增可选的语音轮关联
字段(voice_turn_token):仅语音通话发起的一轮携带,须属于当前用户的在途
通话且未被使用;打字提问不携带,行为逐字不变。

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

#### Scenario: 语音轮携带关联字段
- GIVEN 通话侧已为用户 A 的一通在途通话签发了关联字段
- WHEN 用户 A 以该字段提交流式问答
- THEN 该轮与通话侧绑定,通话侧可订阅其终态并代为取消;其余行为与打字提问一致

#### Scenario: 关联字段非法
- GIVEN 关联字段不存在、已被使用,或不属于当前用户
- WHEN 提交问题
- THEN 返回 400,不落消息、不回调 Agent 面

#### Scenario: 打字提问零回归
- GIVEN 请求不携带关联字段
- WHEN 提交问题
- THEN 请求校验、编排与响应与引入前逐字一致
