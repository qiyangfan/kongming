# ADR-008: agent 面 LLM 多协议出站(openai / anthropic / responses)

日期: 2026-09-26

状态: 已接受(实施载体:openspec 变更 `multi-protocol-llm`)

## 背景

agent 面 LLM 客户端是 OpenAI Chat Completions 单协议(`AsyncOpenAI`):国内主流厂商(DeepSeek / Kimi / GLM / 千问 / Doubao / MiniMax / StepFun / 千帆 / 混元 / LongCat / MiMo 等)普遍提供的官方 **Anthropic Messages 兼容分支**(订阅制 Coding Plan 所在,价格远低于按量)与多家已原生的 **OpenAI Responses** 端点均无法直连,端点选型被协议锁死。

ADR-007 修订(2026-09-26)后官方直连为一等公民、muniu 降级为可选端点;端点预设目录(`docs/model-providers.md`)已收录各家分支端点口径。多协议直连是直连一等公民化的后半程。

## 决策

### 1. 协议枚举与配置面

出站协议 `protocol ∈ {openai, anthropic, responses}`,两级声明:

- **条目级**(注册表 JSON 可选字段,缺省 `openai`):畸形取值 = 单条目剔除告警(与缺字段/重名同级的 fail-closed 纪律,不裸崩);
- **主模型级**(`KM_AGENT_LLM_PROTOCOL`,缺省 `openai`):非法值启动告警并回退 openai,不拒启(与「主模型 base_url 不在白名单只告警不拒」的既有兼容口径一致)。

否决「每条目多协议候选列表」:回退链属路由语义,协议是端点属性,组合只会制造不可审计的出站。

### 2. 翻译层单点收敛,openai 路径零回归

新增 `kongming_agent/llm_protocol.py`,双职责:

1. **纯函数翻译**:canonical OpenAI 形态(ReAct 内核消息史 / function schema / 工具调用)↔ anthropic Messages / Responses 请求应答形态;
2. **anthropic SDK 客户端封装**(`AnthropicClient`:构造 / 双头鉴权 / 错误映射 → LlmError)。

`llm.py` 三通道(chat / chat_vision / chat_with_tools)在 `_resolve` 后按协议分支:openai 走既有代码路径(行为等价、可执行语句零语义改动,零回归硬判据);anthropic / responses 经翻译层收敛为 `ProtocolReply(content/tool_calls/usage)` 回到既有 `_report` 与返回结构。embed 恒 OpenAI 兼容(Anthropic 无 embeddings 语义,009)。否决「openai 也重构进适配器接口」:零回归优先,重构留给协议语义稳定后的独立变更。

### 3. anthropic 客户端 = 官方 `anthropic` SDK(AsyncAnthropic),双头鉴权

否决 httpx 手写(本变更设计初稿曾选 httpx,推翻):

- 与 openai 通道「官方 SDK」形态同构:同一模块一边 SDK 一边手写是两套哲学;
- Messages 协议在活跃演进,手写层会把「跟进供应商协议漂移」变成自己的长期维护面,SDK 把它外包给官方——**实施期即兑现**:anthropic SDK 1.8.0 的 `messages.create` 类型面已移除 `temperature`(协议演进为 `output_config.effort` 档位),官方 SDK 直接把该漂移吸收为类型契约;本仓裁决为 anthropic 出站**不发送 temperature**(可选参数,官方与分支端点均接受缺省),canonical 采样语义仅对 openai / responses 生效;
- SDK 纯 Python,传递依赖 httpx2/pydantic 均轻,边际成本低。

URL 语义:base_url 即 Claude Code 的 `ANTHROPIC_BASE_URL` 同构(SDK 请求 `{base}/v1/messages`)。鉴权:SDK 自发 `x-api-key` + `anthropic-version`,`default_headers` 补 `Authorization: Bearer` 双头(官方端点认 x-api-key,国内分支普遍认 Bearer,双发双兼容;若实证出现个别端点拒收双头,加条目级 `auth_header` 配置是纯增演进)。`max_tokens` 必填:未显式传时用常量缺省 8192(文档化行为)。

### 4. 翻译表(保真边界)

| canonical              | anthropic                                                                | responses                                               |
| ---------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------- |
| system 消息            | 顶层 `system`(不进消息史)                                                | `instructions`(不进 input)                              |
| `assistant.tool_calls` | `tool_use` 块(`input` = 解析后 args;畸形以 `{}` 回放,错误观察已在内核侧) | `function_call` 项(`arguments` 原文回放)                |
| `role:"tool"`          | user 消息内 `tool_result` 块(连续多条合并一条 user)                      | `function_call_output` 项(output = 观察原文)            |
| 工具声明               | `{name, description, input_schema}`                                      | 扁平 `{type:"function", name, description, parameters}` |
| vision data-url        | `image.source(base64)`                                                   | `input_image`(data-url 直传)                            |
| usage                  | `input/output_tokens` → prompt/completion,total 求和                     | `input/output/total_tokens` 同映射                      |

畸形 / 缺失 usage → 既有 ERROR / 0 采集语义(038);失败调用(非 2xx / 超时 / 应答畸形)统一收敛 LlmError 并落 ERROR 采集,不裸崩。

## 零回归边界

- 缺省(注册表条目无 `protocol` 字段 + 未设 `KM_AGENT_LLM_PROTOCOL`)时行为与引入前完全一致:池条目仍为 `AsyncOpenAI`,三通道走 openai 分支,`test_model_routing.py` / `test_react.py` / `test_usage_collector.py` 全绿实证;
- ReAct 内核、用量采集、路由、白名单、Bot 覆盖语义零改动;
- 零 API 契约变更(contracts/openapi.json 不动);零前端影响;零 schema 变更。

## 与既有决策的关系

- **ADR-007(修订)**:muniu 为可选端点之一,多协议直连后 agent 侧端点选型完全解耦;muniu 的密钥收敛 / 统一计量价值不变,选择权在部署侧。
- **021 出口白名单**:新协议条目同样受 `model_allowed_hosts` 约束,分支端点主机须放行。
- **038 失败采集**:三协议失败调用同表同语义(status/latency_ms),归一 usage 走既有 `_report` 通道。

## 后果

- 正面:端点选型与协议解耦,订阅制 Coding Plan 分支端点可直连降本;协议漂移维护面外包给官方 SDK;缺省零回归,采用 = 加一个字段。
- 负面:新增直依赖 `anthropic` SDK(纯 Python);responses 端点方言成熟度不一(翻译层只依赖公开契约核心面,真实接入冒烟时登记预设目录);双鉴权头对个别端点拒收的理论面(缓解:纯增配置演进)。
