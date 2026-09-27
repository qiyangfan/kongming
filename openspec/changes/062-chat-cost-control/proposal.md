# 062-chat-cost-control 提案:用户问答面成本控制(限流 + 配额)

## Why

成本控制只覆盖了**最不需要保护的那条链路**。已核实的当前矩阵:

| 入口 | 鉴权 | 限流 | 配额 |
|---|---|---|---|
| `POST /v1/chat/completions` | api-key + scopes | — | ✅ 034 配额切面,超额 429 + `X-Quota-Warning` |
| `POST /chat/query` | `chat:execute` 权限点 | ❌ 无 | ❌ 无 |
| `POST /chat/query/stream` | `chat:execute` 权限点 | ❌ 无 | ❌ 无 |
| `POST /bot/{bot_key}/chat` | ❌ 无(免登录公开嵌入) | ❌ 无 | ❌ 无 |

全仓唯一的 `RedisRateLimiter` 调用点在 `toolcall/router.py:327`(工具执行限流)。而单次问答要烧 **5+ 次 LLM 调用**(understand + retrieve + generate + ≤2 次 selfheal + summarize,`agent_timeout_sec=120`)。

倒挂的后果:已有计费关系的 api-key 调用方被配额管着;匿名访客经公开嵌入页、任意登录用户刷问答,却是零成本控制。多租户下也无法向租户问责——账单月结时才发现某租户刷了三个月。这是"能否放开给用户用"的前置条件,不是优化项。

次要点:`chat` 面 SQL 执行行数上限是 inline 魔数 `limit=1000`(`chat/router.py:151`),而导出路径有 `export_max_rows` 配置项——同一语义两个来源,且其中一个不可配置。

## What Changes

1. **登录用户问答面**(`/chat/query` 与 `/chat/query/stream`,含 bot 登录态链路)加双闸:分钟级速率(`RedisRateLimiter`,(租户, 用户) 维度,fail-open 与现有口径一致)+ 日/月配额(`billing.check_quota`,租户(+可选用户)维度,超额 429 并回落 `X-Quota-Warning`)。
2. **公开嵌入面**(`/bot/{bot_key}/chat`)新增 (助手标识, 客户端 IP) 维度限流:客户端 IP 取既有 `ClientIpMiddleware` contextvar(经 `--forwarded-allow-ips` 约束,非客户端可伪造),fail-open。该面无用户/租户维度可用,这一维是**必须新增**而非重复造轮子。
3. **结果行数上限配置化**:`Settings.chat_result_max_rows` 取代 inline `limit=1000`,缺省 1000(行为零变化)。
4. **超限响应复用既有错误信封与 detail 码形态**:新增 `CHAT_RATE_LIMITED` / `CHAT_QUOTA_EXCEEDED`;前端 `ERROR_CODE_LABELS` 补两条中文文案。ADR-005:契约变更须 `make contracts` 并提交产物。

## Non-goals

- **按 token 精确预扣/实时计费**:本轮按"调用次数"配额;token 维度的成本可见性由 055 对账旁路承担,不重复建账。
- **agent 面并发上限/信号量**:属容量治理,与限流正交,另立 change。
- **per-bot 单独配额阈值与管理台 UI**:先用租户级默认值,精细化另立。
- **账号级封禁扩展**:auth 域已有登录失败锁定(ADR-003),不动。
- **付费/充值/套餐变更流程**:mock 支付通道(035)已覆盖交易,不扩。

## Capabilities

### Modified Capabilities

- developer-portal-billing:配额切面从「网关切面」扩展到「用户问答面」;新增「问答成本控制」Requirement(速率控制、配额、公开嵌入限流、行数上限可配置四项)。

## Impact

- **落点**:`apps/api/kongming/chat/router.py`(+ service)、`kongming/bot/router.py`、`kongming/config.py`、`kongming/toolcall/ratelimit.py`(维度泛化)、`apps/web/src/lib/api.ts`(`ERROR_CODE_LABELS`)。
- **需核对**:配额计数若基于 `sys_llm_usage` 按时间范围 count,须确认 `(tenant_id, created_at)` 索引存在;缺则随本变更补迁移(ADR-004/006)。
- **OpenAPI 契约变更**:新增 429 响应与两个错误码 → 必须 `make contracts` 并提交产物(ADR-005),CI 漂移门禁会兜底。
- **无 ADR 需要新增**:成本控制是 034 配额切面的同构延伸,不改变信任边界(ADR-002)与 schema 基线约定(ADR-004)。
