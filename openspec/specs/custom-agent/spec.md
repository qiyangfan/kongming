## Purpose

让用户把助手配置成 Bot(system prompt + 工具子集 + 数据源 + 模型),复用 ReAct 内核运行,可发布为 web 嵌入与 OpenAI 兼容 API;运行时权限继承创建者并受工具防御与护栏全量约束。

## Requirements

### Requirement: Bot 定义(agent-as-config)

系统 SHALL 支持用户定义 Bot:bot_key(租户内唯一,小写蛇形)、name、system_prompt、工具子集(注册表工具名集合)、datasource_key、model(021 注册表模型名,可空=主模型)、enabled、is_public(分享开关)。创建/编辑时 SHALL 校验:工具子集 ⊆ 创建者当前被授予的工具权限点对应的工具集(越权绑定拒绝 400);datasource_key 归属当前租户;model 在注册表内(非空时)。Bot 按 tenant_id 隔离。

#### Scenario: 越权工具绑定拒绝

- GIVEN 创建者无 tool:announcement:create 权限
- WHEN 创建 Bot 工具子集含 create_system_announcement
- THEN 拒绝(400),不落库

#### Scenario: 定义合法落库

- GIVEN 工具子集在创建者权限内
- WHEN 创建
- THEN 落库成功,bot_key 租户内唯一约束生效

### Requirement: Bot 运行时权限继承

Bot 运行 SHALL 继承创建者权限:每次运行的注入工具集 = Bot 配置子集 ∩ 创建者当前权限对应工具集(逐请求动态求交);创建者权限被回收后,对应工具 SHALL 在运行时失效(无需 Bot 侧任何同步);工具执行仍走六层防御与意图审核(020),审计以创建者身份落库并携带渠道标记。

#### Scenario: 权限回收即时失效

- GIVEN Bot 配置含 execute_sql,创建者的 tool:sql:execute 被回收
- WHEN 任一渠道调用该 Bot
- THEN LLM 可用工具集不含 execute_sql(零额外同步)

#### Scenario: 六层防御不绕过

- GIVEN Bot 运行中 LLM 产出非法 SQL 工具调用
- WHEN 执行回调
- THEN 与 web 渠道同口径被防火墙拦截并落审计

### Requirement: 发布渠道

系统 SHALL 提供两类发布渠道:①web 嵌入——/bot/{bot_key} 免登录公开对话页,仅当 Bot enabled 且 is_public 时可用(否则 404 同形);②OpenAI 兼容 API——POST /v1/chat/completions(api-key 鉴权,响应为 choices/usage 形态;流式不做)。渠道调用 SHALL 经 020 输入/输出护栏(与 web 问答同口径)。

#### Scenario: 公开嵌入页可用

- GIVEN Bot 启用且公开
- WHEN 未登录访问 /bot/{bot_key} 并提问
- THEN 正常回答(经护栏与工具防御)

#### Scenario: 未公开 404

- GIVEN Bot is_public=false
- WHEN 访问 /bot/{bot_key}
- THEN 404(不暴露存在性)

### Requirement: API Key 最小体系

系统 SHALL 提供 Bot 渠道的 api-key:创建时明文仅返回一次,库内只存 SHA-256 哈希;撤销即失效;scopes 限定 bot_keys 集合(越集合调用拒绝);api-key 鉴权独立于 JWT(X-Api-Key 头)。管理端点要求登录态+Bot 归属(创建者或超管)。

#### Scenario: 明文只显示一次

- GIVEN 创建 api-key
- WHEN 创建响应返回
- THEN 明文仅在本次响应出现;后续列表只见哈希摘要与备注

#### Scenario: 撤销即失效

- GIVEN api-key 被撤销
- WHEN 用其调用 /v1/chat/completions
- THEN 401

### Requirement: 发布治理与审核

Bot 公开发布(打开 is_public)或保存 system_prompt 时 SHALL 经 020 输入护栏审核(injection 规则集),命中 SHALL 拒绝并提示;Bot 管理端点(列表/创建/编辑/启停/分享开关/api-key 管理)SHALL 按租户隔离且仅创建者或超管可操作;全部写操作落操作日志。

#### Scenario: 提示词违规拦截发布

- GIVEN Bot 的 system_prompt 含「忽略以上指令」类 injection 内容
- WHEN 保存或开启公开
- THEN 拒绝(400)并提示未通过安全检查

#### Scenario: 他人 Bot 不可操作

- GIVEN 用户 B 尝试编辑用户 A 的 Bot
- WHEN 提交
- THEN 404 同形(不暴露存在性)

