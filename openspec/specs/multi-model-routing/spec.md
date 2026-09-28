## Purpose

让平台按任务特性选择模型(代码生成用强模型、轻量任务用快模型)并核算用量:多模型注册表(能力标签+计价)+ 任务级确定性路由 + 出口白名单 + 逐次调用用量采集落库;单模型部署与既有链路零回归。

## Requirements

### Requirement: 多模型注册表

系统 SHALL 支持经环境变量注册多个 LLM 模型:每个模型条目 MUST 含唯一名称、base_url、api_key(仅 Agent 面进程内存,不落库不落日志)、能力标签(code/reasoning/chat/vision 子集);并 SHALL 支持可选 `protocol` 字段声明出站协议,取值域 `openai`(OpenAI Chat Completions,缺省)| `anthropic`(Anthropic Messages)| `responses`(OpenAI Responses)。注册表为空或未配置时 SHALL 回退既有单模型配置(llm_*),行为与引入前一致。注册表条目畸形(缺字段/重名/protocol 非法)SHALL 启动 fail-closed 拒配(告警并剔除该条目,不裸崩;主模型 protocol 非法 SHALL 启动告警并回退 openai,不拒启)。主模型 SHALL 经环境变量声明协议(缺省 openai),语义与条目字段一致。条目中的未知键 SHALL 被忽略,不因未知键拒配。

**上下文窗口(080)**:条目 SHALL 支持可选 `context_window` 声明该模型的上下文窗口规模,作为生成提示词装配预算的输入;未声明时 SHALL 回退全局缺省窗口值(可配)。该字段 SHALL NOT 出现在任何对外响应或管理面输出中(与 api_key 同为进程内配置),SHALL NOT 影响出站协议与路由判定语义。窗口值非正数等畸形形态 SHALL 按"未声明"处理并告警,SHALL NOT 拒配整条目(窗口只用于预算收敛,不构成安全边界)。

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

#### Scenario: 未知键不拒配

- GIVEN 注册表条目携带契约外的额外键(如历史遗留的计价字段)
- WHEN Agent 面启动
- THEN 该条目正常生效,额外键被忽略,不告警不拒配

#### Scenario: 缺省 openai 零回归

- GIVEN 注册表条目未声明 protocol(或显式 openai)
- WHEN Agent 面启动并处理问答
- THEN 该条目出站行为与引入前一致(Chat Completions 通道)

#### Scenario: 畸形 protocol 单条目剔除

- GIVEN 注册表条目 protocol 值不在取值域内
- WHEN Agent 面启动
- THEN 该条目被剔除(告警),其余条目与主模型可用(不裸崩)

#### Scenario: 窗口缺省回退

- GIVEN 注册表条目未声明 `context_window`
- WHEN 生成环节做装配预算
- THEN 按全局缺省窗口计算上界,不因缺字段而报错或跳过预算

#### Scenario: 畸形窗口不拒配

- GIVEN 某条目 `context_window` 为 0 或非数字
- WHEN Agent 面启动
- THEN 该条目仍可用(窗口按缺省处理并告警),SHALL NOT 被整条剔除

#### Scenario: 窗口不外泄

- GIVEN 已配置带窗口的注册表
- WHEN 查询用量或任何对外端点
- THEN 响应不含 `context_window` 与模型侧配置

> 备注(并行变更协调):本块为 075-model-registry-pricing-removal(删计价)
> 与 080-prompt-assembly-budget(增上下文窗口)对「多模型注册表」的
> **合并终态**。openspec MODIFIED 为整块替换,两变更的本块文本刻意保持
> 逐字一致——无论谁后 sync 进 `specs/multi-model-routing/`,结果都是
> 「无计价 + 有 context_window」,不会互相覆盖丢失。

### Requirement: 任务级路由策略

LLM 调用 SHALL 按任务类型路由:任务类型枚举(SQL_GEN/UNDERSTAND/SUMMARIZE/TITLER/DESCRIBE/REACT)经路由表(环境变量)映射模型名;未配置路由的任务 SHALL 回退主模型(既有 llm_model)。路由 SHALL 确定性(同配置同结果,可审计);既有调用方(不传任务类型)SHALL 走主模型零回归。embedding 调用 SHALL NOT 参与路由(独立配置语义不变,009 立约)。

