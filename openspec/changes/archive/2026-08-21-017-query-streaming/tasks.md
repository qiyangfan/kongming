# 017 query-streaming 任务清单

## 1. agent 面(节点事件+流式端点)

- [x] 1.1 graph/streams.py:事件发射器(wrap_stage 节点 wrapper;running/done
  stage 对发;validate 通过发 sql;summarize 后发 summary;selfheal
  retryCount=本次序号;非流式 get_stream_writer RuntimeError 静默跳过)
  +单测(非流式直调零行为/事件字段)
- [x] 1.2 build.py:get_graph() 各节点挂 wrapper(nodes.py/prompts.py 零改动);
  同步 /query 回归测试既有全绿
- [x] 1.3 main.py:POST /query/stream(astream 双通道;终态构造抽公共函数
  与 /query 共用;LlmError→success=false result;意外异常→error 终态;
  SSE 帧格式 event:/data:;响应头 Cache-Control/X-Accel-Buffering)
- [x] 1.4 agent 测试:流式事件序列(成功链路 stage 成对+sql+summary+result)/
  validate 拒绝不发 sql/自愈 retryCount 透出/LLM 故障同同步口径/
  流式与同步终态同构直证

## 2. api 面(中继+断线续存+落库)

- [x] 2.1 chat/sse.py:SSE 帧结构解析纯函数(agent 字节流→(event,data) 帧;
  注释行忽略/多 data 行拼接;JSON 容错在 service 层——畸形过程帧跳帧、
  result 帧畸形收敛 error 终态)+单测
- [x] 2.2 chat/ports.py:SessionFactory 端口(后台 task 独立会话);
  chat 契约面核对(import-linter 零框架不破)
- [x] 2.3 chat/agent_client.py:query_stream(httpx stream 消费 agent SSE,
  帧回调形态;非 200/传输失败收敛 AgentUnavailable 同语义)
- [x] 2.4 chat/service.py:流式编排——前段共用(抽私有段与 query 同源)、
  _consume_agent 后台 task(result 帧落库:三态映射/result_json/chart/
  writeback/标题任务触发条件与同步一致;独立会话;FAILED 收敛)、
  终态富化(result 事件 data 构造与同步响应同映射)
- [x] 2.5 chat/router.py:POST /chat/query/stream(权限/租户/前段校验异常
  常规 JSON;StreamingResponse+心跳 15s wait_for;四事件 data 模型进
  OpenAPI responses)
- [x] 2.6 api 测试:流式全链(mock agent 流)/断线续存(直测后台 task:
  消费完+落库)/传输失败 error 终态+FAILED 落库/persist=false 零落库/
  前段校验失败常规 JSON/心跳超时分支/result 与同步逐字段一致直证

## 3. web 面(流式消费+呈现)

- [x] 3.1 api.ts:streamChatQuery(doFetch 复用;401 单飞重放;分帧解析;
  resp 非 ok 走 toStatusError;signal 透传 abort)
- [x] 3.2 StreamCard 组件:阶段指示(running/done 视觉+selfheal 重试
  次数)+SQL 折叠提前渲染+summary 打字机追加;shadcn/lucide/tailwind
  token 纪律
- [x] 3.3 ChatPage:提问切流式(流式状态管理/守卫防迟到写入/AbortController
  触发点=切会话/新建/切租户/卸载;终态接管+invalidateQueries;流异常
  呈现与问句回填沿用现有形态)
- [x] 3.4 make contracts 重生成类型+web tsc/eslint 全绿

## 4. 质量门禁与冒烟

- [x] 4.1 make check 全绿(api/agent ruff+mypy+lint-imports+pytest+web)
- [x] 4.2 016 评估基线复验:KM_EVAL_ADMIN_PASSWORD=… uv run pytest evals
  -m smoke(基线红线零劣化)
- [x] 4.3 真 LLM+Playwright MCP 冒烟:流式全链(阶段点亮/SQL 提前/打字机/
  终态接管)、REJECTED 分态、断线续存(中断后重进会话可见结果)、
  取消(切会话不污染)、persist 语义、016 同步链路无回归
- [x] 4.4 R1 独立评审(作者/评审分离)缺陷清零+/goal 审查循环
