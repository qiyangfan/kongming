## Purpose

让 Agent 从「固定 NL2SQL 管线」升级为「可自主选择并调用工具的通用 AI Agent」:LLM 在运行时决定调哪个工具、接收结果、决定下一步(ReAct 循环);工具执行收口于控制面通用回调,经与 NL2SQL 同范式的六层纵深防御,AI 进程始终不持任何凭证(ADR-002)。

## ADDED Requirements

### Requirement: 工具定义与注册表

系统 SHALL 在控制面启动时自动注册一组内置工具,供 LLM 作为可调用函数消费。每个工具的元数据 MUST 包含:唯一名称、自然语言描述(供 LLM 选择)、参数 JSON Schema(供校验与 LLM 函数签名)、所需权限点、是否为写操作(write flag)、幂等策略、是否需人工确认(require_confirm)。工具注册表 SHALL 以控制面为唯一真理源:Agent 面 SHALL NOT 维护独立的工具 schema 清单;控制面调用 Agent 面 ReAct 端点时 SHALL 按调用方授权集注入工具 schema(含写标记与确认标记),未授权工具不出现在注入集中。

#### Scenario: 启动后内置工具就绪

- GIVEN 控制面与 Agent 面均已启动
- WHEN 任意用户发起 ReAct 模式问答
- THEN 内置工具集合(至少含 validate_sql / execute_sql / rag_retrieve / create_system_announcement)经注入对 LLM 可见,LLM 可在其参数 schema 约束内选择调用

#### Scenario: 工具元数据可作为 LLM 函数签名消费

- GIVEN 一个已注册的工具
- WHEN Agent 面将注入的工具集合以 OpenAI 兼容的 function/tool schema 形式传给 LLM
- THEN LLM 产出的 tool_calls 中的工具名能在注入集中命中,参数结构符合该工具的 JSON Schema

#### Scenario: 未注册工具被拒绝

- GIVEN LLM 产出一个工具名不在注册表中的 tool_call
- WHEN Agent 面将该 tool_call 回调控制面执行
- THEN 控制面拒绝执行,返回明确的错误码,并落审计记录该次越权尝试

#### Scenario: Agent 面不维护独立工具 schema

- GIVEN 控制面新增或变更某工具的参数 schema
- WHEN 不改动 Agent 面任何代码或配置
- THEN 下一次 ReAct 调用即按新 schema 注入,Agent 面无漂移副本

### Requirement: ReAct 工具调用循环(Agent 面)

Agent 面 SHALL 提供 ReAct 内核端点(内部令牌认证,ADR-002):LLM 根据用户意图自主决定调用哪个工具、接收工具结果、决定继续调工具还是产出最终答案。系统 MUST 保留既有 NL2SQL 链路 `/query` 与 `/query/stream`(8 节点图)的对外契约与行为不变,作为「预设工具配方」向后兼容。ReAct 端点终态响应 SHALL 与既有问答端点逐字段同构(success/verdict/summary/insights/stats/columns/rows/sql/retry_count/error,单一定义防漂移)。循环 SHALL 设轮次上限(可配置,缺省不超过 10),达到上限 SHALL 强制终止并返回结构化错误(携带已达上限提示),禁止无限循环。同一轮问答内的多次工具执行回调 SHALL 共用同一 trace_id。

#### Scenario: 数据查询场景 LLM 自主编排工具

- GIVEN 用户提问「查询租金最高的 5 部影片」,且 SQL 校验/执行/RAG 检索工具均已注入
- WHEN Agent 面进入 ReAct 循环
- THEN LLM 自主选择工具组合(如 rag_retrieve → 生成 SQL → validate_sql → execute_sql),最终返回的响应字段与既有 NL2SQL 链路逐字段同构

#### Scenario: 老链路向后兼容零回归

- GIVEN 评估集(016)中任一既有问题
- WHEN 经既有 `/query` 链路处理
- THEN 响应与行为与 019 之前完全一致,评估通过率不低于既有基线

#### Scenario: ReAct 链路评估等价

- GIVEN 评估集中任一数据查询问题
- WHEN 经 ReAct 内核处理
- THEN 响应字段语义与老链路等价,评估通过率不低于既有基线口径

#### Scenario: 循环终止条件

- GIVEN LLM 已收集足够信息
- WHEN LLM 在某一轮不再产出 tool_calls 而是产出最终文本答案
- THEN ReAct 循环终止,向控制面返回最终响应

