# Tasks

## 1. 配置面:协议声明与 fail-closed 校验

- [x] 1.1 settings.py:`ModelEntry` 增 `protocol: str = "openai"`;`parse_registry` 解析该字段,取值域外(非 openai/anthropic/responses)告警剔除该条目;`Settings.llm_protocol: str = "openai"`(env `KM_AGENT_LLM_PROTOCOL`),`fail_closed_checks` 对主模型非法值告警并回退 openai。验证:`tests/test_model_routing.py` 新增 3 组(缺省 openai 零回归 / 畸形条目 protocol 剔除告警 / 主模型非法值回退告警),先红后绿。
- [x] 1.2 `.env.example` 补 `KM_AGENT_LLM_PROTOCOL` 注释示例(anthropic 分支条目与 responses 条目各一行);`docs/model-providers.md` B 节由「不可直连」改写为三协议直连口径(含 anthropic base 拼接 `/v1/messages` 与双头说明)。验证:文档人工走读 + `.env.example` 与 settings 字段名逐一比对。

## 2. 翻译层:llm_protocol.py(测试先行)

- [x] 2.1 新增 `kongming_agent/llm_protocol.py` 骨架:`ProtocolReply` 数据类 + 协议常量枚举 + 归一 `Usage` 类型。验证:`ruff check` / `mypy` 通过。
- [x] 2.2 anthropic 翻译纯函数(先写失败测试 `tests/test_llm_protocol.py::test_anthropic_translation_*`):system 置顶、user/assistant(含 tool_calls→tool_use 块)、role:tool→tool_result 块、工具声明 function schema→input_schema、vision data-url→base64 source、应答回读(text/tool_use→LlmToolCall,arguments 畸形保 raw_args)、usage input/output→prompt/completion(total 求和)。验证:表驱动用例全绿。
- [x] 2.3 responses 翻译纯函数(同文件先红后绿):instructions、历史 assistant 文本/function_call、function_call_output、扁平工具声明、输出 message/function_call 回读、usage 映射。验证:表驱动用例全绿。
- [x] 2.4 anthropic SDK 客户端封装:pyproject 增官方 `anthropic` 依赖(uv sync 更新锁文件);AsyncAnthropic(base_url=条目端点,`default_headers` 补 `Authorization: Bearer`);错误映射(APIStatusError/APIConnectionError/超时/应答畸形 → LlmError)。验证:`httpx2.MockTransport`(anthropic SDK 的 HTTP 栈)注入 `AsyncAnthropic(http_client=…)` 集成钉子 4 组(双头断言 / 200 正常回读 / 5xx → LlmError / 畸形 JSON → LlmError),先红后绿。

## 3. LLM 客户端接入:三通道协议分支

- [x] 3.1 llm.py:`_resolve` 返回值扩为 (client, model, protocol),池条目与主模型携带协议;chat/chat_vision/chat_with_tools 在 openai 分支保持行为等价(可执行语句零语义改动),anthropic/responses 走翻译层 → 统一回填 `_report`(usage 归一)与既有返回结构;embed 保持 openai 不动。验证:既有 `tests/test_react.py` / `test_model_routing.py` / `test_usage_collector.py` 全绿(零回归实证)。
- [x] 3.2 集成钉子:anthropic 条目的 chat_with_tools ReAct 往返(MockTransport 假上游,tool_use→观察→最终答案收敛)+ responses 条目 chat 通道路由命中(`MODEL_REGISTRY_JSON` 注入)。验证:`tests/test_llm_protocol.py` 集成组全绿。
- [x] 3.3 失败语义:anthropic/responses 调用超时/非 2xx 时 `_report(status="ERROR", latency_ms)` 落记录断言(038 口径)。验证:集成组断言全绿。

## 4. 收尾门禁与立法

- [x] 4.1 ADR-008(docs/adr/008-multi-protocol-egress.md):多协议出站立法——三协议取值域、anthropic 官方 SDK(否决 httpx 手写:与 openai 通道 SDK 形态同构 + 协议漂移维护面外包给官方)、双头鉴权裁决、零回归边界。验证:文档评审走读,引用 openspec change 与 ADR-007 修订。
- [x] 4.2 全量门禁:`make check`(api/agent ruff+mypy+pytest + web tsc+eslint)exit 0;覆盖率门禁不降。验证:命令输出贴验收记录。
- [x] 4.3 交叉自查:变更 diff 全量复审(零回归 = openai 路径行为等价,diff 仅三元组解构/cast 收窄等无害接入;协议分支异常路径均收敛 LlmError + ERROR 采集);`make contracts` 无漂移(零 API 变更实证)。验证:diff 走读记录 + contracts diff 为空。
