# developer-portal-billing spec delta(062-chat-cost-control)

## ADDED Requirements

### Requirement: 用户问答速率控制

用户问答端点(`POST /chat/query` 与 `POST /chat/query/stream`)SHALL 受
(租户, 用户) 维度的分钟级速率限制,限额 SHALL 可配置。达到限额的请求
SHALL 返回 429 与稳定错误码,且 SHALL NOT 进入 LLM 调用链(不消耗 token)。
限流存储不可用时系统 SHALL 降级放行——限流是配额保护层而非安全闸,
安全闸由权限点、租户隔离与 SQL 防火墙各自独立承担。

#### Scenario: 超速请求不消耗 token

- GIVEN 某 (租户, 用户) 已达分钟级限额
- WHEN 该用户再次发起问答
- THEN 返回 429 与 `CHAT_RATE_LIMITED`,且 agent 面无任何 LLM 调用发生

#### Scenario: 速率维度为租户加用户

- GIVEN 同一租户下两个不同用户,其中甲已达限额
- WHEN 乙发起问答
- THEN 乙不受甲限额影响(限流键含用户维度)

#### Scenario: 限流存储不可用降级放行

- GIVEN 限流存储不可达
- WHEN 用户发起问答
- THEN 请求被放行,并记录 error 级日志,业务链路不中断

### Requirement: 用户问答配额

用户问答 SHALL 受租户(可叠加用户)维度的日/月配额约束,维度至少含调用
次数。超额 SHALL 返回 429、携带 `X-Quota-Warning` 预警响应头,语义与网关
(OpenAI 兼容)切面一致。配额数据不可读时 SHALL 按 fail-closed 拒绝并落
审计——配额放行的代价是不可归因地消耗 token。

#### Scenario: 超额返回 429 并预警

- GIVEN 某租户当月调用次数配额已用尽
- WHEN 该租户下用户发起问答
- THEN 返回 429 与 `CHAT_QUOTA_EXCEEDED`,响应头携带 `X-Quota-Warning`

#### Scenario: 配额不可读时拒绝而非放行

- GIVEN 配额计数值不可读
- WHEN 用户发起问答
- THEN 请求被拒绝,不进入 LLM 调用,且拒绝事件落入审计

#### Scenario: 配额维度含调用次数

- GIVEN 某租户配额仅配置了调用次数维度
- WHEN 该租户连续发起问答直至超额
- THEN 超额判定以该维度为准,token/成本维度不参与判定(由对账旁路可见)

### Requirement: 公开嵌入端点限流

免登录的公开嵌入问答端点(`POST /bot/{bot_key}/chat`)SHALL 受 (助手标识,
客户端 IP) 维度的速率限制。客户端 IP SHALL 取可信代理链解析值,请求自带
的来源头 SHALL NOT 参与限流键计算。达到限额 SHALL 返回 429,且 SHALL NOT
进入 LLM 调用链。

#### Scenario: 匿名高频调用被限

- GIVEN 同一客户端 IP 对同一公开助手的问答已达限额
- WHEN 该 IP 再次调用
- THEN 返回 429,不进入 LLM 调用

#### Scenario: 伪造来源头不改变限流键

- GIVEN 请求自带头伪造了来源 IP
- WHEN 计算限流键
- THEN 使用可信代理链解析出的客户端 IP,自造头被忽略

#### Scenario: 单助手维度隔离

- GIVEN 助手 A 的某客户端 IP 已达限额
- WHEN 该 IP 调用助手 B
- THEN 不受助手 A 限额影响(限流键含助手标识)

### Requirement: 问答结果行数上限可配置

问答面 SQL 执行的结果行数上限 SHALL 为可配置项,SHALL NOT 为代码内硬编码
值。缺省值 SHALL 与配置化之前的行为一致,保证零回归。

#### Scenario: 缺省值零行为变化

- GIVEN 未显式配置结果行数上限
- WHEN 执行一次返回行数超过上限的问答 SQL
- THEN 截断行数与配置化之前一致

#### Scenario: 上限可下调生效

- GIVEN 将结果行数上限配置为较低值
- WHEN 执行问答 SQL
- THEN 按新上限截断,且截断事件可观测(日志或 trace)
