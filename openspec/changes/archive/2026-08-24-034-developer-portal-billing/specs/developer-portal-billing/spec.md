## Purpose

开发者自助门户(api-key 管理/用量看板/账单)+ 三维配额 fail-closed
(token/调用/成本)+ 套餐与周期账单;用量以服务端采集为唯一事实源
(不经客户端,防绕过),api-key 体系复用既有(025,语义不变)。

## ADDED Requirements

### Requirement: 用量配额(fail-closed)

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

#### Scenario: 用量防绕过

- GIVEN 一次模型调用
- WHEN 计量
- THEN 以服务端 sys_llm_usage 采集为准(客户端无上报通道)

### Requirement: 套餐与周期账单

系统 SHALL 支持套餐(name/monthly_tokens/monthly_calls/price_per_mtok
[厘/千 token]/price_per_call[厘/次]),租户可绑套餐(配额取套餐值,
租户级策略可覆盖);系统 SHALL 每月 1 日为上月未出账租户生成账单
(sys_invoice:period/usage_json 明细[模型×token×单价]/amount[厘]/
status=PENDING),金额列 MUST 为整数厘(禁浮点);账单明细 MUST
按租户隔离可查。

#### Scenario: 套餐配额生效

- GIVEN 租户订阅「专业版」(月 100 万 token)
- WHEN 配额判定
- THEN 该租户 token 上限=套餐值

#### Scenario: 周期账单生成

- GIVEN 每月 1 日
- WHEN 出账检查执行
- THEN 上月未出账租户各落一条 PENDING 账单(含模型维度明细)

#### Scenario: 账单明细隔离

- GIVEN 租户 A 的账单
- WHEN 租户 B 查询
- THEN 404 同形,不可见

### Requirement: 开发者门户

系统 SHALL 提供开发者门户(/portal,登录即可,限自己租户,权限点
developer:portal):api-key 列表(复用既有端点)+ 当期用量看板
(三维进度+预警标记)+ 账单列表与明细。

#### Scenario: 开发者查看用量

- GIVEN 授予 developer:portal 的用户
- WHEN 访问门户
- THEN 展示自己租户的 key 列表/当期三维用量/账单,按租户隔离

#### Scenario: 他租户不可见

- GIVEN 租户 B 用户
- WHEN 访问门户
- THEN 只见自己租户数据(无跨租户入口)
