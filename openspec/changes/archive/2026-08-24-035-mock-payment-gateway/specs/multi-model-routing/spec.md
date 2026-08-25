## MODIFIED Requirements

### Requirement: 用量采集与统计

每次 LLM 调用(chat/chat_with_tools/embeddings)SHALL 采集:trace_id(可空)、模型名、任务类型、prompt/completion/total tokens。**采集通道分两类**:bot 运行链路(/internal/bot-run)SHALL 由 Agent 面按请求收集用量明细(带序号)并随响应回传,控制面 MUST 在响应返回调用方前同步落库(带 bot_id 归属,tenant 取 api-key 归属;(trace_id, 序号)唯一约束兜底重试双写,冲突静默跳过);其余链路(chat 问答/recipe/titler 等)SHALL 维持 Agent 面内部回调上报(fire-and-forget,上报失败不阻断主链路,仅日志),控制面 SHALL 落 sys_llm_usage。系统 SHALL 提供用量聚合查询端点(按租户+模型+任务+时间维度聚合 tokens 与调用次数),权限点 system:llm-usage:view(仅 SUPER_ADMIN 种子覆盖)。

#### Scenario: 问答全链用量落库

- GIVEN 一轮正常问答(多次 LLM 调用)
- WHEN 链路完成
- THEN sys_llm_usage 落对应多条记录(每次调用一条,含模型/任务/tokens/trace_id)

#### Scenario: bot 链路请求内同步落库

- GIVEN 一次 bot 运行(v1 网关/嵌入页/商店/MCP 渠道)
- WHEN 控制面收到 Agent 面响应
- THEN 响应内携带的用量明细同步批量落库(含 bot_id),重试冲突按
  唯一约束静默去重

#### Scenario: 上报失败不阻断

- GIVEN 控制面不可达
- WHEN Agent 面上报用量(fire-and-forget 通道)
- THEN 主链路响应不受影响,失败仅日志

#### Scenario: 聚合查询

- GIVEN SUPER_ADMIN 已登录且库内有用量记录
- WHEN 按时间范围查询用量聚合
- THEN 返回按租户+模型+任务分组的 tokens 合计与调用次数

### Requirement: 用量采集计费口径

sys_llm_usage(服务端采集,不经客户端)SHALL 作为配额与计费的用量
事实源;bot 渠道 MUST 以请求内同步入账为准(响应返回时本次用量
已可判定,偏低估窗口收窄为落库失败残余,失败仅日志不阻断应答);
非 bot 渠道维持 fire-and-forget 宽松语义(偏低估)在文档明示。

#### Scenario: 计费以服务端采集为准

- GIVEN 配额判定或账单生成
- WHEN 聚合用量
- THEN 仅取 sys_llm_usage(无客户端上报通道)

#### Scenario: bot 渠道计费基准

- GIVEN bot 渠道一次调用完成
- WHEN 随后的配额判定或出账聚合
- THEN 本次用量已计入(请求内入账)