#### Scenario: 循环深度上限保护

- GIVEN LLM 连续产出 tool_calls 未收敛
- WHEN 工具调用轮次达到配置上限
- THEN 循环强制终止并返回结构化错误(携带已达上限提示),无进一步工具副作用

### Requirement: 通用工具执行回调

系统 SHALL 提供通用内部端点 `POST /internal/tool/execute` 供 Agent 面回调控制面执行工具,MUST 复用既有内部端点的 X-Internal-Token 鉴权口径(ADR-002)。Agent 面 SHALL 只传递工具名、参数(标识符与文本)、调用方上下文(user_id/tenant_key/datasource_key)、trace_id 与幂等键(写工具),MUST NOT 持有或传递任何数据库凭证或外部系统凭证。响应 SHALL 为结构化三态:executed(含结果)/ rejected(防御层拒绝,含原因码)/ confirm_required(写工具待人工确认,含确认标识);传输层故障由 Agent 面收敛为工具执行错误观察喂回 LLM,不裸抛。

#### Scenario: 合法 token 工具执行

- GIVEN Agent 面携带正确的 X-Internal-Token 与一个合法 tool_call
- WHEN 回调控制面 /internal/tool/execute
- THEN 控制面经纵深防御后执行工具并返回结构化结果

#### Scenario: 非法或缺失 token 被拒绝

- GIVEN Agent 面携带错误或缺失的 X-Internal-Token
- WHEN 回调控制面 /internal/tool/execute
- THEN 控制面拒绝执行(403 口径同既有内部端点),不触发任何工具副作用

#### Scenario: AI 不持凭证的边界

- GIVEN 任一工具执行
- WHEN Agent 面将 tool_call 回调控制面
- THEN 所有凭证(数据库连接、外部 API key)仅由控制面持有与使用;Agent 面请求体中不含任何凭证字段

### Requirement: 工具纵深防御(六层)

每个工具的执行 MUST 经过六层独立防御,任一层被绕过仍有兜底:

1. **内部令牌与身份复校**:X-Internal-Token 校验 + 用户-租户绑定复校(与既有执行路由同构,fail-closed);工具执行请求 MUST 携带 user_id(无系统调用豁免——工具权限点校验需要真实主体)。
2. **工具权限点**:每个工具绑定权限点(如 tool:sql:execute、tool:announcement:create),调用方角色集不含该权限点即拒,最小授权。
3. **租户隔离**:跨租户执行 fail-closed 拒绝(沿用租户白名单复校)。
4. **参数 JSON Schema 校验**:控制面按注册表 schema 校验 LLM 产出的 args,越界/畸形参数拒绝;该校验 SHALL 先于任何工具语义执行(写工具确认挂起点之后恢复执行时同一校验仍生效)。
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

### Requirement: 内置工具集

系统 SHALL 内置以下工具,全部纳入注册表与纵深防御:

- **SQL 校验工具**(validate_sql,只读):调用既有 SQL 防火墙做 AST 预检。
- **SQL 执行工具**(execute_sql,只读):走既有执行路由全链路(防火墙 + data_scope 改写 + 只读池),SQL 通道恒只读不开写口子;携带 datasource_key 时沿用动态数据源绑定校验。
- **RAG 检索工具**(rag_retrieve,只读):走既有三路召回;召回零命中(schema 与历史双空)时 SHALL 回退实时内省(与老链路 retrieve 节点「RAG 优先、内省兜底」同语义),内省亦失败时返回空提示不误导。
- **系统公告创建工具**(create_system_announcement,写工具示例):落公告表 + 审计,require_confirm 默认人工确认,幂等键防重复发布。

#### Scenario: SQL 工具只读锁定不被写通道污染

- GIVEN execute_sql 工具已注册
- WHEN LLM 产出一个含 DDL/DML 的 SQL 经该工具
- THEN 该 SQL 仍被 SQL 防火墙拦截;写工具通道(公告)独立于 SQL 通路,不在 SQL 通路开口子

#### Scenario: 系统公告写工具端到端

- GIVEN 一个被授予 tool:announcement:create 的用户
- WHEN 用户说「发布一条系统公告:明日维护」,LLM 选 create_system_announcement 工具
- THEN 系统向前端下发确认请求(携带工具名、参数摘要、预期副作用);用户确认后,公告落库(含 title/content/publisher/创建时间),sys_tool_audit 落执行记录,幂等键防止重复发布

