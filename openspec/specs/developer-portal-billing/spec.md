## Purpose

开发者自助门户(api-key 管理/用量看板/账单)+ 三维配额 fail-closed
(token/调用/成本)+ 套餐与周期账单;用量以服务端采集为唯一事实源
(不经客户端,防绕过),api-key 体系复用既有(025,语义不变)。

## Requirements

### Requirement: 用量配额判定(fail-closed)

系统 SHALL 支持租户级三维配额策略(dimension ∈ TOKENS/CALLS/COST,
period=MONTHLY,limit_value,warn_pct 默认 80),由超管配置(system:
billing:manage)。OpenAI 兼容网关(POST /v1/chat/completions)执行前
MUST 判定:当月 token 用量(sys_llm_usage 聚合)+ 当月调用数(api
渠道审计计数)+ 成本估算(套餐单价×token),任一维度超限 MUST
拒绝(429 QUOTA_EXCEEDED)并落审计,不执行请求;达到 warn_pct
MUST 以响应头 X-Quota-Warning 标记预警(不拒绝)。

#### Scenario: 超额 fail-closed 拒绝

- GIVEN 租户当月 token 用量已达上限
- WHEN 新请求到达网关
- THEN 429 QUOTA_EXCEEDED + 审计,不执行

#### Scenario: 未超放行并预警

- GIVEN 用量达 80% 未达 100%
- WHEN 新请求到达
- THEN 放行执行,响应头 X-Quota-Warning 标记

### Requirement: 配额口径与服务端采集

**口径注记**:CALLS 维度=工具执行计数(api 渠道审计行数,纯问答型 Bot 无
工具调用恒 0,既定语义);请求形态校验(空问题 400)先于配额判定(429)
为既定顺序;**COST 维成本估算以绑定套餐单价为前提**——未绑套餐租户单价
按 0,COST 策略零信号(配置面 MUST 给出提示,035 R1-A4)。用量计量 MUST
以服务端 sys_llm_usage 采集为唯一事实源(客户端无上报通道)。

#### Scenario: 未绑套餐时 COST 维零信号

- GIVEN 租户未绑套餐且配置了 COST 维策略
- WHEN 网关判定成本
- THEN 单价按 0 计,COST 维不触发拒绝,配置面给出提示

#### Scenario: 用量防绕过

- GIVEN 一次模型调用
- WHEN 计量
- THEN 以服务端 sys_llm_usage 采集为准(客户端无上报通道)

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
审计——配额放行的代价是不可归因地消耗 token。配额已用量 SHALL 由既有
`sys_llm_usage` 按窗口 count 派生(失败尝试计入,038「失败调用也采集」
既有口径的延续),SHALL NOT 为此新增用量表。

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

### Requirement: 问答速率窗口边界平滑

登录问答端点与公开嵌入问答端点的分钟级速率判定 SHALL 考虑紧邻窗口中的既有请求。某一身份在窗口切换前已用尽额度时，窗口切换后 SHALL NOT 立即获得完整的新额度；可用额度 SHALL 随此前请求离开近似 60 秒判定范围而恢复。该速率判定允许有界近似，不作为精确审计或调用计量来源。

本 Requirement 不改变既有身份维度、速率阈值配置、超限响应、进入 LLM 前拒绝语义或限流存储故障时的放行语义。工具调用与工具域限额不受本 Requirement 影响。

#### Scenario: 登录问答在窗口边界不重置完整额度

- GIVEN 某 `(租户, 用户)` 在速率窗口切换前刚用尽分钟额度
- WHEN 该用户在窗口切换后立即再次发起问答
- THEN 请求仍按近期流量判定为超限,返回 `CHAT_RATE_LIMITED`,且不进入 Agent/LLM 调用链

#### Scenario: 公开嵌入在窗口边界不重置完整额度

- GIVEN 某 `(助手标识, 可信客户端 IP)` 在速率窗口切换前刚用尽分钟额度
- WHEN 该身份在窗口切换后立即再次发起问答
- THEN 请求仍按近期流量判定为超限,返回 429,且不进入 LLM 调用链

#### Scenario: 近期请求老化后额度恢复

- GIVEN 某身份因近期请求达到分钟额度而被拒绝
- WHEN 近期请求逐步离开速率判定范围后再次发起请求
- THEN 系统按恢复后的可用额度放行,无需管理员清理限流状态

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
