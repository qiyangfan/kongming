# 077-llm-usage-protocol-attribution 实施任务

## 1. 测试先行

- [x] 1.1 失败测试:agent→api 内部上报携带 protocol,三通道(openai/anthropic/
      responses)各自透传,落库后 protocol 列值为对应通道;未携带时落 null 不报错;
      先跑红
- [x] 1.2 失败测试:明细分页按 protocol 筛选只返回该协议行;同名 model 两条目
      流量可拆分;返回行含 protocol 列(null 行不炸);先跑红
- [x] 1.3 失败测试:汇总按 protocol 过滤后 tokens/calls 计数只含该协议;
      不过滤时与现状逐字一致(零回归);无 system:llm-usage:view 时 403;先跑红

## 2. 实现(agent 面 + api 面 + 迁移)

- [x] 2.1 迁移 `0032_llm_usage_protocol.py`:sys_llm_usage 增
      `protocol VARCHAR(16) NULL`;存量不回算;down_revision 按实际落地顺序
      与 069 的 0031 串联(不得双头)
      **验证**:`make api-check` 绿;迁移 up/down 往返
- [x] 2.2 `llm.py`:`_report` 增 protocol 参数,三通道调用点各透传自身协议
      (缺省 openai,与 DEFAULT_PROTOCOL 语义一致);内部上报 DTO 增字段
      **验证**:1.1 转绿
- [x] 2.3 `llmusage/repository.py`:落库 SQL 含 protocol;明细/汇总筛选支持
      protocol;`llmusage/router.py`:`UsageRecordIn` 与 summary/page 参数增
      protocol(可选,空=不过滤)
      **验证**:1.2、1.3 转绿;`make api-check` 绿

## 3. 契约与前端

- [x] 3.1 `make contracts` 提交产物(ADR-005);CI 漂移门禁兜底
      **验证**:contracts/openapi.json diff 含新筛选参数与 protocol 列
- [x] 3.2 前端用量明细页:protocol 列 + 筛选(类型由生成产物提供,禁手写)
      **验证**:`pnpm typecheck && pnpm lint` 绿

## 4. 收口

- [x] 4.1 本地构造注册表两条目(model 同名、protocol 不同)各发一次调用,
      验证 protocol 归因各走各的端点
      **实测(2026-09-28,agent 容器内本地 mock 端点,零真实上游成本)**:
      注册表构造 same-model-openai(protocol=openai)与
      same-model-anthropic(protocol=anthropic),两条目 model 同名
      (same-model);任务路由 TASK_OA→openai 条目、TASK_AN→anthropic
      条目;出口白名单放行 127.0.0.1。各发一次调用:openai 条目命中
      mock 的 POST /v1/chat/completions(报文键 messages/model/
      temperature,返回 OK-OPENAI);anthropic 条目命中
      POST /v1/messages(报文键 system/max_tokens/messages/model,
      返回 OK-ANTHROPIC)。协议分派按条目而非模型名——同名不同协议
      的两条目不互相串路;协议字段经 077 的 _report(protocol=…)
      逐点接线,单测另有字段钉集。
      明细按 protocol 筛出两股流量;ERROR 行同样可见协议归属;记录于 apply
- [x] 4.2 全量 `make check` 收口

> 077 落地记录(2026-09-27):
> - 迁移 0033_llm_usage_protocol(落地序接 069 的 0032 之后,串联无双头):
>   sys_llm_usage 增 protocol VARCHAR(16) NULL;存量 null 不回算。
> - agent 面:llm.py _report 增 protocol keyword(缺省 openai 与
>   DEFAULT_PROTOCOL 语义一致);四个出站点 + 072 重试循环的每尝试报告
>   各带自身协议——openai 通道四站点显式 PROTOCOL_OPENAI,anthropic/
>   responses 通道(_chat_via_protocol / chat_with_tools 协议分支)透传当
>   次目标 protocol,embed 恒 openai;072 的用法每尝试一条天然带协议。
> - api 面:UsageRecordIn 纯增 protocol(缺省 None=旧版 agent 零回归);
>   落库 SQL 含 protocol(含 035 bot 收集器通道同步透传);明细分页增
>   protocol 过滤 + 行含 protocol 列;汇总增 protocol 过滤型筛选(不新增
>   分组键,不过滤逐字一致)。
> - 前端:用量明细页增协议列(null 展示「-」)+ 协议筛选 Select
>   (openai/anthropic/responses 三值);类型由生成产物提供。
> - 契约:make contracts 重新生成(page/summary 筛选参数 + protocol 列);
>   contracts-check 零漂移;前端 typecheck/lint/format/test 全绿。
> - 测试:tests/test_llm_usage.py 增 3 例(协议落库+旧版 null/明细分页
>   协议筛选拆分同名条目/汇总协议过滤+403);受控行为变更对齐 agent 侧
>   字段钉集(test_model_routing/test_usage_collector 三处全键钉)。
>   agent 441 passed / api 985 passed(含 062/069 增量)。
> - 4.1 本地构造注册表两条目各发一次调用:需真实端点冒烟(环境限制),
>   协议透传已由替身级测试覆盖(三通道 _report 带 protocol 的单测;
>   anthropic/responses 通道透传路径见llm_protocol 既有用例零回归)。
> - 边界复核:protocol 未接入成本估算轨(075 纪律)——只做归因;本变更
>   未改 prompt 指纹(073)与装配预算(080)的字段,互不相交。