运行期降级与重试 SHALL NOT 篡改路由表、注册表或任何持久状态:同配置的后续请求 SHALL 仍按同一路由表解析目标;降级只作用于当次调用的尝试序列,且当次实际使用的模型 SHALL 在用量记录中可判别。

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

#### Scenario: 降级不改写路由状态

- GIVEN SQL_GEN 路由到 deepseek-coder,且该模型对某次调用连续瞬时失败
- WHEN 该次调用降级至主模型完成
- THEN 后续 SQL_GEN 调用仍解析到 deepseek-coder
- AND 降级仅体现在该次用量的模型字段中

### Requirement: 供应商出口白名单

注册表模型的 base_url 主机名 SHALL 在白名单(环境变量配置,缺省含主模型供应商主机)内;不在白名单的条目 SHALL 拒配(告警跳过)。主模型 llm_base_url 不在白名单时 SHALL 启动告警(不拒——既有配置兼容)。

#### Scenario: 白名单外条目拒配

- GIVEN 注册表条目 base_url 为白名单外主机
- WHEN Agent 面启动
- THEN 该条目被拒(告警),不进入可用模型集

### Requirement: 用量采集与统计

每次 LLM 调用(chat/chat_vision/chat_with_tools/embeddings)SHALL 采集:trace_id(可空)、
模型名、任务类型、prompt/completion/total tokens、调用耗时(latency_ms,毫秒)
与调用成败(status,SUCCESS/ERROR)。**失败调用(超时/连接失败/供应商协议错
——含应答畸形如空 choices)SHALL 同样采集落库**(tokens 记 0,usage 缺失不
因此丢弃记录;畸形应答已回 usage 的保留实测值)——失败率是路由
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

### Requirement: 多协议出站适配

LLM 出站 SHALL 按条目(或主模型)声明的协议适配三条通道:文本问答(chat)、视觉(vision)、工具调用(chat_with_tools,含 ReAct 消息史往返翻译);embedding 通道 SHALL NOT 参与协议适配(恒 OpenAI 兼容 embeddings API,009 立约)。协议适配 MUST 保持外部可观测语义不变:调用方(LLM 客户端之上)继续以 OpenAI 形态消息史与工具调用结构交互,工具调用参数 JSON 畸形仍收敛为「参数畸形」观察喂回自纠(019 口径);用量 SHALL 归一为 prompt/completion/total tokens 走既有采集通道(anthropic 的 input/output tokens、responses 的 input/output tokens 均按此映射,total 为求和),失败调用(非 2xx/应答畸形)SHALL 维持既有 ERROR 采集语义。anthropic 协议出站 MUST 同时携带 `x-api-key` 与 `Authorization: Bearer` 双鉴权头及 anthropic-version 头(官方端点与国内厂商分支双兼容),并以 Claude Code 同构方式由 base_url 组装请求路径(去尾斜杠 + /v1/messages)。anthropic 协议 MUST 支持必填的输出上限参数(未显式配置时使用有文档的缺省值,不裸传空值)。

#### Scenario: anthropic 条目直连工具调用闭环

- GIVEN 注册表条目 protocol=anthropic 且 base_url 指向某厂商 Anthropic 兼容分支
- WHEN ReAct 链路经该条目执行(产生工具调用 → 观察 → 最终作答)
- THEN 出站请求为 Messages 协议形态(系统提示置顶、历史 assistant 工具调用与工具观察翻译为协议块、双鉴权头在场),工具调用结果回流后链路正常收敛最终答案

#### Scenario: responses 条目直连

- GIVEN 注册表条目 protocol=responses
- WHEN chat_with_tools 经该条目调用
- THEN 出站走该端点的 Responses 通道(工具声明与历史按 Responses 形态翻译),应答的文本/工具调用/用量与 openai 条目同构回传

#### Scenario: 视觉通道协议翻译

- GIVEN vision 路由命中 protocol=anthropic 条目且请求携带 base64 图片
- WHEN chat_vision 执行
- THEN 图片以 Anthropic 协议的 base64 图像块出站,文本应答正常返回

#### Scenario: anthropic 用量归一采集

