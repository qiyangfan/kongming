# Spec Delta: payment-gateway

## REMOVED Requirements

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
