# Design: 运行时用量与健康上报（runtime-usage-reporting）

## Context

现有 kongming 本地 sys_llm_usage/审计；锦官需要产品事件与健康。LLM 用量已经经 muniu 计量，kongming 再报会双计。

## Goals / Non-Goals

**Goals:**
- 产品事件/健康可聚合。
- 上报不拖垮主链路。
- 一个指标一个来源。

**Non-Goals:**
- 不做计费。
- 不上报 LLM token/费用。
- 不传敏感内容。

## Decisions

1. 事件 envelope 含 source/tenant/user/bot/product_event/health/幂等键。
2. 本地缓冲 + 重试；失败不阻断。
3. 只报产品事件/健康摘要，不报提示词/结果内容；LLM 只保留本地引用。
4. 服务身份认证；匿名拒绝。

## Risks / Trade-offs

- 缓冲膨胀 → 上限 + 丢弃策略 + 告警。
- 重复上报 → 幂等键。
- 双计 → 口径负向测试。

## Migration Plan

greenfield：新增产品事件/健康上报；本地审计保留。
