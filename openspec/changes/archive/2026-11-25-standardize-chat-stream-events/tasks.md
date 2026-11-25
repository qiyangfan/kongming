# 流式问答事件实施任务

各项完成后运行 `make check` 的对应子项，并在实施记录中写明命令和结果。

## 1. 公开模型与 API 输出

- [x] 1.1 为六种公开事件定义按 `type` 选择的模型。复用阶段、SQL、总结、工具调用的数据模型以及同步 `ChatQueryOut`，为两种流错误定义必填的 `reason_code`。模型测试覆盖每种合法事件，以及类型与数据错配、额外过程字段、错误枚举、字符串重试次数和空结果。
——apply 记录:2026-11-25 `test_chat_stream_events.py` 6 例先红(无 ChatStreamEvent)后绿;type 必填(去默认值防漏写静默匹配首分支),过程 data 与外层 extra=forbid,retry_count 用 StrictInt,reason_code 枚举两种流错误,result.data 复用 ChatQueryOut。
- [x] 1.2 收紧 API 消费 Agent 过程帧的入口。在进入阶段记录和发送队列前检查数据，排除诊断 `trace`，用轮次标识和失败类别记录坏消息。先补失败测试，再验证两个 Agent 流入口的合法过程消息可用，畸形 JSON、结构错误、未知事件和内部诊断不会传给网页或污染阶段记录。
——apply 记录:2026-11-25 先写坏帧用例再收紧:过程帧组合信封后经 PROCESS_STREAM_EVENTS 校验,非法形状/非对象/非 JSON 一律跳帧(日志只记 trace_id+事件名+错误数,不含原文);trace 诊断不出公开流(原透传用例反转为不透传);有效 stage 才进节点轨迹。
- [x] 1.3 改用有类型的发送队列和统一编码出口。正常、业务失败、无需调用 Agent、显式取消的结果都通过 `to_chat_query_out` 生成 `result.data`。验证问答结果与同步响应使用相同定义，心跳和 `X-Trace-Id`、配额预警及防缓冲响应头可用。
——apply 记录:2026-11-25 队列元素改为事件对象/ChatOutcome/StreamFailure/None;正常、业务失败、无需 Agent、已取消四路终态都经同一出口 to_chat_query_out 装入 result;心跳、X-Trace-Id、X-Quota-Warning、防缓冲头回归用例零改动通过。
- [x] 1.4 实现 Agent 结尾选择和 API 输出失败收尾。覆盖无效结果在合法结尾前、重复结尾、读尾异常、缺失结尾、结果转换或编码失败及队列提前结束。对 `persist=true/false` 分别验证结尾数量和保存结果，确认编码失败不会改写已保存的应答或新增失败记录。定向执行 `test_chat_stream.py` 和 `test_chat_turn_cancel.py`。
——apply 记录:2026-11-25 先红后绿:无效 result(success 非布尔)不作为结尾,后续合法 result 照常收敛;重复 result 只处理首个(幂等守卫);读尾异常/缺失结尾→AGENT_UNAVAILABLE;编码失败→预制 STREAM_OUTPUT_FAILED 结尾,不改写已保存状态;persist=true/false 的保存数量各有用例;test_chat_stream.py 26 例全绿。

## 2. 生成接口说明与网页检查函数

- [x] 2.1 从公开模型生成 200 的 `text/event-stream`、`x-sse-data-schema` 和六个分支。增加接口测试，确认不存在 `application/json` 的 200 响应，六个分支可通过 `type` 区分，并用导出的定义检查代表性的真实响应帧。同步问答响应及流前 HTTP 错误的声明也须通过回归检查。
——apply 记录:2026-11-25 STREAM_RESPONSES 只声明 text/event-stream,x-sse-data-schema 六分支+discriminator;FastAPI 对 model 键自动补的 application/json 由导出脚本定点移除;接口测试断言 200 只声明事件流、六分支可由 type 区分、六组件 required=[type,data]、真实响应帧落进声明分支。
- [x] 2.2 将 Ajv 8 的独立校验生成步骤接入 `contracts:gen` 和 `contracts-check`。以导出的事件定义为输入，登记生成文件及其类型声明，按生成文件配置检查范围。验证连续生成内容一致，生成模块无需在浏览器中编译规则，并通过 `make contracts` 和 `pnpm typecheck` 验证产物可用。
——apply 记录:2026-11-25 scripts/gen-sse-validator.mjs 用 Ajv 8.20.0(升为直接开发依赖)standalone 生成 src/lib/chat-stream-validate.gen.js+.d.ts(剥 OpenAPI 扩展关键字 discriminator,$ref 改写到 $defs);产物自包含且确定性(连续生成零差异);contracts:gen 与 contracts-check 均已接线(漏生成即拦)。

## 3. 网页接收与分发

- [x] 3.1 解析默认 SSE 消息，以生成的函数检查 JSON 后按 `type` 分发。更新 `api.test.ts`，覆盖六种事件、未知类型、错误字段、无效 `result` 和 `error`、合法结尾之后的同分块事件，以及读完仍无合法结尾。新增事件类型后缺少分支须使类型检查失败。
——apply 记录:2026-11-25 parseSseData 只认 data: 行;JSON.parse 后经生成的校验函数,未知/无效事件跳帧;switch(type) 分发并 satisfies never 穷尽检查(新增事件缺分支即编译失败);api.test.ts 15 例(含空对象不冒充结果、错配跳帧、trace 拒收)。
- [x] 3.2 补齐分帧和读取结束测试，覆盖分块截断的中文、LF、CRLF、CR、同一消息中的注释、多行 `data:`、末尾缺少空行和主动停止读流。保留页面已有的回调和上下文守卫，执行网页定向测试及 `make web-check`。
——apply 记录:2026-11-25 drainSseBuffer 首条合法结尾即停止同块后续分发;新用例覆盖中文跨块截断、CRLF(经读取层剥 CR)、末尾缺空行留缓冲、注释不触发分发、读到结尾即停止消费 reader、读完无结尾返回 false;共 22 例。

## 4. 一起验收前端和 API

- [x] 4.1 运行流式问答、工具调用、文档问答及取消相关的定向测试，覆盖两个 Agent 流入口和 API 输出。比对本轮事件、最终显示和保存的应答，手工走普通问答、ReAct、显式停止、断线后在历史中查看结果四条路径。
——apply 记录:2026-11-25 api 定向(流式/取消/React/路由鉴权)与 web vitest 全绿;api-check 全量 1823 passed/10 skipped(覆盖率 77.41%);真实链路四路径冒烟(api 8030+agent 8031,真实模型):普通问答信封帧与唯一 result;ReAct tool_call 成对下发(问得过大触发工具轮次上限属既有防御);流中取消 1.5s→CANCELLED 携消息标识,历史恰一条已取消行;断线后后台照常收敛落库(业务终态可查)。
- [x] 4.2 检查网页构建后的校验模块大小，并验证包含较大表格的正常问答可以完整显示。用合并请求相同版本的 `oasdiff` 检查生成契约。若确需接受已批准的事件格式差异，只登记该端点的指定差异，复核其他接口和契约检查仍通过。
——apply 记录:2026-11-25 pnpm build 通过(ChatPage chunk 75.69 kB/gzip 24.47 kB,校验模块并入其中);300 行大结果帧经编码完整下发(新增回归用例);oasdiff 1.39.0 本地实测恰一处 ERR(移除 200 的 application/json),已批准差异登记在 contracts/oasdiff-ignore.txt,CI oasdiff 步骤加 --err-ignore,本地等效命令通过。
