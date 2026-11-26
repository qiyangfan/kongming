# Proposal: 2026-11-26-add-kongming-runtime-usage-reporting（运行时用量与健康上报（runtime-usage-reporting））

## Why

锦官需要 kongming 的产品事件与健康来执行权益、聚合用量与展示服务状态；LLM token/费用由 muniu 唯一上报，kongming 不重复上报。

## What Changes

- 定义产品事件/health 事件 envelope；服务身份认证。
- 断网缓冲/重试/幂等。
- 控制面不可用不阻断本地执行。
- 固定口径：LLM token/费用只由 muniu 上报；kongming 只报产品事件。

## Non-goals

- 不做计费。
- 不做控制面聚合。
- 不上报 LLM token/费用（由 muniu 负责）。
- 不上报提示词/敏感内容。

## Capabilities

### New Capabilities

- `runtime-usage-reporting`：运行时 usage/health 事件上报

### Modified Capabilities

无

## Impact

apps/api 上报客户端/缓冲；控制面 ingest；不写服务库之外的数据。
