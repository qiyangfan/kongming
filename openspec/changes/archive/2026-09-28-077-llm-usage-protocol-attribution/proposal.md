# 077-llm-usage-protocol-attribution 提案:LLM 用量补落出站协议维度(多协议观测闭环)

## Why

多协议 LLM 出站已落地并归档(multi-protocol-llm,ADR-008:`openai` Chat Completions、
`anthropic` Messages 自研翻译、`responses` 三通道),但观测面仍停留在单协议时代口径:
`sys_llm_usage` 记录 model / task / tokens / latency_ms / status,而 `protocol`
取值域虽已由 multi-model-routing spec「多模型注册表」定义(`apps/agent/kongming_agent/
settings.py:18` ADR-008 取值域单源),**只存在于 agent 进程内存,不落库、不可筛、
不可聚合**。多协议能力的观测闭环缺最后一环。

三个已代码核实的咬人场景:

1. **同名模型跨协议条目混同**:注册表允许多条目(model 名可同、protocol 不同,
   如同一个 `qwen-plus` 一条走 DashScope `chat/completions`、一条走某网关
   `responses`)。汇总与明细中两股流量混为同一 model 行,运营者按通道/供应商
   核对流量时无法拆分——分不开就无从对账。
2. **协议级故障归因**:`anthropic` 通道走后端自研翻译层(`llm_protocol`),
   翻译层缺陷(畸形应答、参数不兼容、流式协议差异)会集中表现为 status=ERROR
   或空应答。用量面不按协议切分时,定位「是哪个通道在报错」只能逐条翻 agent 日志。
3. **多协议能力验收与持续复盘**:三个通道的流量占比与健康分布,今天没有任何
   管理面视图;新增第四个协议时必须改代码才能观测。

标准做法参照:OTel `gen_ai` 语义约定与 Langfuse 等观测后端均把 provider/protocol
作为生成调用的必要归因属性,与 model、tokens、status 并列而非可选项;参考项目
ai-chatbot 的轮次日志(`modelRuns` nested 元素)同样把 provider 与 stepCode/
modelKey/modelName 并列为其观测底座——那边把它当必要字段,这边把它丢在进程内。

**与在途变更的边界(先划清,防评审时混为一谈)**:073-prompt-fingerprint 管的是
prompt **内容版本**维度(重估条件未触发,不动);075-model-registry-pricing-removal
删计价字段并立起「三条成本口径不解耦、不造第三轨」的纪律——本变更 SHALL NOT
把 protocol 接成成本估算轨,**归因不等于计价**;072-agent-outbound-resilience
管出站**调度韧性**(重试/降级判定),本变更只把既成事实落得可见,不碰调度。

## What Changes

1. **schema**:新迁移 `apps/api/alembic/versions/0032_llm_usage_protocol.py`:
   `sys_llm_usage` 增 `protocol VARCHAR(16) NULL`。取值域对齐注册表单源
   (`openai | anthropic | responses`,ADR-008),不新造枚举。**存量行 null 不回算**
   (model 名不可可靠映射协议,DashScope 代理同名模型是常态)。
2. **agent 面**:`llm.py` `_report` 增 `protocol` keyword 参数,由出站客户端实例
   按其绑定的注册表条目取值透传(openai/anthropic/responses 三通道各自的调用点
   各带自身协议;缺省 openai 与 `DEFAULT_PROTOCOL` 语义一致);内部上报 DTO
   同步增字段(agent↔api 内部契约,不进 OpenAPI)。
3. **api 面**:`/internal/llm-usage` 上报入口 `UsageRecordIn` 增 protocol 并落库;
   明细分页 `/system/llm-usage/page` 增 protocol 可选筛选;汇总
   `/system/llm-usage/summary` 增 protocol 可选筛选(**过滤型,不新增分组键**)。
   权限点不变(仍 `system:llm-usage:view`)。
4. **前端**:用量明细页增 protocol 列与筛选(类型由 `pnpm contracts:gen` 生成)。
5. **契约**:page/summary 筛选参数进 OpenAPI → `make contracts` 提交产物(ADR-005)。

## Non-goals

- **协议级成本估算 / 第三口径轨**:成本归 055 的牌价轨与 muniu 轨两轨,
  protocol 只做归因不做计价(075 纪律,不重犯)。
- **协议健康探测 / 熔断 / 自动降级**:072-agent-outbound-resilience 领地——
  韧性判定属出站调度层,本变更只保证结果可见。
- **从历史行反算 protocol**:model 名映射协议不可靠(代理同名模型是常态),
  存量 null 一律展示「-」,不回算、不推断。
- **protocol 进用户面 / SSE**:纯管理观测面,「内部诊断信号不进用户响应」
  与 069「四信号」同款口径。
- **按 protocol 新增汇总分组维度改返回结构**:过滤型筛选已覆盖全部三个
  真实场景;分组维度扩展留待真实聚合诉求出现(不为观测而观测)。

## Capabilities

### Modified Capabilities

- observability:新增「用量协议归因」Requirement——usage 记录落 protocol 列
  (取值域三值、存量 null 不回算)、明细分页与汇总支持 protocol 过滤、
  明细分页返回含 protocol 列(null 展示口径);「观测面数据边界」不涉
  (协议维度与租户维度正交,仍按租户收敛)。

## Impact

- **迁移**:`apps/api/alembic/versions/0032_llm_usage_protocol.py`
  (sys_llm_usage 增列)。**迁移链协调**:069-chat-quality-observability 计划
  `0031_chat_quality_signals`(在途未落),两迁移以实际落地顺序串联,
  SHALL NOT 双头/双父——若 069 先落,本变更 down_revision 指 0031;
  若本变更先落,069 落地时将其 0031 挂到本迁移之后并由后落地方修头部。
- **agent**:`llm.py`(`_report` 签名与三通道调用点透传)、内部上报 DTO 构造点。
- **api**:`llmusage/router.py`(`UsageRecordIn`/summary/page 参数)、
  `llmusage/repository.py`(落库 SQL 与筛选 SQL)。
- **前端**:`apps/web/src/lib/llmusage-api.ts` 及用量页明细(筛选+列)。
- **契约**:`make contracts` 提交产物(ADR-005);CI 漂移门禁兜底。
- **ADR**:零触发。ADR-002 信任边界不变(仍进程内 env→内部上报→PG);
  ADR-004 既有表加列走新迁移(同仓约定);ADR-008 取值域单源复用不新造。
- **与在途变更关系**:073(若将来触发,同一张表加 prompt 版本列,字段正交、
  迁移链串联即可);075/080-prompt-assembly-budget(改 multi-model-routing
  注册表块,本变更只增消费不改定义,规格块不相交)。
