# 025 custom-agent:自定义 Bot(agent-as-config)

## Why

019 提供了 ReAct 内核、020 提供了护栏,平台具备把「固定助手」升级为
「用户可配置的 Bot」的全部内核条件:用户配置 system prompt + 工具子集 +
数据源 + 模型,复用单一 ReAct 内核运行,运行时权限继承创建者并受六层
防御与护栏全量约束。Bot 是第三波其余域(mcp-interop 的 Bot as tools、
bot-store 的上架、billing 的配额)的共同前置。

## What Changes

- **Bot 定义**(agent-as-config,Alembic 0014 sys_bot_definition):
  bot_key(小写蛇形)/name/system_prompt/工具子集(注册表工具名集合)/
  datasource_key/model 覆盖(021 注册表名)/enabled;tenant+创建者归属。
  创建/编辑时 SHALL 校验工具子集 ⊆ 创建者当前被授予的工具权限点集
  (越权绑定拒绝)。
- **Bot 运行时**:经 ReAct 内核运行——LLM 决策 system=Bot 的
  system_prompt(拼内核纪律后缀),工具集 = Bot 配置子集 ∩ 创建者当前
  权限(逐请求动态过滤;创建者权限回收后对应工具运行时失效,零额外
  同步);工具执行走 019 /internal/tool/execute 六层防御 + 020 护栏
  (意图审核按 Bot 调用方语义),全部调用落 sys_tool_audit(渠道字段
  可溯)。
- **发布渠道 v1**:①web 嵌入——公开分享链接(/bot/{bot_key} 免登录
  只读对话页,Bot 启用且公开开关打开时可用);②OpenAI 兼容 API
  (POST /v1/chat/completions,api-key 鉴权,choices/usage 形态响应)。
  api-key 最小体系(0014 sys_api_key:SHA-256 哈希存储+明文仅创建时
  返回一次+撤销即失效+scopes 限 bot_ids;独立鉴权不经 JWT)。
- **发布治理**:公开发布前 SHALL 过 020 护栏审核(system_prompt 过
  输入护栏 injection 规则,命中拦截发布);Bot 管理端点(CRUD/启停/
  分享开关)按租户隔离,创建者或超管可操作。
- **web**:对话区 Bot 选择器(默认孔明=内置配方/ReAct);/admin/bots
  管理子页(列表/创建编辑/渠道与密钥);/bot/{key} 公开嵌入页。

## Capabilities

### New Capabilities

- `custom-agent`: Bot 定义与校验、运行时权限继承、发布渠道(web 嵌入/
  OpenAI 兼容)、api-key 最小体系、发布治理的行为契约。

### Modified Capabilities

- `tool-calling`: ReAct 端点纯增 bot 配置注入(system_prompt 覆盖与
  工具子集过滤语义)——MODIFIED ReAct requirement 补 Bot 运行形态。

## Non-goals

- 不做 webhook/MCP 渠道(triggers-webhooks/mcp-interop 域);不做 IM 渠道。
- 不做 Bot 与工作流绑定(workflow-orchestration 域);不做 Bot 计费配额
  (developer-portal-billing 域,api-key 体系届时复用扩展)。
- 不做 Bot 市场/上架(bot-store 域);不做多 Bot 编排。
- Bot 对话不落 chat 会话体系(嵌入/API 渠道为无状态问答;会话化随
  conversation 域深化)。

## Impact

- **代码**:api 新域包 kongming/bot/(models/ports/service/repository/router);
  agent /query_react 纯增 bot_system_prompt 字段;alembic 0014
  (sys_bot_definition + sys_api_key);种子权限点 system:bot:manage(19);
  web Bot 选择器+/admin/bots 页+/bot/{key} 公开页。
- **契约**:Bot 管理端点 + /v1/chat/completions + QueryReactIn 纯增字段。
- **依赖**:零新依赖。
- **ADR 引用**:ADR-002(Bot 运行时零凭证,工具执行全回调)、ADR-004、
  ADR-005。
- **安全**:创建者权限继承的逐请求动态过滤(测试先行);api-key 哈希
  存储;公开渠道护栏审核;六层防御不因 Bot 开绕过。
