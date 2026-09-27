# 072-agent-outbound-resilience 提案:agent 面出站调用韧性(瞬时重试 + 单层降级)

## Why

已逐行核实的当前状态:**agent 面两个出站点都是零重试**。

- LLM 出站(`apps/agent/kongming_agent/llm.py`)只有 `llm_timeout_sec=30` 硬超时,
  全文无 `retry`/`backoff`/`sleep`;四个出站点(`chat`/`chat_vision`/
  `chat_with_tools`/`embed`)同此。
- 控制面回调(`apiclient.py`)同样单次尝试,`api_timeout_sec=30`。
- 任何一次 `LlmError` 直接上抛,`_run_graph` 统一收敛为 `llm_unavailable`
  (`success=False`)——`graph/nodes.py:437` 的注释把这条路径写得明白。
  `validate`/`execute` 的错误有 selfheal(`MAX_RETRY=2`)兜底,
  **LLM 调用本身没有任何恢复手段**。

后果不是理论风险:一轮问答有 understand + retrieve + generate + summarize
(加 ≤2 次 selfheal)共 3–5 次 LLM 调用,每次都是独立失败点。供应商一次
429、一次 31 秒的长 prompt 超时、一次网络抖断,用户看到的就是
「服务暂不可用」,而退避 1 秒重发即可成功。单租户自建部署(compose 直连
dashscope 或 muniu,ADR-007)没有网关层吸收这类抖动,agent 是唯一的吸收点。

次要点:agent→api 的执行回调(`/internal/sql/execute`)单次失败会让本轮直接
失败,并诱导 selfheal 再烧一次 LLM 生成——最贵的失败路径。

这不是「锦上添花的重试框架」,而是**当前完全缺失的最小韧性**。

## What Changes

1. **瞬时故障重试**:同一调用点对瞬时类故障(连接建立失败、读超时、
   HTTP 408/429/5xx 及协议层等价错误)自动重发。尊重供应商 `Retry-After`,
   但受退避上限截断(防止长等待穿透上层 `agent_timeout_sec=120`)。
2. **尝试预算硬上限**:单次业务调用最多 3 次尝试(可配)。最后一次尝试
   **允许**是降级目标;降级目标本身不再重试。保证最坏耗时
   ≈ 2×30s + 退避 + 1×30s < 上层 120s 时限。
3. **单层降级链**:任务路由目标瞬时故障耗尽 → 主模型;主模型也耗尽且
   显式配置了降级条目(`KM_AGENT_LLM_FALLBACK_ENTRY`,缺省空=关闭)→ 该条目;
   否则失败。**Bot 模型覆盖(`model_override`)不参与降级**——Bot 创建者显式
   选定的模型与计价不容被静默替换。
4. **错误分类单点**:非瞬时故障(4xx 除 408/429、配置类错误)一律不重试;
   分类逻辑收敛为一个纯函数,四个出站点共用。
5. **控制面回调分层接入**:只读类回调(`sql_execute`/`fetch_schema`/
   `rag_retrieve`/`attachment_content`)在**连接层未送达失败**时可重试;
   `report_usage` 不重试(重复落库);`tool_execute` 不重试(工具副作用不可
   假定幂等)。
6. **用量每尝试一条**:每次尝试各落一条 `sys_llm_usage`(实际模型、成败、
   耗时),不合并——失败率与额外成本必须可见。这是 038 既有口径的延续。

## Non-goals

- **LLM API key 池与健康态**(参考项目 `PooledChatModel` + `RedisKeyPoolHealthStore`
  四件套):ADR-002 禁 agent import redis,健康态只能进程内、多副本必然分裂——
  那正是参考项目 fail-soft 退化后的形态;且单租户自建场景多 key 是伪需求。
  跨条目降级覆盖同一故障场景且零新概念。
- **agent 面并发上限/信号量**:062 Non-goals 已论断「属容量治理,与限流正交」;
  流式 API 上信号量造成头部阻塞,比快速失败更糟。429 交给退避,不交给自造限流器。
- **多级降级链/按权重路由**:两级以上只会把「哪次调用用了哪个模型」变成不可追责的
  黑箱;单层 + 用量留痕已是可审计的下限。
- **熔断器(circuit breaker)**:需要供应商故障的持续观测与半开探测;在单副本、
  单供应商的自建部署里,退避+降级已覆盖其收益,熔断只增加状态面。
- **第三方重试库(tenacity 等)**:需求是「两类错误 + 一个上限 + 一个退避函数」,
  自建约 60 行纯 asyncio;引库等于为一个 while 循环加依赖。
- **流式中断续传**:断线续存已由 query-streaming 域立约,不在本变更。
- **SQL 防火墙函数白名单化**(见 design D2):会让「执行无副作用」变成可证明命题,
  但那是 sqlfw 域自身的演进,本变更只按「未送达才重试」的保守规则规避。

## Capabilities

### Modified Capabilities

- multi-model-routing:任务级路由策略补「降级不篡改路由状态」约束;新增
  「出站调用韧性」Requirement(瞬时分类与重试、尝试预算、单层降级链、
  用量每尝试一条、回调分层重试五项)。

## Impact

- **落点**:`apps/agent/kongming_agent/llm.py`(新增重试原语 + 四个出站点接入)、
  `apiclient.py`(分层接入)、`settings.py`(4 个可配项)、`tests/`(新增韧性用例)。
- **契约**:无 OpenAPI 变更(agent 内行为,不新增响应字段)→ 无需 `make contracts`。
- **ADR**:不涉及。ADR-002 信任边界不变(仍零 DB/redis 凭证);ADR-008 多协议不变
  (重试包在 `client.create` 之外,协议翻译零改动)。
- **与在流变更的交互**:
  - **062(chat-cost-control)**:配额按次从 `sys_llm_usage` count 派生——
    重试会增加失败尝试行,配额随之上升。这是 038「失败调用也采集」的既有
    口径,非本变更引入的偏差;本变更在 design D5 记录该语义并写入 spec,
    避免 062 落地后被当成 bug。
  - **069-chat-quality-observability(并行提案)**:其质量汇总的「平均 token」
    走 `chat_message ⋈ sys_llm_usage on trace_id`。本变更使同一 trace_id 下
    可能出现 ERROR 行(tokens=0)与 SUCCESS 行并存,**按行平均会被 0 值稀释**;
    该处必须按 trace_id 分组取 `SUM(total_tokens)`。已列为 tasks 3.3 的
    接线核对项(两变更各自正确、合起来错误的典型缝)。
- **无 schema 变更**:不新增列(`seq`/`status`/`latency_ms` 已足够排序与判别)。
