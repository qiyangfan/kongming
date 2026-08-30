# chat spec delta(047-qa-retrieval-governance)

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
(persist=true 时),不裸 500。**047 修订(文案与错误细节数据面):**

REJECTED 状态 SHALL 以 `blocked_friendly` 按 reason 分态产出用户文案;
RESERVED_TABLE 分态文案 SHALL 中性、不责备用户、给出可行动指引
(「该问题涉及的数据不允许查询,请调整问法或咨询管理员」)——保留表
拦截的常见成因是模型越界而非用户过错,文案不得使用「访问了不允许的
表,已被拒绝」类归责表述。其余分态(GUARDRAIL/DATA_SCOPE/非只读)
语义准确维持原文。

FAILED 状态 `error` SHALL 为用户语言友好文案(「查询执行失败,请调整
问法后重试」),原始技术错误 SHALL 迁移至响应新增字段
`error_detail: str | None`(纯增,缺省 null;老调用方零感知);result_json
落库同步携带 error_detail,流式 result 帧与同步响应同构携带。前端 SHALL
以友好文案为主呈现,`error_detail` 非空时以折叠「技术详情」披露原始
错误(honest + 可排障 + 不吓人);error_detail 为 null(含存量消息)
SHALL 不渲染折叠块。NO_DATA 中性形态(044,见「消息状态第四态」
requirement)与 REJECTED 其余分态不动。

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

#### Scenario: 保留表拦截文案中性(047)
- GIVEN 链路生成 SQL 引用保留表被拒(reason 含 RESERVED_TABLE)
- WHEN 响应组装
- THEN error 为「该问题涉及的数据不允许查询,请调整问法或咨询管理员」
- AND 不含「访问了不允许的表」归责表述

#### Scenario: FAILED 友好文案+技术细节分离(047)
- GIVEN 链路执行失败,原始错误为 PG 报错(如 missing FROM-clause ...)
- WHEN 响应组装
- THEN error=「查询执行失败,请调整问法后重试」
- AND error_detail=原始 PG 报错全文
- AND 流式与同步两通道字段同构

#### Scenario: 存量消息不渲染折叠块(047)
- GIVEN 落库消息 result_json 无 error_detail 字段
- WHEN 前端回显 FAILED 消息
- THEN 呈现友好 error 文案,不渲染「技术详情」折叠块