- GIVEN anthropic 条目应答返回 input_tokens/output_tokens 用量
- WHEN 该次调用完成
- THEN 采集记录 prompt_tokens=input、completion_tokens=output、total_tokens=两者之和,与既有通道同表同语义

#### Scenario: embedding 不参与协议适配

- GIVEN 任务路由或主模型为 protocol=anthropic 条目
- WHEN embeddings 通道调用
- THEN 仍走既有 OpenAI 兼容 embedding 配置(009 回退链不变),不经 Messages 协议出站

### Requirement: 出站调用韧性

agent 面出站调用(LLM 供应商调用与控制面回调)SHALL 对瞬时类供应商故障自动恢复,并对非瞬时类故障立即失败。规则如下:

1. **瞬时故障重试**:连接建立失败、读超时、HTTP 408/429/5xx 及协议层等价错误 SHALL 触发同一调用点重发。重试次数、退避基线、退避上限 SHALL 可配置,缺省开启。退避 SHALL 指数增长并叠加随机抖动;供应商 `Retry-After` SHALL 被尊重但受退避上限截断,以防长等待穿透上层请求时限。
2. **非瞬时故障不重试**:HTTP 4xx(408/429 除外)与配置类错误(缺凭证、模型不存在、协议非法)SHALL NOT 重试——重试只放大成本并掩盖配置错误。
3. **尝试预算**:单次业务调用的尝试总数 SHALL 有硬上限(可配,缺省 3);最后一次尝试 SHALL 可为降级目标,且降级目标本身 SHALL NOT 再触发重试,使最坏耗时受单轮问答总体时限约束。
4. **单层降级链**:任务路由目标瞬时故障耗尽 SHALL 降级至主模型;主模型也耗尽且显式配置了降级条目 SHALL 降级至该条目;否则失败。Bot 模型覆盖(`model_override`)SHALL NOT 参与降级——显式选择的模型与计价不容被静默替换。
5. **用量每尝试一条**:每次尝试 SHALL 各落一条用量记录(实际模型名、成败、耗时),SHALL NOT 合并为单条。失败尝试计入按次配额是既有口径的延续,非本需求引入的偏差。
6. **回调分层**:控制面回调 SHALL 按可重试性分层——元数据/召回/附件类只读回调与「请求未送达」类失败 SHALL 可重试;用量上报 SHALL NOT 重试(重复落库);工具执行 SHALL NOT 重试(工具副作用不可假定幂等)。SQL 执行回调仅对连接层未送达失败重试:读超时 SHALL NOT 重试(请求已送达且可能已执行)。

#### Scenario: 供应商限流后按 Retry-After 恢复

- GIVEN 供应商对首次调用返回 429 且带 `Retry-After`
- WHEN agent 重试
- THEN 按 `Retry-After`(受退避上限截断)等待后重发
- AND 调用成功时用户侧无失败感知,用量记录最终成功态

#### Scenario: 鉴权失败不重试

- GIVEN 供应商返回 401(凭证错误)
- WHEN 该次 LLM 调用执行
- THEN 只发生一次出站调用,直接失败并按既有 ERROR 口径落用量

#### Scenario: 路由目标故障降级主模型

- GIVEN SQL_GEN 路由到 deepseek-coder,该供应商连续瞬时失败
- WHEN 重试预算耗尽
- THEN 该次调用由主模型完成,用量记录的实际模型为主模型
- AND 后续 SQL_GEN 调用仍解析到 deepseek-coder

#### Scenario: Bot 显式模型不降级

- GIVEN 某 Bot 配置 `model_override` 指向注册表某条目
- WHEN 该条目连续瞬时失败
- THEN 不降级到主模型,该轮以失败收口

#### Scenario: 执行回调读超时不重试

- GIVEN agent 调用控制面执行 SQL,请求已送达但读超时
- WHEN 判定重试资格
- THEN 不重发(执行可能已发生,重复执行不可接受)

#### Scenario: 用量上报失败不重试

- GIVEN 用量上报请求失败
- WHEN 上报逻辑执行
- THEN 不重发,仅按既有 fire-and-forget 口径记录日志

#### Scenario: 尝试预算硬约束

- GIVEN 任何失败序列(连续瞬时故障 + 触发降级)
- WHEN 单次业务调用执行
- THEN 出站尝试总数不超过配置上限,最坏耗时不超过单轮问答总体时限
