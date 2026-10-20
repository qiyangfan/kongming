# 平滑问答速率窗口并修复 Redis 原子计数

## Why

问答成本闸和公开嵌入限流目前共用固定窗口计数。固定窗口在窗口切换前后可能连续放行两份额度，不利于稳定控制昂贵的 LLM 请求。更紧急的是，共享计数器把 `SET NX EX` 和 `INCR` 分成两次 Redis 操作：键可能在两次操作之间过期，随后 `INCR` 创建无 TTL 的新键，使该限流身份长期被拒绝。

本变更为问答成本闸采用跨窗口平滑的分钟速率约束，同时修复所有既有固定窗口计数的原子 TTL 管理。租户/用户及助手/IP 的限流维度、现有配置值、429 错误语义、Redis 故障时放行和日/月调用配额保持不变。

## What Changes

- 登录问答(`/chat/query`、`/chat/query/stream`)与公开嵌入问答(`/bot/{bot_key}/chat`)在 60 秒速率判定中考虑紧邻窗口的既有流量，窗口切换不再立即发放完整新额度。
- 共享 Redis 固定窗口计数以单个原子操作递增并确保 TTL；此修正覆盖工具总配额及工具域配额，保持其固定窗口行为和配额不变。
- 问答限流仍按 `(tenant_id, user_id)` 分桶；公开嵌入仍按 `(bot_key, trusted_client_ip)` 分桶。超限仍在进入 LLM 调用链之前返回原有 429。

## Non-goals

- 不改变工具总配额和工具域配额的固定窗口策略。
- 不引入令牌桶、漏桶、排队整形、应用级并发闸或新的全站入口限流。
- 不改变限流阈值、租户日/月调用配额、Redis 故障时 fail-open 语义、可信客户端 IP 解析或 RBAC。
- 不增加依赖、数据库表、迁移或 HTTP/OpenAPI 契约字段。

## Capabilities

### Modified Capabilities

- `developer-portal-billing`:增加登录问答与公开嵌入端点的窗口边界平滑行为。

## Impact

- **代码**:`apps/api/kongming/toolcall/ratelimit.py`、`apps/api/kongming/chat/costgate.py` 及其端口/组合根测试。
- **行为契约**:仅修改 `developer-portal-billing` 的问答速率行为；`tool-calling` 继续使用固定窗口。
- **架构**:继续使用现有 Redis 8，无新增 ADR、数据库 schema 或 OpenAPI 变更。
- **验证**:覆盖原子 TTL、窗口边界、身份隔离、fail-open、429 不进入 LLM，以及工具固定窗口零行为变化；完成严格 OpenSpec 校验。
