## Purpose

让用户把助手配置成 Bot(system prompt + 工具子集 + 数据源 + 模型),复用 ReAct 内核运行,可发布为 web 嵌入与 OpenAI 兼容 API;运行时权限继承创建者并受工具防御与护栏全量约束。

## ADDED Requirements

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
