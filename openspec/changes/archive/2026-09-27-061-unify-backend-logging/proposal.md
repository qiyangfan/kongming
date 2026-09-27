# 061-unify-backend-logging 提案:后端日志体系统一与可观测性补齐

## Why

两个进程均**无任何 logging 配置**——全仓 grep 无 `dictConfig` / `basicConfig` / `logging.config` / `--log-config`。uvicorn 默认只给 `uvicorn.*` logger 挂 handler、不动 root logger,于是 apps/api 51 个模块与 apps/agent 全部的 stdlib 日志,INFO/DEBUG 被 root 的 lastResort handler(WARNING 级)静默丢弃。已实测复现:同一次运行 `log.info` 无任何输出、`log.warning` 有输出。

被丢弃的恰是运维最需要的信息:apps/api 13 处 `log.info`,覆盖 `_daily_archive_loop` / `_monthly_invoice_loop` / `_payment_expire_loop` / `_attachment_cleanup_loop` 四条定时任务的每次执行结果、trigger scheduler 的 tick、mcp/sandbox 出站调用的成功记录。一个以审计留存为卖点的自建产品,运维侧对这些完全失明——归档失败只能靠"上个月的数据怎么没了"事后发现。

同时存在**双轨**:6 个模块(`trigger/scheduler.py`、`attachment/cleanup.py`、`mcp/client.py`、`sandbox/runner.py`、`sandbox/repository.py`、`workflow/engine.py`)用 structlog,未配置时默认写 **stdout + key=value**;其余用 stdlib 写 **stderr**。同一份日志被劈成两个流、两种格式,无法用同一条管道收集。

另有两项收尾:`prometheus-client` 在 `apps/api/pyproject.toml` 声明为生产依赖但全仓 0 处引用;`trace_id` 已贯穿审计/用量表/trace 时间线,却没有响应头回落客户端,用户报障时无法提供可定位标识。

## What Changes

1. **统一到 stdlib logging 单一 API**:6 个 structlog 模块改回 `logging.getLogger(__name__)`;从 `apps/api` 与 `apps/agent` 的 `pyproject.toml` 移除 `structlog` 依赖并重生成 lock。
2. **两进程各配一份 `logging.config.dictConfig`**,在各自启动期执行一次:root logger 显式挂 handler、level 可配、dev 用可读文本格式、prod 用 JSON 格式(同一事件字段一致)。两进程不可互相 import(ADR-002),各自持一份小配置是刻意选择,不强行抽共享包。
3. **`Settings` 增加日志级别**:api 为 `KM_LOG_LEVEL`、agent 为 `KM_AGENT_LOG_LEVEL`(遵循各自 `env_prefix`),缺省 `INFO`;登记 `.env.example`。
4. **移除 `prometheus-client` 死依赖**(同步 `uv.lock`)。
5. **`X-Trace-Id` 响应头回落**:控制面响应携带本轮问答 trace_id(与用量明细/审计/trace 时间线同一值);并以 contextvar 注入日志 record,使任一运行日志可关联到 trace。

## Non-goals

- **引入 OTel / Jaeger / SkyWalking / APM 或任何指标端点**:无采集后端与消费需求;`prometheus-client` 已实证"先引后不用"的代价,不再重复。
- **日志外发与检索后端**(ELK/Loki/云日志):属部署运维面,另立 change。
- **uvicorn access log 迁到自建中间件**:uvicorn 现成能力够用,替换只增复杂度与性能损耗。
- **日志落库**:审计域已有 `sys_oper_log` / `sys_sql_audit` 两条专用管线,不建第三份重复沉淀。
- **per-module 日志级别细配**:单一级别满足自建部署,细配属过度配置。

## Capabilities

### Modified Capabilities

- observability:新增「运行日志可观测」Requirement——运行日志 SHALL 有单一 API、单一输出流、可配置级别、可关联 trace_id;未配置时 SHALL NOT 静默丢弃 INFO 及以上。

## Impact

- **落点**:`apps/api/kongming/main.py`(+ 新 logging 配置模块)、`apps/agent/kongming_agent/main.py`、上述 6 个 structlog 模块、两进程 `pyproject.toml` 与 `uv.lock`、`.env.example`。
- **无 schema 变更**(ADR-004);**无 OpenAPI 契约变更**——`X-Trace-Id` 为响应头,不改变响应体信封(ADR-005 零触发,`make contracts` 仍应跑一次确认产物无漂移)。
- **风险**:移除 structlog 需逐处核对调用形态(6 处均为 `log.info(msg, **kw)`,stdlib `%`-style 可直接对应);移除后需确认无人依赖 structlog 的 key=value 输出做日志解析(全仓无此类消费,已核)。
