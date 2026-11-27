# Spec Delta

## Purpose

孔明的运行时权益执行：以锦官套餐、配额与权益快照为准执行并上报产品事件；本地不再拥有计划、账单、发票、支付与开发者门户控制面；LLM 用量只由 muniu 上报。

## ADDED Requirements

### Requirement: 权益与配额执行

kongming SHALL 按锦官权益/配额快照执行；超限 SHALL 拒绝并审计；缺失/非法安全类权益 SHALL fail-closed；本地 SHALL NOT 保存计划/账单/发票/支付状态；billing 控制面归锦官。

#### Scenario: 超限拒绝

- GIVEN 租户配额耗尽
- WHEN 新请求
- THEN 429，审计记录

#### Scenario: 本地无计费状态

- WHEN 检查本地表/端点
- THEN 无计划/账单/支付控制数据

### Requirement: 产品事件上报

kongming SHALL 将租户/用户/Bot 维度的产品事件推送到锦官；事件 SHALL 幂等并支持断网缓冲；锦官不可用 SHALL NOT 阻断本地执行；SHALL NOT 上报 LLM token/费用（由 muniu 唯一上报）；上报内容 SHALL NOT 含凭据。

#### Scenario: 正常上报

- WHEN 一轮问答完成
- THEN 产品事件发送，锦官聚合

#### Scenario: 不重复 LLM

- WHEN 产生 LLM 调用
- THEN 只记录本地引用，不上报 token/费用

#### Scenario: 控制面不可用

- GIVEN 锦官宕机
- WHEN 产生产品事件
- THEN 本地缓冲，恢复后补发

## REMOVED Requirements

### Requirement: 套餐与周期账单

**Reason**: 套餐、配额策略与账单归锦官共享控制面；kongming 只按权益快照执行，不再本地创建或保存计划/账单。

**Migration**: 本地 `sys_billing_plan`/`sys_quota_policy` 改名 legacy_ 保留只读历史；新套餐/账单在锦官控制台维护。

### Requirement: 开发者门户

**Reason**: 开发者门户的账单、用量与 API key 自助面归锦官；kongming 移除 `/portal/*` 页面与端点。

**Migration**: 旧 `/portal` 深链不再提供入口；账单与用量查询改由锦官控制台承担。

### Requirement: 多维度配额 scope 与绑定校验

**Reason**: 配额 scope 与租户绑定归锦官；kongming 只消费权益快照中的租户配额。

**Migration**: 本地配额策略表只读历史；租户/团队/用户/Bot 级配额改由快照下发。

### Requirement: 多维配额用量口径与唯一性

**Reason**: 多口径配额聚合归锦官；kongming 不再维护 plan/policy 维度的唯一性约束。

**Migration**: 用量事实仍落本地 `sys_llm_usage`；聚合与对账由锦官/木牛按口径完成。

### Requirement: 网关多路配额叠加判定

**Reason**: 网关配额改为按租户权益快照的 BOT/调用日配额执行，本地多路叠加策略退役。

**Migration**: 删除本地 plan/bot/user/team 配额策略来源；超限仍 429 并审计。

### Requirement: 计费配置租户清单

**Reason**: 计费配置归锦官；kongming 不再提供计费管理清单端点。

**Migration**: 页面与 `/system/billing/*` 端点删除；租户清单改由锦官控制台提供。

### Requirement: LLM 成本对账(只读旁路)

**Reason**: LLM token/费用只由木牛上报并在锦官聚合；kongming 不再提供本地成本对账端点。

**Migration**: 移除 `KM_MUNIU_ADMIN_*` 与 `KM_COST_RECONCILE_THRESHOLD`；对账改由管理域完成。

### Requirement: 支付单创建

**Reason**: 本地支付通道与收银台退役；支付与账单归锦官。

**Migration**: 本地支付表改名 legacy_ 只读；`/portal/payments` 端点删除。

### Requirement: 回调核销(统一路径)

**Reason**: 本地支付回调核销删除，避免第二写者。

**Migration**: 支付回调只在锦官处理；kongming 不再接受支付回调。

### Requirement: 支付通道抽象与 mock 通道

**Reason**: mock 支付是本地计费控制面的一部分，随计费退役。

**Migration**: 删除 `KM_BILLING_MOCK_*` 与 `KM_BILLING_PAYMENT_TTL_MINUTES`。

### Requirement: 收银台

**Reason**: `/pay` 收银台页面随本地支付退役。

**Migration**: 支付入口由锦官提供；旧书签不再可达。

### Requirement: 支付单查询与取消

**Reason**: 本地支付单查询/取消端点删除。

**Migration**: 支付状态在锦官控制台查询；kongming 不保存支付状态。

### Requirement: 账单作废

**Reason**: 账单作废属计费控制面，归锦官。

**Migration**: `/billing/invoices/{id}/void` 端点删除；作废操作在锦官执行。
