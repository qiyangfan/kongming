# 034 developer-portal-billing:开发者门户与计费配额

## Why

025 的 api-key 体系让外部系统能调 Bot,但没有滥用防线与商业闭环:
任何持 key 者可无限烧 token。本变更补配额与计费层:三维配额
(token/调用/成本,fail-closed)+套餐与账单+开发者门户(上游
developer-portal-billing 域的 Kongming 适配;api-key 管理 025 已建全
[哈希存储/撤销/单 Bot 绑定],不重复建设,025 语义不变)。

## What Changes

- **配额三维**(api 新 `billing` 域包 + 0022 迁移 sys_quota_policy/
  sys_billing_plan/sys_invoice):租户级策略(dimension=TOKENS/CALLS/
  COST,period=MONTHLY,limit_value,warn_pct 默认 80);**网关切面
  fail-closed**:POST /v1/chat/completions 执行前判定当月用量
  (sys_llm_usage 聚合——021 服务端采集,不经客户端,防伪造)+
  当月调用数(sys_tool_audit channel=api 计数)+成本估算(plan
  单价×token),任一超限 → 429 QUOTA_EXCEEDED + 审计;达 warn_pct
  响应头 X-Quota-Warning 标记(不拒绝)。
- **套餐与账单**:sys_billing_plan(name/monthly_tokens/monthly_calls/
  price_per_mtok[厘/千 token]/price_per_call[厘/次]);租户绑套餐
  (配额=套餐值,策略可覆盖);账单=lifespan 每日薄壳检查(015/031
  同款)——每月 1 日为上月未出账租户生成 sys_invoice(usage_json
  明细[模型×token×单价]+amount,status PENDING);明细按租户隔离
  可查。v1 不接支付网关(出账单/预付费语义)。
- **开发者门户**:/portal 页(新导航项,登录即可,限自己租户)——
  api-key 列表(复用 025 端点)+当期用量看板(三维进度条+预警标记)
  +账单列表与明细对话框;新权限点 developer:portal(排序 27,进
  DEVELOPER 推荐集——自助门面)。
- **超管配置**:/admin/billing 第十五子页——套餐 CRUD+租户配额策略
  CRUD+全部账单概览;system:billing:manage(排序 28,仅超管)。

## Capabilities

### New Capabilities

- `developer-portal-billing`:配额三维 fail-closed、套餐与周期账单、
  开发者门户、超管计费配置的行为契约。

### Modified Capabilities

- `custom-agent`:OpenAI 兼容网关前置配额切面(超额 429 拒)。
- `multi-model-routing`:sys_llm_usage 转正为计费用量口径(021 服务端
  采集;「统计非计费」域内旧注按本域转正修订)。

## Non-goals

- 不接支付网关(账单 PENDING 出账语义,收款是商务流程);不做
  api-key 限权扩展(025 单 Bot 绑定语义不变——上游 scopes 形态由
  025 R1-M5 摘死决策覆盖,不重开);不做请求内同步 token 采集
  (021 fire-and-forget 口径为 v1 计费基准,严格计费级采集随支付
  网关再做);不做按用户/Bot 维度的配额(v1 租户级);不做用量
  实时推送(看板即查询)。
- 不改 api-key 既有端点(025 建全);不做账单 PDF 导出(列表+
  明细 JSON 展示)。

## Impact

- **代码**:api 新 billing 域包(models/ports/repository/service/router
  [策略/套餐/账单/门户聚合])+网关切面(bot router v1 端点前置
  quota check)+lifespan 出账薄壳+web PortalPage/BillingPage。
- **Schema**:0022 迁移三表(ADR-004 约定;金额列 INTEGER 厘
  [禁浮点])。
- **契约**:/billing/* 配置端点+/portal/* 自助端点;make contracts。
- **ADR 引用**:ADR-004/ADR-005。
- **安全**:配额 fail-closed+用量服务端唯一来源+租户隔离+审计;
  测试先行(超额拒/未超放/预警/账单生成/隔离)。
