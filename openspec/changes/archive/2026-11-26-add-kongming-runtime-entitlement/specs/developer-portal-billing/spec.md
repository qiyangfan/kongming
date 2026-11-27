# Spec Delta: developer-portal-billing

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
