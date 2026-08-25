## Purpose

让平台按任务特性选择模型(代码生成用强模型、轻量任务用快模型)并核算用量:多模型注册表(能力标签+计价)+ 任务级确定性路由 + 出口白名单 + 逐次调用用量采集落库;单模型部署与既有链路零回归。

## Requirements

### Requirement: 多模型注册表

系统 SHALL 支持经环境变量注册多个 LLM 模型:每个模型条目 MUST 含唯一名称、base_url、api_key(仅 Agent 面进程内存,不落库不落日志)、能力标签(code/reasoning/chat/vision 子集)、可选计价(每百万 token 单价)。注册表为空或未配置时 SHALL 回退既有单模型配置(llm_*),行为与引入前逐字一致。注册表条目畸形(缺字段/重名)SHALL 启动 fail-closed 拒配(告警并回退单模型,不裸崩)。

#### Scenario: 空注册表零回归

- GIVEN 未配置模型注册表
- WHEN Agent 面启动并处理问答
- THEN 全部 LLM 调用走既有 llm_* 单模型配置,行为与 021 之前一致

#### Scenario: 多模型注册生效

- GIVEN 注册表配置了 qwen-plus(主)与 deepseek-coder(code 标签)
- WHEN Agent 面启动
- THEN 两个模型可用,按路由表分发调用

#### Scenario: 畸形条目拒配

- GIVEN 注册表某条目缺 base_url
- WHEN Agent 面启动
- THEN 该条目被拒(告警),其余条目与主模型可用

### Requirement: 任务级路由策略

LLM 调用 SHALL 按任务类型路由:任务类型枚举(SQL_GEN/UNDERSTAND/SUMMARIZE/TITLER/DESCRIBE/REACT)经路由表(环境变量)映射模型名;未配置路由的任务 SHALL 回退主模型(既有 llm_model)。路由 SHALL 确定性(同配置同结果,可审计);既有调用方(不传任务类型)SHALL 走主模型零回归。embedding 调用 SHALL NOT 参与路由(独立配置语义不变,009 立约)。

#### Scenario: SQL 生成走指定模型

- GIVEN 路由表配置 SQL_GEN → deepseek-coder
- WHEN 问答链路执行 generate 节点
- THEN 该次 LLM 调用发往 deepseek-coder 的 base_url 与模型名

#### Scenario: 未配置任务回退主模型

- GIVEN 路由表只配置了 SQL_GEN
- WHEN summarize 节点执行
- THEN 该次调用走主模型

#### Scenario: 既有调用零改动

- GIVEN 未传任务类型的调用(如标题改写既有代码)
- WHEN 执行
- THEN 走主模型,行为与 021 之前一致

### Requirement: 供应商出口白名单

注册表模型的 base_url 主机名 SHALL 在白名单(环境变量配置,缺省含主模型供应商主机)内;不在白名单的条目 SHALL 拒配(告警跳过)。主模型 llm_base_url 不在白名单时 SHALL 启动告警(不拒——既有配置兼容)。

#### Scenario: 白名单外条目拒配

- GIVEN 注册表条目 base_url 为白名单外主机
- WHEN Agent 面启动
- THEN 该条目被拒(告警),不进入可用模型集

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
