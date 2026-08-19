# chat 能力域(delta)

## ADDED Requirements

### Requirement: 会话列表与消息历史端点

系统 SHALL 提供会话列表端点:JWT 认证 + 权限点 `chat:execute` 校验 +
租户白名单校验(X-Tenant-Id,缺省取用户默认租户,语义与问答端点一致);
返回当前用户在当前租户下的会话(id/标题/更新时间),按更新时间倒序,
SHOULD 支持条数上限参数(缺省 50)。列表 SHALL 只含本人本租户会话,
不含他人或他租户会话(过滤而非报错)。

系统 SHALL 提供会话消息端点(路径含 session_id):同一鉴权与租户约束;
返回该会话全部消息按创建时间升序,user 消息含问句,assistant 消息
SHALL 携带结构化应答要素(状态/摘要/SQL/结果列/行/洞察/统计/图表建议),
要素缺失时以缺省形态呈现(如无 SQL 则空)。归属校验:会话不存在、
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