#### Scenario: SQL 执行工具保留数据源隔离

- GIVEN execute_sql 工具携带 datasource_key
- WHEN 执行动态数据源查询
- THEN 沿用动态数据源绑定校验 + 行级 data_scope 改写(与既有链路一致)

### Requirement: 写工具人工确认

标记 require_confirm 的写工具被调用时,系统 SHALL NOT 立即执行:控制面落待确认记录(status=PENDING_CONFIRM,含工具名/参数摘要/幂等键/过期时间),响应 confirm_required;Agent 面 SHALL 把「待确认」作为工具观察喂回 LLM,LLM 据此产出说明性最终答案(正常终态,不算失败)。问答响应 SHALL 纯增携带待确认调用信息(确认标识/工具名/参数摘要),供前端渲染确认卡片。用户确认 SHALL 经专用端点提交(归属校验:仅本会话发起人可操作),确认后控制面按幂等键执行同一工具并落执行回执消息;用户拒绝或确认过期 SHALL 落审计且不执行。同一幂等键的重复确认 SHALL NOT 产生重复写。

#### Scenario: 写工具挂起待确认

- GIVEN LLM 产出 require_confirm 写工具的 tool_call
- WHEN 控制面收到执行回调
- THEN 不执行写动作,落 PENDING_CONFIRM 审计,响应 confirm_required 携带确认标识
- AND 问答正常终态返回,响应含待确认调用信息

#### Scenario: 用户确认后执行

- GIVEN 存在本会话的待确认调用且未过期
- WHEN 会话发起人提交确认
- THEN 控制面执行该工具(六层防御全量再过一遍),落 EXECUTED 审计与执行回执消息

#### Scenario: 他人确认被拒

- GIVEN 待确认调用属于用户 A 的会话
- WHEN 用户 B 提交确认
- THEN 拒绝(归属校验),不执行,落审计

#### Scenario: 重复确认幂等

- GIVEN 用户已对某待确认调用确认并执行成功
- WHEN 同一确认标识再次提交确认
- THEN 返回首次执行结果,不产生重复写

### Requirement: 工具调用流式契约

ReAct 流式端点的 SSE 流 SHALL 新增 tool_call 事件(字段:tool_name、args 摘要、status ∈ {running, done, rejected}、result 摘要),与既有 stage/sql/summary/result/error 事件并列。事件扩展 MUST 向后兼容——老前端忽略未知事件类型。一次工具调用 SHALL 至少发射 running 与终态(done/rejected)各一帧;result 摘要有截断上限。

#### Scenario: tool_call 事件下发

- GIVEN ReAct 循环进入一次工具调用
- WHEN 工具开始执行、执行完成、或被防御层拒绝
- THEN SSE 流依次下发对应 status 的 tool_call 事件

#### Scenario: 老前端向后兼容

- GIVEN 一个未升级的前端客户端
- WHEN 收到未知事件类型 tool_call
- THEN 客户端忽略该事件,不崩溃,不影响对 result/error 终态事件的处理

### Requirement: 工具管理与审计查询

系统 SHALL 提供超管工具管理面:已注册工具清单查询(名称/描述/权限点/写属性/启用状态,只读)与工具执行审计分页查询(按工具/用户/租户/状态/时间筛选),管理端点要求权限点 system:tool:manage(仅 SUPER_ADMIN 种子覆盖)。审计查询 SHALL 沿用行级可见性口径:非 data_scope=ALL 的角色只能看自己的工具执行记录(与 014/015 同构)。

#### Scenario: 工具清单查询

- GIVEN SUPER_ADMIN 已登录
- WHEN 访问工具清单端点
- THEN 展示全部已注册工具的元数据(名称/描述/权限点/写属性/启用状态),只读

#### Scenario: 工具执行审计查询

- GIVEN SUPER_ADMIN 已登录
- WHEN 按工具名/状态/时间范围筛选审计
- THEN 返回 sys_tool_audit 分页结果

#### Scenario: 非超管无工具管理权限

- GIVEN 一个角色未被授予 system:tool:manage
- WHEN 访问工具管理端点
- THEN 返回 403(接口权限点拦截),落 perm_denied 审计

#### Scenario: 行级可见性

- GIVEN 非 ALL 数据范围的角色(被授予审计查询权的场景)
- WHEN 查询工具执行审计
- THEN 只返回本人记录,他人记录不可见
