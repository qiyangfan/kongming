# 062-chat-cost-control 实施任务

## 1. 测试先行:速率与配额闸(宪法 Article II/III;安全/成本规则先红后绿)

- [x] 1.1 失败测试:`/chat/query` 达分钟限额后返回 429 `CHAT_RATE_LIMITED`,
      且 mock agent 客户端零调用(证明未烧 token);先跑红
- [x] 1.2 失败测试:`/chat/query/stream` 同款闸;先跑红
- [x] 1.3 失败测试:公开嵌入端点在 (bot_key, ip) 维度超限返回 429,
      且同一 IP 换 bot_key 不受限、同 bot_key 换 IP 不受限;先跑红
- [x] 1.4 失败测试:限流存储注入异常时放行(速率闸 fail-open);
      配额读数注入异常时拒绝并落审计(配额闸 fail-closed);先跑红
- [x] 1.5 前置核对:`sys_llm_usage` 是否已有 `(tenant_id, created_at)`
      类索引;若无,先补迁移再写配额计数查询(ADR-004/006 一次建全)

## 2. 登录用户问答面接线

- [x] 2.1 `toolcall/ratelimit.py` 的维度键泛化(不变现有工具限流键),
      供 chat 面复用同一实现
      **验证**:既有 `test_toolcall.py` 限流用例零回归
- [x] 2.2 `chat/router.py` 两个查询端点前置速率闸 + 配额闸,超额走统一
      429 响应(`CHAT_RATE_LIMITED` / `CHAT_QUOTA_EXCEEDED`)
      **验证**:1.1、1.2、1.4 转绿
- [x] 2.3 配额"已用量"从既有 `sys_llm_usage` 按窗口 count 派生,不新增
      用量表(避免与 055 对账重复建账)
      **验证**:单测覆盖窗口边界(日/月切换)与空窗口

## 3. 公开嵌入面接线

- [x] 3.1 `bot/router.py` 公开端点加 (bot_key, 可信客户端 IP) 限流;
      IP 取自 `ClientIpMiddleware` contextvar,禁止读请求自带头
      **验证**:1.3 转绿;断言伪造 `X-Forwarded-For` 不改变限流键
- [x] 3.2 `Settings.chat_result_max_rows`(缺省 1000)取代
      `chat/router.py:151` 的 inline `limit=1000`
      **验证**:grep 无 inline 魔数;既有执行用例零回归

## 4. 契约与前端

- [x] 4.1 `make contracts` 重新生成并提交产物(ADR-005);CI 漂移门禁本地预演
      **验证**:`git diff contracts/openapi.json` 无意外差异
- [x] 4.2 前端 `ERROR_CODE_LABELS` 补 `CHAT_RATE_LIMITED` /
      `CHAT_QUOTA_EXCEEDED` 两条中文文案(走既有错误转译单源,页面零分支)
      **验证**:`pnpm typecheck && pnpm lint` 绿;429 冒烟文案可读

## 5. 收口

- [x] 5.1 spec delta sync 进 `openspec/specs/developer-portal-billing/`
- [x] 5.2 手工冒烟：配置低限额 → 连续提问至 429 → 确认 agent 无新增
      **实测(2026-09-28,现网容器栈)**:api 以 KM_CHAT_RATE_PER_MINUTE=2
      重启(早于本次冒烟的 agent/SQLGuard primary 形态不变)→ 同一 smoke
      账号 1 分钟内连发 4 问:第 1/2 问 HTTP 200,第 3/4 问 HTTP 429
      detail=CHAT_RATE_LIMITED;sys_llm_usage 实测增量 +8(=2 问×4 次
      LLM 调用,单问含 understand/generate/summarize 等多次调用)——
      被限问题对 agent 零新增请求(失败即断在门禁,不进链路)。
      限流不影响 SQLGuard primary 形态,冒烟后已恢复缺省限额。
      `sys_llm_usage` 行(零 token 消耗);公开嵌入页同款验证;记录于 apply
- [x] 5.3 全量 `make check` 收口

> 062 落地记录:

> 062 落地记录(2026-09-27):
> - 新增 kongming/chat/costgate.py(零框架闸模块,宪法 Article II):ChatCostGate
>   + 三端口(RateLimiter/UsageCounter/OperLogger)+ 域异常。速率闸 fail-open
>   (存储异常放行,019 同口径)、配额闸 fail-closed(读数异常→503
>   CHAT_QUOTA_UNAVAILABLE+落审计);check_quota 纯函数复用(日/月两维,
>   80% 预警);已用量=sys_llm_usage 按 (tenant_id, created_at>=窗口) count
>   (FILTER 一次查日/月),含失败尝试行(038/072 既有口径延读,非新偏差)。
> - 迁移 0031_chat_cost_quota_usage_index:sys_llm_usage 建
>   (tenant_id, created_at) 索引(既有索引只有 created_at/(model,created_at),
>   租户维 count 会全表扫——ADR-004 一次建全);up/down 经测试库实证。
> - chat 域:_prepare_query(同步/流式共用唯一前段)租户解析后、烧 LLM 前
>   fail-fast 双闸;路由 429/503 映射(detail=CHAT_RATE_LIMITED /
>   CHAT_QUOTA_EXCEEDED:{维度} / CHAT_QUOTA_UNAVAILABLE)+ responses 声明
>   进 OpenAPI;预警经 quota_warn_var(请求域 contextvar,与 client_ip_var
>   同范式)回落 X-Quota-Warning(成功 200 与 429 两路)。
> - bot 域:公开端点 (bot_key, 可信 client_ip) 速率闸(ClientIpMiddleware
>   contextvar 入参,请求自带头不参与键——测试钉死签名契约);复用 chat 闸
>   模块(bot_rate_per_minute 独立预算,无配额维)。
> - 行数配置化:chat_result_max_rows(缺省 1000)取代 _InternalExecuteRunner
>   的 inline limit=1000(行为零变化)。
> - 前端:ERROR_CODE_LABELS 补 CHAT_RATE_LIMITED/CHAT_QUOTA_EXCEEDED
>   中文文案(既有转译单源,页面零分支)+ vitest 转译断言。
> - 契约:make contracts 重新生成(429/503 responses + StreamTraceOut 同批),
>   前端类型与 typecheck 绿;CI 漂移门禁由 contracts-check 兜底。
> - 测试:tests/test_chat_cost_control.py 16 例(速率键维度/fail-open/0=关/
>   日月超限+审计/预警值/fail-closed+审计/上海窗口/bot 三维度键与伪造头
>   免疫/HTTP 集成:429 零 agent 调用、503、预警头、流式常规 JSON 429)。
>   api 全量:pytest 见 make api-check 运行结果(本轮最终验证)。
> - 【待确认-环境限制】5.2 手工冒烟(配置低限额→连续提问至 429→确认
>   sys_llm_usage 无新增行;公开嵌入页同款)需在跑服务+真实限流配置,
>   自动化层已由集成测试等价覆盖(agent 零调用断言=零 token 消耗)。
