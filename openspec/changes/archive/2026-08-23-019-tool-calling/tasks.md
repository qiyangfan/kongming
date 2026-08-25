# 019 tool-calling 实施任务

> 安全规则类(六层防御/权限点/租户)测试先行:先写失败测试再实现。
> 每个任务组完成后跑对应 make check 子集并记录。

## 1. Schema 与种子(ADR-004)

- [x] 1.1 alembic 0008:sys_tool_audit + sys_announcement 两表(design D11 列清单;UUID v7/VARCHAR+CHECK/timestamptz 纪律;downgrade 完整)
- [x] 1.2 权限点种子:tool:sql:validate/tool:sql:execute/tool:rag:retrieve/tool:announcement:create/system:tool:manage(排序 10-14)+ DEVELOPER 推荐绑定只读三件套(只增不摘);种子断言测试更新(精确到列表相等,随 007 惯例)

## 2. api toolcall 域包(测试先行)

- [x] 2.1 失败测试:六层防御矩阵(未注册工具拒绝+审计/无权限点拒绝/跨租户拒绝/JSON Schema 越界拒绝/限流拒绝/审计全量落库)
- [x] 2.2 失败测试:内置工具 handler(execute_sql 走执行路由且 DDL 仍被防火墙拦/validate_sql 复用防火墙/rag_retrieve 三路召回透传/announcement 幂等键重复不双写)
- [x] 2.3 失败测试:写工具确认流(PENDING_CONFIRM 不落公告/确认后执行+回执/拒绝不落/过期按拒绝/重复确认幂等/他人确认 404 同形)
- [x] 2.4 实现 models.py(ToolSpec/args_digest 脱敏截断/幂等键生成纯函数)+ registry.py(四内置工具代码声明式注册)
- [x] 2.5 实现 repository.py(sys_tool_audit 写入独立短事务/分页查询行级可见性/sys_announcement 幂等写入)
- [x] 2.6 实现 service.py 六层编排(L1 租户绑定复校抽共用/L2 权限点/L4 jsonschema/L5 审计+Redis 固定窗口限流/L6 幂等+确认闸;handler 委托既有域)
- [x] 2.7 端点:POST /internal/tool/execute(内部)+ GET /system/tools + GET /system/tools/audit/page(system:tool:manage);import-linter 第 9 契约
- [x] 2.8 确认端点 POST /chat/tool-confirm(归属+过期+幂等;回执消息落库)

## 3. agent ReAct 内核

- [x] 3.1 LlmClient.chat_with_tools(tools 透传/tool_calls 解析/args JSON 畸形收敛为观察喂回;单测 mock 全分支)
- [x] 3.2 graph/react/:state+react_llm 节点(LLM 决策)+ tool_dispatch 节点(逐个回调 api tool_execute,观察喂回)+ finalize(终态同构映射 design D7)+ 轮次上限强制终止
- [x] 3.3 apiclient.py 增加 tool_execute 回调(HTTP 异常收敛为 ERROR 观察,与 sql_execute 同范式)
- [x] 3.4 /query_react 与 /query_react/stream 端点(终态构造复用/扩展 _build_query_out;stage_wrapper 挂 react 节点;tool_call 事件发射)
- [x] 3.5 agent 测试:ReAct 循环单测(mock api:数据查询自主编排/终止条件/轮次上限/未注册工具观察喂回/confirm_required 观察);流式事件序列测试

## 4. chat 集成与契约

- [x] 4.1 QueryChatIn 纯增 agent_mode(recipe 缺省/react);service 分支调 agent.query_react(注入 tools=registry 按权限过滤);空工具集退化语义
- [x] 4.2 ChatOutcome 纯增 pending_tool_calls(本 trace PENDING_CONFIRM 行聚合);同步/流式双路
- [x] 4.3 流式中继事件白名单加 tool_call;断线续存/心跳等 017 语义零改动复验
- [x] 4.4 make contracts 重生成 + 前端类型;017 SSE 手法注册 tool_call 事件 schema 入 OpenAPI
- [x] 4.5 chat 测试:agent_mode 分支/非法值 422/无权限空工具集/pending_tool_calls 聚合/确认端点全矩阵

## 5. web 面

- [x] 5.1 api.ts/类型消费:tool_call 事件分发 + pending_tool_calls 渲染数据流
- [x] 5.2 StreamCard/AssistantCard 工具调用链 chips(lucide 图标,状态 running/done/rejected;结果预览展开)
- [x] 5.3 写工具确认卡(确认/拒绝按钮 → tool-confirm → react-query 失效刷新回执)
- [x] 5.4 Agent 模式开关(zustand 存 km.agent_mode,缺省 recipe;会话区 Switch 复用 shadcn 组件)
- [x] 5.5 /admin/tools 第八子页(清单只读 + 审计分页/筛选/时间本地化,复用 015 模式;nav.ts 单源加项;权限守卫 system:tool:manage)

## 6. 评估与冒烟

- [x] 6.1 evals 支持 KM_EVAL_AGENT_MODE(react 时请求携带 agent_mode);README 记 ReAct 基线
- [x] 6.2 recipe 基线复验:smoke 5/5 + full 宽松 100% 零劣化
- [x] 6.3 真 LLM 冒烟:ReAct 数据查询自主编排全链/公告写工具确认流端到端(确认+拒绝+幂等)/未注册工具诱导拒绝+审计/限流触发/流式 tool_call 事件序列/UI 工具链 chips+确认卡+管理页/无权限退化

## 7. 评审与收尾

- [x] 7.1 R1 双路独立评审:web 路 2 必修+3 建议;api/agent 路 3 必修+6 建议(全修,附直证)
- [x] 7.2 R1 修复清零 + 复验(M1-M3/S1-S6/D1-D4 全修,直证 +5 用例,门禁全绿)
- [x] 7.3 make check 全绿(api+agent+web+import-linter)+ 冒烟记录
- [x] 7.4 HANDOFF 更新 + 挂账登记;openspec archive(delta sync 主 spec)
