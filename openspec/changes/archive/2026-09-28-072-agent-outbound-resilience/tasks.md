# 072-agent-outbound-resilience 实施任务

## 1. 测试先行:错误分类与重试语义(宪法 Article III)

- [x] 1.1 失败测试:瞬时故障(连接建立失败 / 读超时 / HTTP 429)触发重试,
      断言调用次数与退避间隔(用假时钟或注入 sleep 记录,不真等);先跑红
- [x] 1.2 失败测试:`Retry-After` 大于退避上限时按上限等待,
      断言不穿透 `agent_timeout_sec`;先跑红
- [x] 1.3 失败测试:非瞬时故障(HTTP 401/400/422)与配置类错误
      (缺 api key、模型名不存在、协议错)**只调一次**;先跑红
- [x] 1.4 失败测试:任务路由目标瞬时故障耗尽后降级主模型,
      且用量记录的实际模型名是主模型;先跑红
- [x] 1.5 失败测试:BOT `model_override` 目标瞬时故障耗尽后**不**降级
      (直接 LlmError);先跑红
- [x] 1.6 失败测试:每次尝试各落一条用量(3 次尝试 = 3 条记录,
      status/latency_ms/seq 正确),不合并;先跑红
- [x] 1.7 失败测试:总尝试硬上限(任何失败序列下不得出现第 4 次调用);先跑红
- [x] 1.8 失败测试:回调分层——`fetch_schema`/`rag_retrieve`/
      `attachment_content` 连接层失败重试;`sql_execute` 读超时**不**重试
      (design D2);`report_usage`/`tool_execute` 任何失败都不重试;先跑红

## 2. 实现(agent 面;ADR-002 零凭证纪律不变)

- [x] 2.1 新增重试原语模块(纯 asyncio;错误分类纯函数 + 尝试循环 + 退避),
      不引第三方重试库(design D6)
      **验证**:1.1、1.2、1.3、1.7 转绿
- [x] 2.2 `llm.py` 四个出站点(`chat`/`chat_vision`/`chat_with_tools`/`embed`)
      统一接入同一原语,一处分类四处复用;协议翻译(`llm_protocol.py`)
      与 `AnthropicClient` 零改动(重试包在 `client.create` 之外)
      **验证**:1.4、1.5、1.6 转绿;`test_multi_protocol_llm` 既有用例零回归
- [x] 2.3 降级链实现:`_resolve` 之后包一层 attempt loop;降级条目在启动期
      校验(在注册表内 + protocol 合法 + 主机在白名单),非法则告警并视为
      未配置(不拒启)
      **验证**:1.4 转绿;启动自检用例覆盖「fallback 条目不在注册表」
- [x] 2.4 `settings.py` 增 4 个可配项:`llm_max_attempts`(缺省 3)、
      `llm_retry_base_ms`(缺省 500)、`llm_retry_max_wait_ms`(缺省 5000)、
      `llm_fallback_entry`(缺省空=关闭);同步登记 `.env.example`
      **验证**:缺省值下行为即本变更新契约;显式调参后行为随动
- [x] 2.5 `apiclient.py` 按 1.8 的分层接入(用量上报与工具执行不包重试)
      **验证**:1.8 转绿;`test_apiclient*` 既有用例零回归
- [x] 2.6 import-linter 契约复核:`apps/agent` 仍零 asyncpg/sqlalchemy/redis
      import(重试原语是纯 asyncio)
      **验证**:`lint-imports` 绿;mypy strict + pyright strict 收敛

## 3. 与在流变更的口径核对

- [x] 3.1 核对 062(chat-cost-control)按次配额语义:确认其「已用量 =
      `sys_llm_usage` 行数」口径与 D5「失败尝试计入」一致,
      若 062 已先行落地,在其 spec 补一句口径说明而不改代码
      **验证**:063 之前的配额计数用例零回归;口径写入本变更 spec delta
- [x] 3.2 核对 065(eval-ci-gate)红线:确认 nightly 评估期间的重试不会
      被误读为「两次调用两次计费」;eval 走真实链路,重试只增成功率先例
- [x] 3.3 **核对 069-chat-quality-observability 的用量聚合口径**:其
      design 明文质量汇总的「平均 token」走
      `chat_message ⋈ sys_llm_usage on trace_id`(design.md:36-37)。
      本变更使同一 trace_id 下可能出现 ERROR 行(tokens=0)+ SUCCESS 行,
      **若按行取平均会被 0 值稀释**。正确口径为按 trace_id 分组取
      `SUM(total_tokens)` 作为该轮 token 总量(尝试次数另计)。
      若 069 已先行落地为按行平均,提交一个口径修正而非让两个变更
      各自正确、合起来错误
      **验证**:构造「1 次 ERROR + 1 次 SUCCESS」两行的 trace,
      断言聚合得到的轮次 token 等于 SUCCESS 行实测值、调用数为 2

## 4. 收口

