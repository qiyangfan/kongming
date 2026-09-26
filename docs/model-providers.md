# 模型供应商端点预设目录(model-providers)

> agent 面 LLM 配置的**端点选型参考**。配置层(ADR-007 修订,2026-09-26)不绑定
> 任何网关:官方直连端点是一等公民,muniu 与 ollama 一样只是「自建网关/本地推理」
> 类的**可选项**。协议事实与各家 Plan 订阅端点红线以 muniu 仓
> `docs/provider-presets.md` 为交叉核对源(2026-09-26 版)。

## 配置入口(能力边界)

| 配置 | 作用 | 约束 |
|---|---|---|
| `KM_AGENT_LLM_BASE_URL` / `_MODEL` / `_API_KEY` | 主模型(单模型形态) | OpenAI 兼容端点;协议可声明(见 B 节) |
| `KM_AGENT_LLM_PROTOCOL` | 主模型出站协议(ADR-008) | `openai`(缺省)\|`anthropic`\|`responses`;非法值启动告警回退 openai |
| `KM_AGENT_MODEL_REGISTRY_JSON` | 多模型注册表(021):主模型/embedding/vision 等多出口 | 条目 = name/base_url/api_key/model(+可选 protocol),api_key 仅进程内存 |
| `KM_AGENT_EMBEDDING_*` | embedding 独立配置(009) | 缺省回退主模型 `llm_*` 组;切非通义主模型时注意 embedding 端点是否仍存在 |
| `KM_AGENT_MODEL_ALLOWED_HOSTS` | 出口白名单(021) | 新增端点的主机名必须放行,否则注册表条目启动即拒配 |

**协议边界**:agent 面出站协议按条目(或主模型)声明(ADR-008):`openai`
(Chat Completions,缺省)| `anthropic`(Anthropic Messages)| `responses`
(OpenAI Responses)。三协议覆盖 chat/vision/chat_with_tools 通道;embedding
恒走 OpenAI 兼容 embeddings API(Anthropic 无 embeddings 语义)。

## A. 官方 OpenAI 兼容端点(直连可用)

| 家 | base_url | 模型示例 | 备注 |
|---|---|---|---|
| 阿里百炼(千问) | `https://dashscope.aliyuncs.com/compatible-mode/v1` | qwen-plus / qwen3.8-max | 本项目缺省端点;embedding 同源 |
| DeepSeek | `https://api.deepseek.com/v1` | deepseek-v4-flash / deepseek-v4-pro | |
| Kimi(Moonshot) | `https://api.moonshot.cn/v1` | kimi-k3 | 国际站 `api.moonshot.ai/v1`,Key 不通用 |
| GLM 智谱 | `https://open.bigmodel.cn/api/paas/v4` | glm-5.3 | Coding Plan 端点分立,勿混用(见 muniu 目录红线) |
| Doubao 火山方舟 | `https://ark.cn-beijing.volces.com/api/v3` | doubao-seed-2-1-pro | 订阅 Plan 端点与按量分开计费 |
| MiniMax | `https://api.minimax.cn/v1` | MiniMax-M3 | 国际站 `api.minimax.io/v1` |
| StepFun 阶跃 | `https://api.stepfun.com/v1` | step-3.7-flash | |
| 百度千帆 | `https://qianfan.baidubce.com/v2` | deepseek-v4-pro | |
| 腾讯混元 TokenHub | `https://tokenhub.tencentmaas.com/v1` | hy3 | |
| Xiaomi MiMo | `https://api.xiaomimimo.com/v1` | mimo-v2.6-pro | |
| LongCat(美团) | `https://api.longcat.chat/openai/v1` | LongCat-2.0 | |
| xAI Grok | `https://api.x.ai/v1` | grok-4.5 | |
| OpenAI | `https://api.openai.com/v1` | — | |
| Gemini(官方兼容层) | `https://generativelanguage.googleapis.com/v1beta/openai` | — | |

聚合/本地平台(同样 OpenAI 兼容直连):SiliconFlow(`api.siliconflow.cn/v1`)、
ModelScope(`api-inference.modelscope.cn/v1`)、Ollama(`http://127.0.0.1:11434/v1`)、
vLLM/SGLang 自托管等。

