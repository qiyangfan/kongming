# 025 custom-agent 设计

## Context

既有:019 ReAct 内核(/query_react,tools 注入)+ 020 护栏(输入/输出/
意图审核)+ 021 模型注册表(model 覆盖语义)+ 008 数据源归属。约束:
Bot 运行时零凭证(ADR-002)、六层防御不绕过、api-key 哈希存储。

## Goals / Non-Goals

Goals:Bot CRUD + 运行时(权限逐请求求交)+ 两渠道(web 嵌入/OpenAI
兼容)+ api-key 最小体系 + 治理页。Non-goals 见 proposal(webhook/MCP/
计费/市场不做)。

## Decisions

### D1: 表(0014)

sys_bot_definition(id uuidv7, tenant_id, bot_key, name, system_prompt TEXT,
tools JSONB[工具名数组], datasource_key, model VARCHAR(64) NULL, enabled
BOOL, is_public BOOL, created_by UUID, created_at/updated_at;
UNIQUE(tenant_id, bot_key));sys_api_key(id uuidv7, tenant_id, bot_id FK,
key_hash VARCHAR(64) UNIQUE[sha256], name, scopes JSONB{bot_keys:[]},
revoked_at, last_used_at, created_at)。

### D2: Bot 运行时(api 侧编排,复用 chat 组合根)

POST /bot/{bot_key}/chat(公开,免登录但 Bot is_public 闸)+ POST
/v1/chat/completions(api-key)。两入口共用 service.run_bot(bot_key,
question, channel):Bot 装载(enabled+public 校验)→ 创建者权限求交
(SqlPermRepo.list_codes(creator)→ registry.filter_specs ∩ bot.tools)→
调 agent /query_react(tools=交集, bot_system_prompt=bot.system_prompt,
tenant_key=Bot 租户, user_id=创建者, datasource_key=Bot 配置)→
护栏(输入 question 过输入护栏;输出 summary 过输出护栏)→ 审计
sys_tool_audit 由 tool/execute 自然落(user_id=创建者,trace 贯穿)。
OpenAI 响应形态:choices[0].message.content=summary,usage 从 021 通道
(不落响应,usage 字段给 null 或估值——v1 给 null,用量经 sys_llm_usage)。

### D3: agent 侧 bot_system_prompt 注入

QueryReactIn 纯增 bot_system_prompt: str = "";react prompts 组装:
bot 非空时 REACT_SYSTEM 的身份段替换——实现:REACT_SYSTEM 拆为
REACT_DISCIPLINE(工具纪律部分常量)与身份段;bot 模式 system=
bot_system_prompt + "\n\n" + REACT_DISCIPLINE(纪律后缀必保留,spec
立约)。空时=REACT_SYSTEM 原文(零回归)。

### D4: api-key 最小体系

key 生成:`km_` + secrets.token_urlsafe(32);库存 sha256 hex。鉴权依赖
`api_key_auth`(X-Api-Key 头):sha256 查行+revoked_at IS NULL+
bot.enabled+is_public 不强制(API 渠道不公开也可——公开语义只管 web
嵌入页;api-key 本身就是授权)。scopes.bot_keys 非空时校验目标 bot 在
集合内。撤销=revoked_at 置位。last_used_at 异步更新(同事务,低频)。

### D5: 治理审核

保存/开启公开时 system_prompt 过 020 evaluate_input(injection 规则);
命中→400「未通过安全检查」。管理端点:GET/POST/PUT/DELETE
/system/bots(+/{id}/enable|disable|share)+POST /system/bots/{id}/keys
+DELETE /system/bot-keys/{id};归属收口:WHERE id AND (created_by=me
OR 超管);无新管理权限点——Bot 属用户资产,创建需 chat:execute(会
用工具的用户才能建 Bot);system:bot:manage 仅超管全局列表。

### D6: web

/bot/{key} 公开页(极简对话:问题→回答,无会话);/admin/bots 子页
(列表/创建编辑对话框/启停/分享开关/api-key 创建一次展示);对话区
Bot 选择器 v1 不做(ChatPage 已支持 agent_mode;Bot 进主对话区随 UX
深化,登记挂账)。

## Risks / Trade-offs

- [公开渠道滥用(无认证问答烧 token)] → is_public 显式开关+护栏全量+
  020 限流不适用(公开页无用户);v1 接受,配额随 billing 域。
- [Bot 提示词削弱内核纪律] → 纪律后缀强制拼接;审核护栏拦 injection。
- [创建者离职/禁用后 Bot 仍在跑] → 权限求交后工具集为空(退化纯文本);
  Bot enabled 不随创建者状态(治理语义:超管可禁用);登记挂账。
- [api-key 暴力枚举] → sha256 哈希+撤销语义;速率限制随网关域。

## Migration Plan

1. 0014 两表(零存量);2. 种子权限点;3. 滚动重启(QueryReactIn 纯增,
   旧 api 不传零影响);4. 回滚=渠道端点下线即无入口,表留库无害。

## Open Questions

- O1:Bot 会话化(多轮记忆)——conversation 域深化。
- O2:公开页限流(IP 维度)——随 billing/网关域。
