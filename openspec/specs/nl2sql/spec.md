# nl2sql 能力域

## Purpose

NL2SQL 核心能力:将自然语言问句转换为 SQL 并执行,返回结构化结果与可视化图表。
行为覆盖:端到端查询、SQL 强制只读、Agent 自愈重试、LLM 可配置、结果可视化、内部接口鉴权。

## Requirements

### Requirement: NL2SQL 查询端到端

系统 SHALL 接收已认证用户的自然语言问句,经 Agent 处理后返回查询结果。

受影响权限层级: 接口权限(`chat:execute`) + SQL 操作权限(SELECT_ONLY)

#### Scenario: 正常的只读统计查询
- GIVEN 已登录用户拥有查询权限,租户为 sakila
- WHEN 提交问句「上个月 rentals 数量最多的 top5 客户」
- THEN 返回包含 5 行的表格结果(customer_id, first_name, last_name, rental_count)
- AND 返回对应的柱状图配置数据
- AND 端到端延迟 P95 < 15 秒
- AND 审计表记录一条日志(含 user_id, tenant_id, question, sql_text, verdict=PASS)

#### Scenario: 指代不清的多轮追问
- GIVEN 用户上一轮问了「rentals 最多的客户」
- WHEN 接着问「那他总共花了多少钱」
- THEN Agent 能理解「他」指代上一轮的 top1 客户,返回该客户总消费金额

(决策: 保留最近 5 轮上下文,平衡 token 消耗与连贯性)

### Requirement: SQL 操作权限强制只读

系统 SHALL 拒绝任何非 SELECT 语句,无论 LLM 生成什么。校验 SHALL 基于 SQL AST,非正则匹配。

#### Scenario: LLM 生成了 DELETE 语句
- GIVEN Agent 生成了「DELETE FROM customer WHERE ...」
- WHEN SQL 经控制面受控执行接口校验
- THEN AST 识别为写语句,校验失败(原因: FORBIDDEN_DML)
- AND Agent 进入 selfheal,权限类错误不重试,直接终止并提示「该查询涉及写操作,已被拒绝」
- AND 审计记录 verdict=BLOCKED

#### Scenario: 试图用注释绕过校验
- GIVEN LLM 生成「SELECT 1; /* */ DROP TABLE customer」
- WHEN 校验
- THEN 检测到多语句或 DDL 节点,拒绝(原因: MULTI_STATEMENT / FORBIDDEN_DDL)
- AND 记录审计

#### Scenario: 子查询中藏 DDL
- GIVEN LLM 生成非法嵌套 DDL 的 SQL
- WHEN 校验
- THEN AST 解析失败,拒绝(原因: PARSE_ERROR)

### Requirement: Agent 自愈重试

当 SQL 生成错误或执行失败时,Agent SHALL 基于错误信息自我修正。

#### Scenario: SQL 执行时列名错误
- GIVEN Agent 生成的 SQL 执行报错「column xxx does not exist」
- WHEN 重试次数 < 上限(默认 2)
- THEN Agent 把错误信息喂回 LLM 重新生成,新 SQL 使用正确列名,最终返回正确结果
- AND 审计记录重试过程(trace_id 贯穿)

#### Scenario: 重试耗尽仍失败
- GIVEN Agent 重试达上限仍无法生成可执行 SQL
- THEN 终止并返回友好错误提示(不暴露原始堆栈给前端)
- AND 审计记录最终失败状态

### Requirement: LLM 模型可配置切换

系统 SHALL 支持通过配置切换 LLM 厂商,不改代码。

#### Scenario: 从通义千问切换到 DeepSeek
- GIVEN 默认配置为通义兼容端点
- WHEN 修改 LLM_BASE_URL/LLM_MODEL 环境变量并重启 Agent 面
- THEN 功能正常工作,所有 LLM 调用走新端点

### Requirement: 结果可视化

系统 SHALL 为查询结果生成合适的图表配置。

#### Scenario: 聚合查询自动配柱状图
- GIVEN 查询结果是分组聚合(如 top N 客户)
- THEN 返回结果中包含图表配置(type=bar, x=类别列, y=数值列),前端渲染柱状图

#### Scenario: 明细查询返回纯表格
- GIVEN 查询结果是明细行
- THEN 不强加图表,仅返回表格数据

### Requirement: 控制面回调 Agent 面

控制面 SHALL 经内部令牌调用 Agent 面问答端点,请求报文 SHALL 携带
trace_id、tenant_key、question;SHOULD 携带 user_id、history、
datasource_key(缺省 default)。trace_id SHALL 与执行审计(sys_sql_audit)
同源贯穿,同一次问答的多次执行回调(含 selfheal 重试)共用同一 trace_id。
Agent 面响应 SHALL 携带 success、verdict(PASS/BLOCKED/ERROR 或空)、
summary、insights、stats、columns、rows、sql、retry_count,失败时携带
error 摘要。控制面 SHALL 为回调设置超时上限;Agent 面不可达/超时 SHALL
映射为友好错误提示,不得向上抛裸 500、不得泄露内部地址与堆栈。

#### Scenario: 正常回调一次走通
- GIVEN 控制面收到合法问答请求
- WHEN 回调 Agent 面
- THEN 报文含 trace_id/tenant_key/user_id/question/history
- AND Agent 面响应 success=true 时含 summary/columns/rows/sql/insights/stats

#### Scenario: selfheal 重试共用 trace_id
- GIVEN 首次执行 verdict=ERROR 触发 selfheal 重试
- WHEN Agent 面多次回调执行路由
- THEN 各次执行回调的 trace_id 相同,审计可按 trace_id 串起重试链

#### Scenario: Agent 面不可达
- GIVEN Agent 面进程不可达或响应超时
- WHEN 控制面回调
- THEN 返回友好错误提示,不暴露内部地址/堆栈,不裸 500

#### Scenario: 三态 verdict 映射
- GIVEN Agent 面响应返回
- WHEN 控制面处理响应
- THEN verdict=PASS 映射为成功结果;verdict=BLOCKED 映射为拒绝提示
  (不重试语义已由 Agent 面执行);verdict=ERROR 映射为失败提示

### Requirement: 内部接口鉴权(api↔agent)

Agent 面回调控制面的内部接口 MUST 鉴权,防止外部或同网段横向调用。

#### Scenario: 带正确内部令牌调用
- GIVEN 两进程共享 INTERNAL_TOKEN 配置
- WHEN Agent 调用 /internal/* 携带 X-Internal-Token: <正确值>
- THEN 校验通过,处理请求

#### Scenario: 缺失或错误令牌调用
- WHEN 调用 /internal/* 无令牌头或值不匹配
- THEN 返回 403,拒绝执行任何 SQL

#### Scenario: 网络隔离兜底
- GIVEN 部署上 Agent 端口仅暴露给内部网络,不映射到宿主机
- THEN 外部网络无法直接访问内部接口(网络层第一道防线)
- AND 即使绕过网络,仍需内部令牌(应用层第二道)