### Requirement: Bot 工具目录含 MCP 工具

Bot 编辑器的可选工具目录 SHALL 合并内置工具与租户内已注册 MCP
工具(持 tool:mcp:use 权限的创建者可见);Bot 绑定 MCP 工具后,
运行时经动态解析执行(mcp-interop spec);server 禁用/删除后,已
绑定其工具的 Bot 运行调用该工具 SHALL 被拒并落审计。渠道枚举
SHALL 纯增 "mcp"(/mcp 端点调用溯源)。

#### Scenario: 绑定 MCP 工具的 Bot 运行

- GIVEN 创建者持 tool:mcp:use,Bot 勾选 1 内置工具+1 MCP 工具
- WHEN Bot 运行(任意渠道)
- THEN LLM 可见工具恰为这 2 个;MCP 工具调用经 019 主链执行

#### Scenario: server 禁用后绑定失效

- GIVEN Bot 绑定了 mcp__fs__read_file
- WHEN 该 server 被禁用后运行 Bot
- THEN 调用该工具被拒(TOOL_NOT_REGISTERED),落审计

### Requirement: Bot 归属与团队可见性

Bot 列表端点 SHALL 按归属过滤:创建者本人的 Bot + 用户作为成员
的团队的 Bot(team-workspace spec 角色规则:VIEWER 可见可运行/
MEMBER 可编辑/OWNER 全权);非成员团队的 Bot MUST NOT 出现在
列表,直访/编辑按同规则 404/403 同形。Bot 定义 SHALL 纯增
team_id 归属列(NULL=个人,零回归);归属变更 SHALL 限团队
OWNER 或创建者本人并落审计。公开页与 api-key 渠道语义不变
(公开即公开,key 绑定即授权)。

#### Scenario: 团队 Bot 对成员可见可运行

- GIVEN Bot 归属团队 T
- WHEN T 的 VIEWER 查询列表并调用该 Bot
- THEN 列表可见,运行正常;编辑被拒(403)

#### Scenario: 非成员不可见

- GIVEN Bot 归属团队 T
- WHEN 非 T 成员查询列表或编辑
- THEN 列表不含该 Bot;编辑 403/404 同形

#### Scenario: 个人 Bot 零回归

- GIVEN Bot 无团队(team_id NULL)
- WHEN 查询/运行
- THEN 仅创建者可见可用,与团队域引入前一致

### Requirement: Bot 上架列与商店渠道

Bot 定义 SHALL 纯增上架字段(published_at/published_by/greeting/
suggestions);未上架 Bot MUST 不出现在商店任何列表(含创建者视角);
上架后 Bot 运行渠道枚举 SHALL 纯增 "store"(商店直跑溯源)。

#### Scenario: 新建默认未上架

- GIVEN 新建 Bot
- WHEN 查看商店列表
- THEN 不出现(默认未上架)

#### Scenario: 商店直跑溯源

- GIVEN 上架 Bot
- WHEN 经商店直跑
- THEN 运行经 025 链路,审计 channel="store"

### Requirement: 网关配额切面

OpenAI 兼容端点 POST /v1/chat/completions SHALL 在 Bot 执行前经过
配额切面(developer-portal-billing spec「用量配额」「Bot 维度配额」):
租户级与 Bot 级双路判定,任一超额 429 QUOTA_EXCEEDED(拒绝维度
文案带 scope 前缀)不落运行;预警经响应头标记;api-key 撤销/无效
401 语义不变。Bot 执行完成后,本次调用的 LLM 用量 MUST 在响应
返回客户端前入账(bot 链路请求内同步采集,见 multi-model-routing
spec)——配额判定的当月用量包含此前全部已应答调用。

#### Scenario: 超额网关拒

- GIVEN api-key 所属租户当月超限
- WHEN 调用 /v1/chat/completions
- THEN 429 QUOTA_EXCEEDED,不执行 Bot,落审计

#### Scenario: Bot 级超限网关拒

- GIVEN 当前 api-key 绑定 Bot 命中 BOT 级配额且已超限
- WHEN 调用
- THEN 429 QUOTA_EXCEEDED: BOT:<维度>,不执行 Bot,落审计

#### Scenario: 未配置配额零回归

- GIVEN 租户无配额策略且未绑套餐
- WHEN 调用
- THEN 无配额判定,行为与切面引入前一致

#### Scenario: 用量请求内入账

- GIVEN 一次正常网关调用(多次 LLM 调用)
- WHEN 响应返回客户端
- THEN 本次全部用量已落 sys_llm_usage(含 bot_id 归属),紧随其后的
  新请求配额判定可计及本次
