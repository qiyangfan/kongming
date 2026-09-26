# Proposal

## Why

agent 面 LLM 客户端当前是 OpenAI Chat Completions 单协议(`AsyncOpenAI`):国内主流厂商(DeepSeek/Kimi/GLM/千问/Doubao/MiniMax/StepFun/千帆/混元/LongCat/MiMo 等)普遍提供的官方 **Anthropic Messages 兼容分支**(订阅制 Coding Plan 所在,价格远低于按量)与多家已原生的 **OpenAI Responses** 端点均无法直连,端点选型被协议锁死。配套裁决:ADR-007 已修订(2026-09-26)——muniu 降级为可选端点,官方直连成为一等公民,多协议直连是其后半程。

## What Changes

- 模型注册表条目(`MODEL_REGISTRY_JSON`)与主模型新增可选 `protocol` 字段:`openai`(缺省,Chat Completions)| `anthropic`(Messages)| `responses`(OpenAI Responses);畸形值按既有条目纪律 fail-closed 剔除告警。
- `KM_AGENT_LLM_PROTOCOL` 主模型协议环境变量(缺省 `openai`)。
- agent 面 LLM 出站按条目协议适配:`chat` / `chat_vision` / `chat_with_tools` 三通道全协议;`embed` 恒 OpenAI 兼容(Anthropic 无 embeddings 语义)。
- 新增 anthropic 出站通道(官方 `anthropic` SDK 的 AsyncAnthropic,与 openai 通道「官方 SDK」形态同构):Claude Code 同构 base_url(SDK 请求 `{base}/v1/messages`)、`x-api-key` + `Authorization: Bearer` 双头鉴权(官方与国内分支双兼容)。
- ReAct 内核零改动:内核继续产出/消费 OpenAI 形态消息史与 `LlmToolCall`,协议翻译收敛在 LLM 客户端层(assistant.tool_calls/tool 角色消息 ↔ tool_use/tool_result ↔ function_call/function_call_output)。
- 用量采集语义不变:三协议应答均归一为 prompt/completion/total tokens 走既有 `_report` 通道;失败调用维持 038 ERROR 采集口径。
- 文档配套:`docs/model-providers.md` Anthropic 分支节改为可直连口径、`.env.example` 协议示例、ADR-008 立法。

## Non-goals(不做什么)

- Gemini 原生 generateContent 协议(Google 官方 OpenAI 兼容层已够用,`docs/model-providers.md` 表 A 已收录;原生协议出现真实需求再立项)。
- LLM 侧流式调用(内部恒非流式,SSE 形态是 api 面职责)。
- 协议自动探测 / 多协议回退链(协议是显式声明,不猜)。
- anthropic 鉴权头形态配置项(双头固定策略,见 design.md 决策 3)。
- agent 面直连中转站的产品化支持(中转站 = 普通 openai 端点自填,目录纪律不变)。

## Capabilities

### New Capabilities

(无——多协议出站归属既有 multi-model-routing 能力的注册表/出口语义延伸)

### Modified Capabilities

- `multi-model-routing`:注册表条目新增 protocol 字段与三协议出站语义;白名单/路由/用量采集语义不变;缺省 openai 时行为与引入前逐字一致(零回归)。

## Impact

- `apps/agent/kongming_agent/settings.py`(ModelEntry.protocol 解析校验 + 主模型 env)
- `apps/agent/kongming_agent/llm.py`(协议分支接入三通道;openai 路径不动)
- 新增 `apps/agent/kongming_agent/llm_protocol.py`(翻译纯函数 + anthropic 官方 SDK 客户端封装)
- 测试:`apps/agent/tests/test_llm_protocol.py`(新增)、`test_model_routing.py`(条目协议校验)
- 零 API 契约变更(contracts/openapi.json 不动);零前端影响;零 schema 变更;新增直依赖:官方 `anthropic` SDK(纯 Python,传递依赖 httpx2/pydantic 均轻,锁文件随变更入库)
