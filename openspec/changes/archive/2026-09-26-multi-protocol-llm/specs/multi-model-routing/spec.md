# Spec Delta

## MODIFIED Requirements

### Requirement: 多模型注册表

系统 SHALL 支持经环境变量注册多个 LLM 模型:每个模型条目 MUST 含唯一名称、base_url、api_key(仅 Agent 面进程内存,不落库不落日志)、能力标签(code/reasoning/chat/vision 子集)、可选计价(每百万 token 单价);并 SHALL 支持可选 `protocol` 字段声明出站协议,取值域 `openai`(OpenAI Chat Completions,缺省)| `anthropic`(Anthropic Messages)| `responses`(OpenAI Responses)。注册表为空或未配置时 SHALL 回退既有单模型配置(llm_*),行为与引入前逐字一致。注册表条目畸形(缺字段/重名/protocol 非法)SHALL 启动 fail-closed 拒配(告警并剔除该条目,不裸崩;主模型 protocol 非法 SHALL 启动告警并回退 openai,不拒启)。主模型 SHALL 经环境变量声明协议(缺省 openai),语义与条目字段一致。

#### Scenario: 空注册表零回归

- **GIVEN** 未配置模型注册表
- **WHEN** Agent 面启动并处理问答
- **THEN** 全部 LLM 调用走既有 llm_* 单模型配置,行为与 021 之前一致

#### Scenario: 多模型注册生效

- **GIVEN** 注册表配置了 qwen-plus(主)与 deepseek-coder(code 标签)
- **WHEN** Agent 面启动
- **THEN** 两个模型可用,按路由表分发调用

#### Scenario: 畸形条目拒配

- **GIVEN** 注册表某条目缺 base_url
- **WHEN** Agent 面启动
- **THEN** 该条目被拒(告警),其余条目与主模型可用

#### Scenario: 缺省 openai 零回归

- **GIVEN** 注册表条目未声明 protocol(或显式 openai)
- **WHEN** Agent 面启动并处理问答
- **THEN** 该条目出站行为与引入前逐字一致(Chat Completions 通道)

#### Scenario: 畸形 protocol 单条目剔除

- **GIVEN** 注册表某条目 protocol 值不在取值域内
- **WHEN** Agent 面启动
- **THEN** 该条目被剔除(告警),其余条目与主模型可用(不裸崩)

## ADDED Requirements

### Requirement: 多协议出站适配

LLM 出站 SHALL 按条目(或主模型)声明的协议适配三条通道:文本问答(chat)、视觉(vision)、工具调用(chat_with_tools,含 ReAct 消息史往返翻译);embedding 通道 SHALL NOT 参与协议适配(恒 OpenAI 兼容 embeddings API,009 立约)。协议适配 MUST 保持外部可观测语义不变:调用方(LLM 客户端之上)继续以 OpenAI 形态消息史与工具调用结构交互,工具调用参数 JSON 畸形仍收敛为「参数畸形」观察喂回自纠(019 口径);用量 SHALL 归一为 prompt/completion/total tokens 走既有采集通道(anthropic 的 input/output tokens、responses 的 input/output tokens 均按此映射,total 为求和),失败调用(非 2xx/应答畸形)SHALL 维持既有 ERROR 采集语义。anthropic 协议出站 MUST 同时携带 `x-api-key` 与 `Authorization: Bearer` 双鉴权头及 anthropic-version 头(官方端点与国内厂商分支双兼容),并以 Claude Code 同构方式由 base_url 组装请求路径(去尾斜杠 + /v1/messages)。anthropic 协议 MUST 支持必填的输出上限参数(未显式配置时使用有文档的缺省值,不裸传空值)。

#### Scenario: anthropic 条目直连工具调用闭环

- **GIVEN** 注册表条目 protocol=anthropic 且 base_url 指向某厂商 Anthropic 兼容分支
- **WHEN** ReAct 链路经该条目执行(产生工具调用 → 观察 → 最终作答)
- **THEN** 出站请求为 Messages 协议形态(系统提示置顶、历史 assistant 工具调用与工具观察翻译为协议块、双鉴权头在场),工具调用结果回流后链路正常收敛最终答案

#### Scenario: responses 条目直连

- **GIVEN** 注册表条目 protocol=responses
- **WHEN** chat_with_tools 经该条目调用
- **THEN** 出站走该端点的 Responses 通道(工具声明与历史按 Responses 形态翻译),应答的文本/工具调用/用量与 openai 条目同构回传

#### Scenario: 视觉通道协议翻译

- **GIVEN** vision 路由命中 protocol=anthropic 条目且请求携带 base64 图片
- **WHEN** chat_vision 执行
- **THEN** 图片以 Anthropic 协议的 base64 图像块出站,文本应答正常返回

#### Scenario: anthropic 用量归一采集

- **GIVEN** anthropic 条目应答返回 input_tokens/output_tokens 用量
- **WHEN** 该次调用完成
- **THEN** 采集记录 prompt_tokens=input、completion_tokens=output、total_tokens=两者之和,与既有通道同表同语义

#### Scenario: embedding 不参与协议适配

- **GIVEN** 任务路由或主模型为 protocol=anthropic 条目
- **WHEN** embeddings 通道调用
- **THEN** 仍走既有 OpenAI 兼容 embedding 配置(009 回退链不变),不经 Messages 协议出站
