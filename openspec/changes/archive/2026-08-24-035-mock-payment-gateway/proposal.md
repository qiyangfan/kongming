# 035 mock-payment-gateway:mock 支付闭环与计费域清偿

## Why

034 建设计费链路(三维配额/套餐/月首出账),但账单永久停在 PENDING——
系统算得出欠费却没有收款闭环;其 Non-goals 明确「严格计费级采集随支付
网关再做」。本变更兑现该约定:以 mock 通道落地完整支付闭环(状态机/
回调核销/幂等/审计全真,仅通道为模拟——与 ADR-003 自建认证不依赖
Keycloak 同构:通道抽象使未来接微信/支付宝只新增 Channel 实现,主链
零改动),并同域清偿 034 挂账的计费欠账(采集准确性/配额维度/套餐治理/
门户与配置体验)。

## What Changes

- **mock 支付闭环**(0023 迁移新 sys_payment 表):支付单(invoice 1:1,
  同期重付旧 CREATED 单自动作废)+ 支付通道抽象(PaymentChannel 端口,
  MockChannel 实现——mock 同样 HMAC 签名走统一回调端点,**核销路径唯一**,
  真实通道接入只换验签/解析)+ 收银台(/portal/pay/{id},模拟成功/失败
  均经回调,不直连改单)+ 核销(状态机 CAS 幂等,重复回调收敛;invoice
  PENDING→PAID 同事务;审计 payment.create/paid/cancel/expire)+ 支付单
  过期(lifespan 每日薄壳置 EXPIRED)+ 超管 VOID 作废入口(034 表预留
  状态补入口)。
- **请求内同步 token 采集**(034 挂账②):agent usage 改 request 级收集
  (contextvar),bot-run 响应纯增 usage_records(bot-run 上下文不再
  fire-and-forget,其余采集路径不变);api BotService.run 请求内同步批量落
  sys_llm_usage(带 bot_id,tenant 取 key 归属不再反解;trace_id+seq 唯一
  索引兜底幂等)——v1 计费基准从「尽力上报」升级为「请求内入账」。
- **Bot 维度配额**(挂账③):sys_quota_policy 扩 scope(TENANT/BOT)+
  scope_ref(bot_id),唯一索引改表达式形态(COALESCE 消 NULL 重复判);
  v1 切面租户级与 Bot 级双路判定(任一超限即拒);Bot 级 TOKENS/COST 用量
  取同步采集的 bot_id 归属。
- **套餐 U/D**(挂账④):PATCH /billing/plans/{id}(名称/额度/单价,审计)
  + DELETE(存在绑定租户 409 拒删;无绑定删除+审计)。
- **billing 自供租户清单**(挂账⑥):GET /billing/tenants(id/tenant_key/
  name,挂 MANAGE)——配置面下拉解耦 system:user:manage。
- **配置面体验清偿**(挂账⑦):策略删除 AlertDialog 确认(013 模式);
  套餐表单数值受控校验(非法行内报错,禁静默归零)。
- **管理面账单分页**(挂账⑧):/billing/invoices 改 page/size/total
  (014 page 模式同构),弃 LIMIT 200。
- **spec 口径补注**(挂账⑤⑨):CALLS=工具执行计数(纯问答 Bot 恒 0)、
  空问题 400 先于配额 429 为既定语义,注记进 spec。

## Capabilities

### New Capabilities

- `payment-gateway`:支付单生命周期、支付通道抽象(mock 实现)、回调验签
  核销幂等、账单支付联动、收银台的行为契约。

### Modified Capabilities

- `developer-portal-billing`:账单 PENDING 出账语义修订为支付闭环
  (PENDING→PAID/VOID);配额策略扩 scope(TENANT/BOT);套餐 U/D;租户
  清单自供;管理面账单分页;CALLS 与 429 顺序口径注记。
- `custom-agent`:v1 配额切面扩 Bot 级判定;bot 链路用量请求内同步入账
  (bot-run 响应纯增 usage_records)。
- `multi-model-routing`:用量采集口径修订——bot 渠道请求内同步入账
  (带 bot_id 归属)为计费基准,fire-and-forget 限非 bot 渠道。

## Non-goals

- 不接真实支付通道(微信支付/支付宝/Stripe),无商户签约/ICP 备案等商务
  动作——通道抽象已预留,接入=新增 Channel 实现+密钥配置;退款/发票/
  支付分账(bot-store 市场模式)不做。
- 不做用户维度配额(v1 网关无用户概念;chat 渠道计费未立约)与 TEAM
  scope 配额(用量无 team 归属)。
- 不做 Bot 级 CALLS 维度(sys_tool_audit 无 bot 归属列,019 域扩列后补)。
- web chat 渠道不过配额切面(v1 网关为计费唯一入口,034 既定)。
- 门户本人账单保持全量 list(个人账单量级小,分页仅管理面)。

## Impact

- **代码**:api billing 域包扩(payment 模型/端口/仓储/服务/路由)+
  BotService 同步落用量 + v1 切面 Bot scope + web PortalPage 收银台/
  BillingPage 完善;agent usage request 级收集+bot-run 响应纯增。
- **Schema**:0023 迁移(sys_payment 新表 + sys_quota_policy scope 扩 +
  sys_llm_usage.bot_id + 表达式唯一索引)——ADR-004 演进制,与 0008–0022
  链式同批提交。
- **契约**:支付单创建/查询/取消、回调、套餐 U/D、租户清单、账单分页
  端点;make contracts 重生成(ADR-005)。
- **ADR 引用**:ADR-004(schema 演进)/ADR-005(契约管线);mock 通道
  决策与 ADR-003 自建认证同构(通道可替换,不引新 ADR,design 记录)。
- **安全**:资金类操作测试先行(核销幂等/重复回调/伪造签名/跨租户);
  回调端点不鉴权但验签(031 webhook 入站同构)。