**中转站纪律**:社区切换工具预设列表里的第三方中转站/聚合站不进本目录;确要接入
就当普通 OpenAI 兼容端点自填 + 白名单放行,平台不为它们背书。

## B. Anthropic 分支与 Responses 端点(多协议直连,ADR-008)

背景:DeepSeek / Kimi / GLM / 千问 / Doubao / MiniMax / StepFun / 千帆 / 混元 /
LongCat / MiMo 等几乎都提供官方 Anthropic Messages 兼容分支,且大量编码模型装在
订阅制 Coding Plan 里(价格远低于按量);多家另有原生 OpenAI Responses 端点。
端点全表见 muniu 仓 `docs/provider-presets.md` 表 B。

agent 面自 multi-protocol-llm 起支持三协议直连,按条目(或主模型 `KM_AGENT_LLM_PROTOCOL`)
声明出站协议:

| protocol | 协议形态 | URL 语义 | 鉴权 |
|---|---|---|---|
| `openai`(缺省) | OpenAI Chat Completions | base_url 即端点 | `Authorization: Bearer` |
| `anthropic` | Anthropic Messages | SDK 请求 `{base}/v1/messages`(与 Claude Code `ANTHROPIC_BASE_URL` 同构) | `x-api-key` + `Authorization: Bearer` 双头 + `anthropic-version` |
| `responses` | OpenAI Responses | base_url 即端点 | `Authorization: Bearer` |

- anthropic 条目的 base_url 填分支端点根(不含 `/v1/messages`,SDK 自动拼接);
- 双头鉴权:官方端点认 `x-api-key`,国内分支普遍认 Bearer,双发双兼容;
- 三协议用量均归一为 prompt/completion/total tokens 入既有采集通道;embedding
  不参与协议适配(恒 OpenAI 兼容,009);
- 经 muniu 桥接仍是可选路径(密钥收敛/统一计量场景),直连为一等公民后非必需。

条目示例(分支端点以 muniu 目录表 B 为准):

```json
[
  {"name": "glm-coding", "base_url": "<厂商 Anthropic 分支端点>", "api_key": "…", "model": "…", "protocol": "anthropic"},
  {"name": "qwen-rsp", "base_url": "<厂商 Responses 端点>", "api_key": "…", "model": "…", "protocol": "responses"}
]
```

## C. muniu 作为可选网关(类 ollama 条目)

选 muniu = 把「密钥收敛、跨供应商统一计量、failover 换道」买回家(论证见
ADR-007 原决策);不选 = 这三项成本自担,零架构阻碍。形态上它就是另一个
OpenAI 兼容端点:

```bash
KM_AGENT_LLM_BASE_URL=http://127.0.0.1:8787/v1   # muniu 入站
KM_AGENT_LLM_API_KEY=mk-...                       # muniu 签发的平台 key
```

## 配置示例

单模型官方直连(切家 = 改三行 + 白名单):

```bash
KM_AGENT_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
KM_AGENT_LLM_MODEL=qwen-plus
KM_AGENT_LLM_API_KEY=sk-...
```

多模型注册表(官方直连 + 自建网关混布;021 env JSON,base_url 可各自指向不同家):

```json
[
  {"name": "qwen-plus", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": "…", "model": "qwen-plus", "capabilities": ["chat"]},
  {"name": "deepseek-v4", "base_url": "https://api.deepseek.com/v1", "api_key": "…", "model": "deepseek-chat", "capabilities": ["code", "reasoning"]},
  {"name": "qwen-vl", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": "…", "model": "qwen-vl-max", "capabilities": ["vision"]},
  {"name": "local-ollama", "base_url": "http://127.0.0.1:11434/v1", "api_key": "ollama", "model": "qwen3:8b"}
]
```

> 白名单:以上条目的主机名(除随 `llm_base_url` 自动入白名单的主模型主机外)
> 须写入 `KM_AGENT_MODEL_ALLOWED_HOSTS`,否则启动即拒配(021 fail-closed)。
