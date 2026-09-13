## Purpose

开发者自助门户(api-key 管理/用量看板/账单)+ 三维配额 fail-closed
(token/调用/成本)+ 套餐与周期账单;用量以服务端采集为唯一事实源
(不经客户端,防绕过),api-key 体系复用既有(025,语义不变)。

## Requirements

### Requirement: 用量配额(fail-closed)

系统 SHALL 支持租户级三维配额策略(dimension ∈ TOKENS/CALLS/COST,
period=MONTHLY,limit_value,warn_pct 默认 80),由超管配置(system:
billing:manage)。OpenAI 兼容网关(POST /v1/chat/completions)执行前
MUST 判定:当月 token 用量(sys_llm_usage 聚合)+ 当月调用数(api
渠道审计计数)+ 成本估算(套餐单价×token),任一维度超限 MUST
拒绝(429 QUOTA_EXCEEDED)并落审计,不执行请求;达到 warn_pct
MUST 以响应头 X-Quota-Warning 标记预警(不拒绝)。**口径注记**:
CALLS 维度=工具执行计数(api 渠道审计行数,纯问答型 Bot 无工具
调用恒 0,既定语义);请求形态校验(空问题 400)先于配额判定(429)
为既定顺序;**COST 维成本估算以绑定套餐单价为前提**——未绑套餐
租户单价按 0,COST 策略零信号(配置面 MUST 给出提示,035 R1-A4)。

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
租户级策略可覆盖);套餐 SHALL 支持更新(名称/额度/单价,审计;改价
不回溯既有账单——账单明细单价为出账时快照)与删除(存在绑定租户
MUST 409 拒删,无绑定删除+审计)。系统 SHALL 每月 1 日为上月未出账
租户生成账单(sys_invoice:period/usage_json 明细[模型×token×单价]/
amount[厘]/status=PENDING),金额列 MUST 为整数厘(禁浮点);账单
明细 MUST 按租户隔离可查。账单状态机:PENDING →(支付闭环,见
payment-gateway spec)→ PAID;PENDING →(超管作废)→ VOID。
管理面账单清单 SHALL 分页(page/size/total),不设裸 LIMIT 截断。

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

#### Scenario: 绑定中套餐拒删

- GIVEN 套餐「专业版」仍被租户绑定
- WHEN 超管删除该套餐
- THEN 409 PLAN_IN_USE,套餐保留

#### Scenario: 管理面账单分页

- GIVEN 库内账单超一页
- WHEN 超管按 page/size 查询
- THEN 返回当页行与 total,可翻页遍历全集

### Requirement: 开发者门户

系统 SHALL 提供开发者门户(/portal,登录即可,限自己租户,权限点
developer:portal):api-key 列表(复用既有端点)+ 当期用量看板
(三维进度+预警标记)+ 账单列表与明细;PENDING 账单 SHALL 提供
「去支付」入口(创建支付单并收银台,见 payment-gateway spec),
账单行 SHALL 展示支付状态(PENDING/PAID/VOID)。

#### Scenario: 开发者查看用量

- GIVEN 授予 developer:portal 的用户
- WHEN 访问门户
- THEN 展示自己租户的 key 列表/当期三维用量/账单,按租户隔离

#### Scenario: 他租户不可见

- GIVEN 租户 B 用户
- WHEN 访问门户
- THEN 只见自己租户数据(无跨租户入口)

#### Scenario: 账单去支付

- GIVEN 门户内一张 PENDING 账单
- WHEN 点击「去支付」
- THEN 创建支付单并进入收银台,完成 mock 支付后账单置 PAID

### Requirement: Bot 维度配额

配额策略 SHALL 支持 scope(TENANT[默认]/BOT):BOT 级策略以
scope_ref 绑定**该租户内**一个 Bot(跨租户或不存在的 scope_ref
MUST 拒绝),限定该 Bot 的当月 TOKENS/COST 用量(以 sys_llm_usage
的 bot_id 归属为准);同一(租户,维度,周期,scope,scope_ref)组合
MUST 唯一(零 uuid 归一的表达式唯一约束,TENANT 行不因 NULL
scope_ref 重复)。网关判定 MUST 双路:Bot 级(当前 api-key 绑定
Bot 命中策略)与租户级各自独立判定,任一超限即拒,拒绝维度文案
MUST 带 scope 前缀(如 BOT:TOKENS)以归因审计。BOT 级无套餐
兜底(未配置=不限);CALLS 维度不支持 BOT 级(工具审计无 Bot
归属,不立约)。Bot 被删除后其策略保留但零命中,不立级联。

#### Scenario: Bot 级超限拒绝

- GIVEN Bot A 配置 BOT 级 TOKENS 上限且当月已达
- WHEN 以 Bot A 的 api-key 调用网关
- THEN 429 QUOTA_EXCEEDED: BOT:TOKENS + 审计,不执行

#### Scenario: Bot 级与租户级独立

- GIVEN Bot A BOT 级未超,但租户级 CALLS 已超限
- WHEN 调用
- THEN 按租户级 429 拒绝

#### Scenario: 跨租户 scope_ref 拒绝

- GIVEN 超管为租户 A 配置 BOT 策略
- WHEN scope_ref 指向租户 B 的 Bot
- THEN 404 拒绝,不落策略

#### Scenario: 同 scope 组合唯一

- GIVEN 租户 A 已有 BOT 级(bot X)TOKENS 策略
- WHEN 再次创建同组合策略
- THEN upsert 语义更新限额,不产生重复行

### Requirement: 计费配置租户清单

系统 SHALL 提供计费配置面专用租户清单端点(挂 system:billing:
manage,返回 id/tenant_key/name),使仅授 billing 管理权限的角色
可完成策略/绑套餐配置,MUST NOT 依赖用户管理权限
(system:user:manage)取租户下拉。

#### Scenario: 单授 billing 管理可配置

- GIVEN 自定义角色仅授 system:billing:manage(无 user:manage)
- WHEN 打开计费配置页并选择租户
- THEN 租户下拉正常出列,策略/绑套餐全流程可用

### Requirement: LLM 成本对账(只读旁路)

系统 SHALL 提供管理面只读端点 `GET /system/billing/cost-reconciliation`(管理鉴权),按时间窗并列呈现产品牌价轨(`sys_llm_usage` token 总量 + `sys_invoice` 牌价账单额,整数厘)与网关成本轨(muniu 管理 API `GET /admin/v1/usage` 的 credit 汇总),计算偏差率并在超过阈值(可配,缺省 0.2)时落审计告警;两轨 MUST NOT 硬换算(牌价为产品定价,允许与成本解耦),比对对象是量级与趋势。对账为纯读旁路:任一侧不可用 MUST 降级为报告内显式「不可用」标注(HTTP 200 + 结构化标注),MUST NOT 影响计费主链路、MUST NOT 以静默空值替代。muniu 管理凭证 MUST 经环境变量注入,不落库、不入日志。

#### Scenario: 双轨正常比对

- GIVEN 窗口内两侧数据均可取
- WHEN 管理员调用对账端点
- THEN 返回双轨汇总、偏差率与数据来源标注;偏差率超阈值时产生审计告警记录

#### Scenario: 成本侧不可用降级

- GIVEN muniu 管理面不可达或未配置凭证
- WHEN 调用对账端点
- THEN 200 返回,成本侧字段显式标注「不可用」及原因,牌价轨数据正常呈现,计费链路零影响

#### Scenario: 凭证不入日志

- GIVEN 已配置 muniu 管理 token
- WHEN 任意对账请求执行与日志落盘
- THEN 日志与审计中不含 token 明文(脱敏断言)
