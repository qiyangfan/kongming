# Design

## Context

agent 面 LLM 客户端(`apps/agent/kongming_agent/llm.py`)为薄封装:三通道(chat / chat_vision / chat_with_tools)全部内部**非流式**,调用方以 OpenAI 形态交互——ReAct 内核(`graph/react/nodes.py`)产出/消费的消息史为 dict 形态的 `user` / `assistant(含 tool_calls)` / `tool(含 tool_call_id)` 三种;`LlmToolCall` 携带 `call_id/name/args/raw_args`。路由单点 `_resolve` 返回 (client, model);用量经 `_report`(duck-type `prompt_tokens/completion_tokens/total_tokens`)统一采集。ADR-007 修订(2026-09-26)后官方直连为一等公民,端点预设目录见 `docs/model-providers.md`。

## Goals / Non-Goals

**Goals**

- 三协议(openai / anthropic / responses)覆盖 chat / vision / chat_with_tools 三通道;条目级 + 主模型级协议声明。
- 缺省 `openai` 时行为与引入前**完全一致**(零回归是硬判据;openai 可执行语句语义零变动,diff 仅三元组解构/类型收窄等无害接入)。
- ReAct 内核、用量采集、路由、白名单、Bot 覆盖语义零改动。
- 依赖增量最小且可控:仅新增官方 `anthropic` SDK(纯 Python,传递依赖 httpx2/pydantic 均轻);不手写协议传输层。

**Non-Goals**

- Gemini 原生 generateContent 协议(Google 官方 OpenAI 兼容层已够用,`docs/model-providers.md` 表 A 已收录;原生协议出现真实需求再立项)。
- LLM 侧流式调用(内部恒非流式,SSE 形态是 api 面职责)。
- 协议自动探测 / 多协议回退链(协议是显式声明,不猜)。
- anthropic 鉴权头形态配置项(双头固定策略,见决策 3)。
- agent 面直连中转站的产品化支持(中转站 = 普通 openai 端点自填,目录纪律不变)。

## Decisions

1. **协议枚举与配置面**:`protocol ∈ {openai, anthropic, responses}`,条目级(注册表 JSON 字段,缺省 openai)+ 主模型级(`KM_AGENT_LLM_PROTOCOL`,缺省 openai)。条目畸形值 = 单条目剔除告警(与缺字段同级);主模型畸形值 = 启动告警回退 openai(与「主模型 base_url 不在白名单只告警不拒」的既有兼容口径一致)。备选:每条目多协议候选列表——拒绝,回退链属路由语义,协议是端点属性,组合只会制造不可审计的出站。

2. **翻译层单点收敛**:新增 `kongming_agent/llm_protocol.py`,只做两件事——①纯函数翻译(canonical OpenAI 形态 ↔ anthropic Messages 形态 / responses 输入输出形态);②anthropic SDK 客户端封装(构造/鉴权头/错误映射)。`llm.py` 三通道在 `_resolve` 后按协议分支:openai 走既有代码路径(**行为等价,可执行语句零语义改动**),anthropic/responses 走翻译层后经统一 `ProtocolReply(content/tool_calls/usage)` 回到既有 `_report` 与返回结构。备选:把 openai 也重构进适配器接口——拒绝,零回归优先,重构留给协议语义稳定后的独立变更。

3. **anthropic 客户端 = 官方 `anthropic` SDK(AsyncAnthropic),双头鉴权**:否决 httpx 手写(本设计初稿曾选 httpx,推翻)——①与 openai 通道「官方 SDK」形态同构:同一模块一边 SDK 一边手写是两套哲学,一致性本身是维护性;②Messages 协议在活跃演进(thinking 块、cache 计费 tokens、新块类型),手写层会把「跟进供应商协议漂移」变成自己的长期维护面,SDK 把它外包给官方——用量归一对新字段(如 cache tokens)的适配成本趋近于零;③SDK 纯 Python,传递依赖 httpx2/pydantic 均轻,边际成本低;「为一个端点加依赖不值」是把网关库的零依赖美学错置到应用层。URL 语义:base_url 即 Claude Code 的 `ANTHROPIC_BASE_URL` 同构(SDK 请求 `{base}/v1/messages`,预设目录分支端点按此口径收录)。鉴权:SDK 自动发 `x-api-key` + `anthropic-version`,再经 `default_headers` 补 `Authorization: Bearer <key>`——官方端点认 x-api-key,国内分支普遍认 Bearer(cc-switch 预设的 AUTH_TOKEN 形态),双发免去配置项;SDK 重试默认与 openai 通道既有默认一致,不新增重试语义。`max_tokens` 必填:未显式传时用常量缺省 8192(SQL 生成/ReAct 决策的输出量级充分,且为文档化行为)。

4. **翻译表(保真边界)**:
   - system → anthropic 顶层 `system` / responses `instructions`;不进消息史。
   - `assistant.tool_calls` → anthropic `tool_use` 块(`input` = 解析后 args,畸形原文存 `raw_args` 时以 `{}` 加错误说明的既有观察语义已在内核侧,翻译层只回放原文 arguments);responses `function_call` 项(`call_id/name/arguments` 原文)。
   - `role:"tool"` → anthropic `user` 消息内 `tool_result` 块(`tool_use_id` 对齐);responses `function_call_output`(`output` = 观察原文)。
   - 工具声明:openai function schema → anthropic `{name, description, input_schema: parameters}`;responses 扁平 `{type:"function", name, description, parameters}`。
   - vision:`data:<mime>;base64,<b64>` data-url → anthropic `image.source(base64)`;responses `input_image`(data-url 直传)。
   - usage:anthropic `input_tokens/output_tokens` → prompt/completion,total = 和;responses `usage.input_tokens/output_tokens/total_tokens` 同映射。畸形/缺失 usage → 既有 ERROR/0 采集语义(038)。

5. **测试先行域**(协议适配属仓库测试先行域):翻译纯函数表驱动(消息史三形态 × 两协议 + 工具声明 + vision + usage 归一)+ MockTransport 注入官方 SDK 的 anthropic 集成钉子(双头断言 / 非理 2xx → LlmError + ERROR 采集 / tool_use 回读)+ responses 翻译与缺省 openai 零回归钉子(`test_model_routing.py` 补条目协议校验用例)。

## Risks / Trade-offs

- [国内分支协议方言差异(如 stop_reason/块形态变体)] → 翻译层按官方 Messages 契约实现,方言差异在真实接入冒烟时登记预设目录(muniu 仓 provider-presets 同源),不预写猜测分支。
- [responses 端点方言差异(国产 Responses 实现成熟度不一)] → 目录已标注 ⚠ 待冒烟;翻译层只依赖 OpenAI Responses 公开契约核心面(message/function_call/usage)。
- [双鉴权头被个别端点拒收] → 概率极低(Claude Code 客户端自身即多头发送);若实证出现,加条目级 `auth_header` 配置是纯增演进,不破坏本次契约。
- [max_tokens 缺省 8192 对长输出任务不足] → chat_with_tools 的 SQL 生成与最终作答在该量级内;真实不足时按条目扩展显式配置(纯增)。

## Migration Plan

零迁移:配置缺省 = 现状等价回放。采用 = 给需要的注册表条目加 `protocol` 字段 + 白名单放行新主机;回滚 = 删字段即回 openai 通道。现网 muniu 形态部署不受影响。

## Open Questions

(无)
