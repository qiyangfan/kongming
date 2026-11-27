# Proposal: 2026-11-26-add-kongming-runtime-entitlement（运行时权益执行（runtime-entitlement））

## Why

套餐/配额/权益改由锦官拥有；kongming 本地计费/计划/发票/支付必须移除，只保留执行与产品事件上报。LLM 用量只由 muniu 上报。

## What Changes

- 按锦官权益快照执行配额/权益。
- 上报产品事件到锦官；不上报 LLM token/费用。
- 移除本地计划/账单/发票/支付/门户控制面。

## Non-goals

- 不创建套餐/账单/发票。
- 不做真实收款。
- 不把计费状态复制到本地。
- 不重复上报 LLM 用量。

## Capabilities

### New Capabilities

- `runtime-entitlement`：控制面权益快照的本地执行与用量上报

### Modified Capabilities

无

## Impact

billing/llmusage/chat 执行层；删除本地计划/账单/支付端点；不写控制面。
