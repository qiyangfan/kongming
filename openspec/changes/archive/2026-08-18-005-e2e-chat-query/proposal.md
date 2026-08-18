# 005 端到端问答链路(Agent LLM 接线 + 用户面 /chat/query)

## Why

agent 面 8 节点图骨架就位但全是桩(节点返回空值,不碰 LLM、不回调);
004 已把 `/internal/sql/execute` 做成真(三态 verdict + 审计)。两条半成品之间
缺的就是本变更:agent 三节点 LLM 接线 + execute 真回调 + api 用户面问答端点,
走通「登录用户提问 → 控制面 → Agent 图 → 受控执行 → 结构化洞察 → 会话落库」
的第一条端到端问答。这是产品首发王牌(数据问答)的命门链路,roadmap 第一梯队之首。

## What Changes

- **agent 面 LLM 接线**(移植上游已验证资产,prompt 原文保值):
  - `understand`:history(最近 5 轮)指代消解/省略补全,无 history 走单轮改写;
  - `generate`:PostgreSQL 只读 SELECT 生成(显式 JOIN/聚合识别/ILIKE/时间窗/
    列投影精确/结果集保护),selfheal 错误喂回,输出清洗(剥 markdown 标记);
  - `summarize`:结构化洞察 JSON(summary + insights[]),stats 本地确定性计算,
    LLM 异常/非 JSON 降级纯文本不报错。
- **agent 面 execute 节点真实化**:httpx 回调 api `/internal/sql/execute`
  (X-Internal-Token),三态 verdict 入 state;BLOCKED(权限类)路由直接终态
  不重试(省配额),ERROR 进 selfheal 重试(上限 2,骨架既有)。
- **agent 面 validate 节点**:本地轻量预检(非空 + sqlglot 可解析 + 单语句 +
  Select 根),检出写操作视为权限类直接终态;深度校验仍在 api 防火墙(004),
  预检不复刻防火墙规则集,价值是省一轮无效回调与 LLM 配额。
- **agent 面新端点 `POST /query`**(内部令牌守门,中间件已有):请求
  trace_id/tenant_key/user_id/question/history/datasource_key;响应
  success/verdict/summary/insights/stats/columns/rows/sql/retry_count/error。
- **api 用户面新端点 `POST /chat/query`**:JWT 认证 + **require_perm 依赖首发**
  (权限点 `chat:execute`,注册表种子只增不改,SUPER_ADMIN 自动补齐、DEVELOPER
  增量绑定)+ X-Tenant-Id 租户白名单校验(multitenant spec)+ 会话自动新建
  (标题=首问前 30 字符)/消息落库(USER+ASSISTANT,status 三态映射
  SUCCESS/FAILED/REJECTED)+ history 后端补全(session_id 在而无显式 history
  时取最近 5 条 user 问句)+ trace_id 生成贯穿 + 回调 agent(超时/不可用
  映射友好错误,不裸 500)+ 图表配置建议(服务端确定性推断:日期列→line、
  低基数分类→pie、类别+数值→bar、明细→none)。
- **api 内部面新端点 `POST /internal/meta/schema`**:agent retrieve 的 schema
  来源——information_schema + pg_description 实时内省 tenant_\<key\> schema
  (表/列/类型/注释),表数上限截断防 prompt 爆炸;RAG 未落地前的过渡形态
  (nl2sql-agent「Schema 智能选取」delta 立约,召回收窄留待 rag 变更)。
- **契约**:`/chat/query` 与 `/internal/meta/schema` 落定即跑 `make contracts`,
  openapi.json 同步提交(ADR-005)。
- **依赖**:api 的 httpx 从 dev 提升为主依赖(回调 agent);agent 新增 sqlglot
  (validate 纯 AST 预检,无 IO,不违 ADR-002)。

## Capabilities

### New Capabilities

- `internal-meta`: 内部元数据通道的行为契约——租户 schema 实时内省
  (范围限定 tenant_\<key\> + public、表数截断、注释来源、未知/禁用租户
  fail-closed),供 agent retrieve 节点消费。

### Modified Capabilities

- `chat`: 新增「问答查询端点」Requirement(认证/权限点/租户校验/会话落库/
  history 补全/trace_id/回调 agent 失败语义/图表建议);修订「多轮上下文后端
  补全」——「不传 sessionId = 临时对话不落库」与「首次问答自动新建会话」存在
  GIVEN 重叠张力,澄清为:缺省自动新建+落库,临时对话须显式 `persist=false`。
- `nl2sql-agent`: 修订「Schema 智能选取」——RAG 落地前的过渡形态立约:
  retrieve 经内部元数据通道取租户 schema 全量内省(截断保护),非硬编码全表
  提示、非空提示;RAG 召回收窄落地后本过渡条款退役。
- `nl2sql`: 新增「控制面回调 Agent 面」Requirement(报文字段/内部令牌/
  超时与不可用语义/三态 verdict 映射到消息状态)。
- `sql-execution`: 新增「控制面保留表拒读」Requirement(005 审查循环实证:
  public 控制表经问答链路透出口令哈希/密文的纵深缺口,执行路由收口
  RESERVED_TABLE 拦截)。

## Non-goals

- 不做前端任何页面(主布局/对话区/会话侧栏是下一棒);本变更冒烟走 curl。
- 不做 RAG 三路召回/知识库落库/LLM 润色元数据(rag/knowledge 变更);
  schema 来源仅实时内省过渡形态。
- 不做 KNOWLEDGE_QA 意图分流(依赖 rag 知识召回);understand 只做指代消解。
- 不做 SSE 流式/打字机(上游 chat_stream 资产,随 query-streaming 域评估);
  本变更同步响应。
- 不做会话列表/切换/删除/重命名端点、结果导出 CSV/Excel、EXPLAIN 查看、
  图表 PNG 下载(chat spec 其余 Requirement,随前端主布局变更交付)。
- 不做动态数据源(004 已 fail-closed;请求透传 datasource_key 但非 default
  照旧 400);不做多模型路由/计费/输入护栏/ReAct tool-calling(第三梯队)。
- 不改 sql-execution/datascope/auth 既有契约;不加数据库迁移(chat 表
  baseline 已建全);不做 sys_oper_log 操作日志切面(004 non-goal 延续)。

## Impact

- **代码(apps/agent)**:`graph/build.py` 七节点桩替换为真实现;新增
  `llm.py`(AsyncOpenAI 封装)、`apiclient.py`(httpx 回调)、`main.py`
  增 `/query` 端点;settings 增 LLM 超时/重试上限。
- **代码(apps/api)**:新增 `kongming/chat/` 域包(router/service/models/
  repository,含图表建议纯函数)、`kongming/internal/meta.py`(schema 内省);
  `auth` 域新增 require_perm 依赖;种子注册表加 `chat:execute`;
  httpx dev→主依赖。
- **契约**:openapi.json 增 /chat/query + /internal/meta/schema;web 类型重生成。
- **测试**(安全规则类先行):agent 节点单测(mock LLM/回调:prompt 组装、
  清洗、降级、BLOCKED 不重试、图全链路 scripted LLM);api 全栈(真实 PG/Redis:
  require_perm 403/放行、会话隔离不泄露、history 补全、回调 agent 失败映射、
  meta 内省 schema 边界);真 LLM 端到端冒烟(tenant_default.goods 活数据)。
- **ADR 引用**:ADR-002(信任边界:agent 零凭证、回调通道、LLM key 只住
  agent)、ADR-003(用户面端点 JWT 认证语义)、ADR-005(契约管线)。
