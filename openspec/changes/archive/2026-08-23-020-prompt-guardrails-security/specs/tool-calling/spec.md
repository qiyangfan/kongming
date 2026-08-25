## MODIFIED Requirements

### Requirement: 工具纵深防御(六层)

每个工具的执行 MUST 经过六层独立防御,任一层被绕过仍有兜底:

1. **内部令牌与身份复校**:X-Internal-Token 校验 + 用户-租户绑定复校(与既有执行路由同构,fail-closed);工具执行请求 MUST 携带 user_id(无系统调用豁免——工具权限点校验需要真实主体)。
2. **工具权限点**:每个工具绑定权限点(如 tool:sql:execute、tool:announcement:create),调用方角色集不含该权限点即拒,最小授权。
3. **租户隔离**:跨租户执行 fail-closed 拒绝(沿用租户白名单复校)。
4. **工具意图审核 + 参数 JSON Schema 校验**:意图审核(020 挂接:同 trace 写工具调用频次阈值,fail-closed)SHALL 先于参数校验;控制面按注册表 schema 校验 LLM 产出的 args,越界/畸形参数拒绝;该校验 SHALL 先于任何工具语义执行(写工具确认挂起点之后恢复执行时同一校验仍生效)。
5. **审计 + 限流**:每次工具执行(含拒绝/失败/限流/待确认)落 sys_tool_audit;按租户+用户维度的固定窗口限流,超额拒绝并落审计。
6. **写工具幂等 + 人工确认**:写操作工具 MUST 支持幂等键防重复执行;标记 require_confirm 的高风险写工具默认人工确认(见「写工具人工确认」requirement)。

#### Scenario: 工具权限点拒绝无权调用(接口层)

- GIVEN 一个角色未被授予某工具所需权限点(如未授 tool:announcement:create)
- WHEN 该角色触发的 tool_call 经控制面执行
- THEN 控制面拒绝执行并落审计(记录拒绝原因),不触发工具副作用

#### Scenario: 跨租户执行被拒(租户层)

- GIVEN 用户对目标 tenant_key 不在白名单
- WHEN tool_call 携带该 tenant_key 回调控制面
- THEN 控制面 fail-closed 拒绝,不执行工具

#### Scenario: 意图审核先于参数校验(020)

- GIVEN 同一 trace 内写工具调用已达意图审核阈值,且本次调用参数畸形
- WHEN 该 tool_call 到达控制面
- THEN 意图审核先拒绝(GUARDRAIL_BLOCKED),参数校验不再执行;sys_guardrail_audit 与 sys_tool_audit 双落

#### Scenario: 参数 JSON Schema 越界拒绝

- GIVEN SQL 执行工具的参数 schema 规定 sql 字段必须是单条 SELECT 语句
- WHEN LLM 产出 args.sql 为一条 DROP 语句
- THEN 控制面参数校验层拒绝并落审计;该 DROP 不进入 SQL 执行通路

#### Scenario: 工具执行限流

- GIVEN 某用户的 tool_call 频率超过限流配额
- WHEN 超额请求到达控制面
- THEN 超额请求被拒绝(限流语义),审计记录该次限流

#### Scenario: 写工具幂等防重复执行

- GIVEN 写工具(create_system_announcement)以相同幂等键被调用两次
- WHEN 第二次调用到达控制面
- THEN 控制面识别幂等键命中,不重复执行写动作,返回首次执行的结果
