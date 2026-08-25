# multi-model-routing delta(038)

## MODIFIED Requirements

### Requirement: 用量采集与统计

每次 LLM 调用(chat/chat_with_tools/embeddings)SHALL 采集:trace_id(可空)、
模型名、任务类型、prompt/completion/total tokens、调用耗时(latency_ms,毫秒)
与调用成败(status,SUCCESS/ERROR)。**失败调用(超时/连接失败/供应商协议错)
SHALL 同样采集落库**(tokens 记 0,usage 缺失不因此丢弃记录)——失败率是路由
质量与供应商健康度的可观测基础。**采集通道分两类**:bot 运行链路
(/internal/bot-run)SHALL 由 Agent 面按请求收集用量明细(带序号)并随响应
回传,控制面 MUST 在响应返回调用方前同步落库(带 bot_id 归属,tenant 取
api-key 归属;(trace_id, 序号)唯一约束兜底重试双写,冲突静默跳过);其余
链路(chat 问答/recipe/titler 等)SHALL 维持 Agent 面内部回调上报
(fire-and-forget,上报失败不阻断主链路,仅日志),控制面 SHALL 落
sys_llm_usage。存量记录(本变更前)SHALL 视为 SUCCESS(历史上失败调用本就
不落记录,缺省语义成立)。聚合查询行为(汇总/趋势/明细)由 observability
域立约,权限点 system:llm-usage:view 不变。

#### Scenario: 问答全链用量落库

- GIVEN 一轮正常问答(多次 LLM 调用)
- WHEN 链路完成
- THEN sys_llm_usage 落对应多条记录(每次调用一条,含模型/任务/tokens/
  latency_ms/status/trace_id)

#### Scenario: 失败调用也落记录

- GIVEN 一次 LLM 调用因供应商超时或连接失败未返回内容
- WHEN 调用异常上抛
- THEN 该次调用仍以 status=ERROR、tokens=0、latency_ms=实际耗时落一条
  用量记录(采集调度本身失败除外——fire-and-forget 语义不变)

#### Scenario: bot 链路请求内同步落库

- GIVEN 一次 bot 运行(v1 网关/嵌入页/商店/MCP 渠道)
- WHEN 控制面收到 Agent 面响应
- THEN 响应内携带的用量明细同步批量落库(含 bot_id 与 latency_ms/status),
  重试冲突按唯一约束静默去重

#### Scenario: 上报失败不阻断

- GIVEN 控制面不可达
- WHEN Agent 面上报用量(fire-and-forget 通道)
- THEN 主链路响应不受影响,失败仅日志

#### Scenario: 聚合查询

- GIVEN SUPER_ADMIN 已登录且库内有用量记录
- WHEN 按时间范围查询用量聚合
- THEN 返回按租户+模型+任务分组的 tokens 合计、调用次数、失败次数与
  延迟统计(行为细则见 observability 域)
