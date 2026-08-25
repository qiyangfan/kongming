# 019 tool-calling:ReAct 工具调用内核

## Why

现有 Agent 是一条固定 8 节点 NL2SQL 管线(understand→…→summarize),只能
「问数」;roadmap 第三梯队的 6 个域(mcp-interop / workflow-orchestration /
cloud-code-sandbox / custom-agent / conversation-artifacts / multimodal-input)
全部以「LLM 运行时自主选择并调用工具的 ReAct 内核」为前置。本变更把 Agent
从固定管线升级为通用工具调用平台,同时以与 NL2SQL 同范式的纵深防御守住
「AI 永不持凭证」的信任边界(ADR-002)。评估基线(016)与 017 流式链路已
就位,正是动内核而不动契约的最佳窗口。

## What Changes

- **agent 面新增 ReAct 内核**:LLM 按注入的工具 schema 自主产出
  `tool_calls` → 回调执行 → 观察结果 → 再决策,直至产出最终答案或触达
  轮次上限;终态响应字段与既有问答端点逐字段同构(success/verdict/
  summary/insights/stats/columns/rows/sql/retry_count/error)。
  既有 `/query` 与 `/query/stream` 8 节点图**逐字节保留**作为「预设工具
  配方」向后兼容(评估集 016 基线不降)。
- **api 面新增通用工具执行回调** `POST /internal/tool/execute`(内部令牌,
  ADR-002 同构):工具注册表为唯一真理源(agent 不维护独立工具 schema,
  由 api 在调用 agent 时按调用方授权集注入);每次执行经六层纵深防御
  (内部令牌与身份复校 → 工具权限点 → 租户白名单 → 参数 JSON Schema →
  审计+限流 → 写工具幂等/人工确认)。
- **内置工具集**:`validate_sql` / `execute_sql` / `rag_retrieve`(只读
  三件套,复用现有防火墙/执行路由/三路召回)+ `create_system_announcement`
  (写工具示例:幂等键 + 人工确认)。
- **新表**(Alembic 迁移,ADR-004):`sys_tool_audit`(工具执行审计,
  成功/拒绝/失败/限流全量落)+ `sys_announcement`(公告写工具落点)。
- **新权限点**(种子只增不摘):`tool:sql:validate` / `tool:sql:execute` /
  `tool:rag:retrieve` / `tool:announcement:create` / `system:tool:manage`;
  SUPER_ADMIN 自动覆盖,DEVELOPER 推荐绑定只读三件套的入口等价权限。
- **流式契约扩展**(ADR-005):SSE 新增 `tool_call` 事件(toolName/args 摘要/
  status∈{running,done,rejected}/result 摘要),老前端忽略未知事件向后兼容;
  事件 schema 入 OpenAPI(017 注册手法)。
- **web 面**:对话卡片新增工具调用链可视化(chips 范式,017 阶段指示器
  风格);写工具人工确认交互(待确认卡片 → 确认/拒绝 → 执行回执);
  /admin/tools 第八子页(工具清单只读 + 工具审计分页查询,行级可见性
  与 014/015 同口径)。
- **问答入口**:chat 查询请求新增可选 `agent_mode`(`recipe` 老链路=
  缺省零回归 / `react` 新内核);web 提供会话级开关,缺省不变。

## Capabilities

### New Capabilities

- `tool-calling`: 工具注册表与元数据、ReAct 调用循环(轮次上限/终止条件/
  向后兼容)、通用工具执行回调、工具六层纵深防御、内置工具集语义、
  工具调用流式事件、写工具幂等与人工确认、工具管理与审计查询。

### Modified Capabilities

- `nl2sql-agent`: ReAct 内核终态与既有问答响应同构要求、tool_call 流式
  事件与既有 stage/sql/summary 事件并列的契约、老链路零回归条款。
- `chat`: 查询请求新增 `agent_mode` 与写工具确认端点的行为契约(响应
  结构纯增、缺省零回归)。

(新权限点在 tool-calling 域 delta 内声明,沿 014/015 惯例不单独 delta rbac。)

## Non-goals

- 不做 MCP 互操作(mcp-interop 独立变更,复用本变更注册表/防御)。
- 不做安全向 prompt 护栏(injection 检测/PII 脱敏/意图审核——上游定位为
  本域伴生,020 独立变更;本变更只预留「工具意图审核在参数校验之前」
  的挂接点语义)。
- 不做自定义 Bot(agent-as-config,custom-agent 域)、不做沙箱代码执行
  工具(cloud-code-sandbox 域)、不做工作流编排。
- 不写工具泛化:内置写工具仅 `create_system_announcement` 一个示例,
  不开放任意 SQL 写通道(六层防御语义内,SQL 工具恒只读)。
- 不改动既有 8 节点图节点逻辑与 prompt 资产(prompts.py 纪律不变);
  不淘汰老链路(评估基线以其为准)。
- 多 worker 限流/审计锁不解决(沿 008 挂账②单 worker 前提,同口径登记)。

## Impact

- **代码**:
  - apps/agent:`graph/react/`(ReAct 循环与状态)、`llm.py` 增加
    tool_calls 形态调用、`apiclient.py` 增加 tool_execute 回调、
    `main.py` 新增 /query_react(+stream);既有 graph/ 一行不动。
  - apps/api:新域包 `kongming/toolcall/`(models/ports/service/repository/
    router 宪法分层);`internal/router.py` 挂 tool/execute;chat 域
    service/router 增 agent_mode 与确认端点;种子权限点;alembic 新迁移
    (sys_tool_audit + sys_announcement)。
  - apps/web:StreamCard/AssistantCard 工具链 chips、确认卡片交互、
    /admin/tools 子页、api.ts 类型全生成(ADR-005)。
- **契约**:openapi.json 新增内部端点(内部面)+ chat 纯增字段 +
  tool_call SSE 事件 schema + 管理端点;make contracts 重生成。
- **依赖**:agent 侧无新依赖(openai SDK 原生支持 tools);api 侧
  JSON Schema 校验用已有依赖或新增轻量 `jsonschema`(design 定);web 零新依赖。
- **ADR 引用**:ADR-002(信任边界:agent 零凭证、执行 100% 回调)、
  ADR-004(新表迁移)、ADR-005(契约管线)。
- **安全**:新执行面=六层防御全量覆盖 + 全量审计;测试先行(安全规则类)。
