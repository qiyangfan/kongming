# Design: 运行时权益执行（runtime-entitlement）

## Context

现有 kongming 本地套餐/配额/账单/门户/mock 支付；锦官将拥有这些共享控制状态。LLM 用量已经经 muniu 计量。

## Goals / Non-Goals

**Goals:**
- 配额执行本地化。
- 计费控制面集中。
- 一个指标一个来源。

**Non-Goals:**
- 不做支付。
- 不做账单 UI。
- 不上报 LLM token/费用。

## Decisions

1. 权益与配额从锦官快照读取；本地执行 fail-closed。
2. 只报产品事件到锦官；LLM 只保留本地引用；本地只保留审计/执行。
3. 删除本地 plan/invoice/payment/portal 管理与 mock 通道；billing 归锦官。
4. 本地用量/权益页只读；服务 admin API 不暴露计费写。

## Risks / Trade-offs

- 配额口径漂移 → 契约测试/维度单位。
- 上报丢失 → 缓冲/重试/幂等。
- 双计 → 口径负向测试。

## Migration Plan

greenfield：删除本地计费控制数据；产品事件从零开始；LLM 对账走 muniu。
