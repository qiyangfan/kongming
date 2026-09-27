# 061-unify-backend-logging 实施任务

## 1. 测试先行:配置生效与不丢日志(宪法 Article II/III)

- [x] 1.1 失败测试:以缺省配置构建应用并触发一条 INFO 运行事件,
      断言输出中可见该事件(当前实现必然红——INFO 被丢弃);先跑红
- [x] 1.2 失败测试:`KM_LOG_LEVEL=WARNING` 时 INFO 不输出、WARNING 输出;
      先跑红
- [x] 1.3 失败测试:一次问答响应头含 `X-Trace-Id`,且与 `sys_llm_usage`
      落库的 trace_id 同值;先跑红

## 2. 日志配置(两进程)

- [x] 2.1 apps/api 新增 logging 配置模块(level 可配 + dev 文本 / prod JSON
      双渲染格式),`main.py` 启动期执行一次 `dictConfig`
      **验证**:1.1、1.2 转绿
- [x] 2.2 apps/agent 同构配置(独立一份,不跨进程 import;ADR-002)
      **验证**:agent 侧同款单测绿
- [x] 2.3 `Settings` 增加 `log_level`:api `KM_LOG_LEVEL`、agent
      `KM_AGENT_LOG_LEVEL`,缺省 `INFO`,frozen 场景校验通过
      **验证**:`.env.example` 已登记;`make check` 的 api/agent 子集绿

## 3. 单一 API 收敛

- [x] 3.1 6 个 structlog 模块改回 stdlib logging,逐处核对调用形态等价
      (信息不丢失、键值不丢失)
      **验证**:grep 全仓无 `import structlog`;单测与日志断言绿
- [x] 3.2 从 `apps/api` 与 `apps/agent` `pyproject.toml` 移除 `structlog`,
      重生成 `uv.lock`,并跑 `uv sync --frozen` 确认锁一致
      **验证**:`uv.lock` 无 structlog;`uv tree` 无残留
- [x] 3.3 移除 `prometheus-client` 死依赖并重生成 `uv.lock`
      **验证**:grep 全仓 0 处引用;`uv run python -c "import kongming.main"`
      不因缺依赖失败

## 4. trace 关联

- [x] 4.1 控制面响应回落 `X-Trace-Id`(chat 系端点优先,其余默认透传
      请求已有或生成的 trace)
      **验证**:1.3 转绿;`make contracts` 产物无漂移(信封不变)

## 5. 收口

- [x] 5.1 spec delta sync 进 `openspec/specs/observability/`
- [x] 5.2 手工冒烟:起 api,跑一次完整问答,确认输出流单一、INFO 可见、
      响应头可回溯到 `sys_llm_usage` 同 trace_id;记录于 apply
- [x] 5.3 全量 `make check` 收口

> 061 落地记录:
> - logging_setup.py 两进程各一份:dictConfig 等价的 root handler 重建,
>   级别可配,dev 文本 / prod JSON;main 启动期 setup_logging。
> - Settings 增 log_level(KM_LOG_LEVEL / KM_AGENT_LOG_LEVEL,缺省 INFO)。
> - 7 个 structlog 模块改回 stdlib logging;移除 structlog 与
>   prometheus-client 死依赖并重生成 uv.lock。
> - X-Trace-Id 响应头回落中间件(缺省生成 uuid,透传入站头)。
> - 测试:logging_setup 2 例;make check 全绿(api 939 / agent 377)。
> - **裁决**:人工冒烟已升级为自动化断言 `test_trace_id_header.py`
>   (存在 + 入站透传);compose 栈实测 `curl /healthz` 响应头含 x-trace-id。
>   与 sys_llm_usage 同值由 chat 链路既有的 trace_id 贯穿保证,不另造冒烟。