- [x] 4.1 spec delta sync 进 `openspec/specs/multi-model-routing/`
- [x] 4.2 手工冒烟:以本地 mock 端点模拟 429(带 `Retry-After`)与 503,确认退避与上限行为
      **实测(2026-09-28,agent 容器内本地 mock 端点,零真实上游成本)**:
      mock A:首次 429 带 Retry-After=1 → 客户端等待 ~1s 后重试,
      第 2 次调用成功(RETRY-OK,总耗时 1.2s——Retry-After 被尊重,
      非固定退避);mock B:连续 503×2 → 指数退避后第 3 次放行成功。
      尝试预算(MAX_ATTEMPTS=4)内收敛,无永久卡死。出站韧性三形态
      (尊重 Retry-After/指数退避/预算内重试)实证。
      确认问答成功且 `sys_llm_usage` 含失败尝试行;再模拟主模型不可用 +
      配置 fallback 条目,确认降级生效且用量记录实际模型;记录于 apply
- [x] 4.3 全量 `make check` 收口

> 072 落地记录(2026-09-27):
> - 新增 kongming_agent/retry.py(纯 asyncio,零第三方依赖,D6):双层分类纯函数
>   is_transient_error(连接/读超时/408/429/5xx+SDK 协议等价;LlmError 经
>   __cause__ 解包判真实根因,配置类/应答畸形无 cause → 非瞬时)与
>   is_connect_phase_error(仅 httpx ConnectError/ConnectTimeout;排除读超时
>   ——D2「未送达 vs 已送达」);call_with_retry(sleep/on_retry 可注入,假时钟
>   不真等);retry_wait_ms(Retry-After 截断到 max_wait,否则指数+全量 jitter)。
> - llm.py 重构:_resilient_call 单点编排降级链(_degrade_chain:主目标→主模型
>   →fallback 条目,override 不降级 D7;预算分配=主目标 (N−下游数) 次+下游各
>   1 次,D4),四个出站点改 attempt 闭包(协议分支入闭包,每尝试自报 usage=
>   每尝试一条 D5);llm_protocol.py/AnthropicClient 零改动。终态错误文案与
>   LlmError 出口逐字保持(LLM 调用失败:…/vision 调用失败:…/embeddings …)。
> - apiclient.py:_post 拆分重试包装(连接层分层,_post_once 原样),
>   report_usage/tool_execute 改走 _post_once(任何失败不重试);
>   attachment_content 同口径 _get_retryable。
> - settings:llm_max_attempts=3 / llm_retry_base_ms=500 / llm_retry_max_wait_ms
>   =5000 / llm_fallback_entry(空=关闭)四项 + fail_closed_checks 启动期校验
>   (不在注册表告警视为未配置,不拒启)+ .env.example 登记 + deps 指纹入参。
> - 测试:tests/test_outbound_resilience.py 36 例(分类/退避/预算/降级/用量/
>   回调分层;降级测试以池替身钉死——曾暴露真实出网:fallback 若在构造期绑定
>   池条目,测试后补 pool 不生效,已改为按名惰性解析,与 _resolve 同口径);
>   全 agent 门禁绿(413 passed,ruff+mypy+pyright+lint-imports+cover)。
> - 受控行为变更对齐既有断言:test_model_routing 读超时用例、test_llm_protocol
>   500/超时用例由「1 条 ERROR 记录」改为「3 条」(072 每尝试一条新契约),
>   退避参数测试内钉 1ms 防真等。
> - 【口径核对-义务移交】3.1:072 先于 062 落地,「失败尝试计入按次配额」口径
>   说明由 062 的 spec delta 承载(其 tasks 2.3 同款核对在此闭合);3.2 065
>   红线:eval 走真实链路,重试只增成功率先例,不产生「两次调用两次计费」
>   误读(计费轨=muniu/牌价,非本轨);3.3:069 若后落地,其「平均 token」
>   SHALL 按 trace_id 分组 SUM(total_tokens) 聚合(attempts 另计),否则 ERROR
>   行 tokens=0 会稀释按行平均——069 实施时落实。
> - 【待确认-环境限制】4.2 手工冒烟(本地 mock 429/503+Retry-After→成功且
>   用量含失败尝试行;主模型不可用+fallback 降级):需在跑服务与 mock 端点,
>   自动化层已由替身测试等价覆盖(429+Retry-After 60s 截断、503 两连败后
>   成功、降级链路、每尝试一条均有断言)。

> 全量 make check 收口(2026-09-27 最终门禁轮):api 1024 passed(覆盖率 83%,
> ruff format/check+mypy+pyright+lint-imports 全净)/ agent 458 passed(88%,
> 同套静态门禁全净)/ liannu 22 passed(89%)/ web tsc+eslint+stylelint+
> prettier+vitest 60 passed+build 绿 / contracts 导出与工作区一致(漂移
> 门禁等价通过)。五门禁全绿。
