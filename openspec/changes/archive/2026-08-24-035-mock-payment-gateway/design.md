# 035 mock-payment-gateway 设计

## Context

034 已建:billing 域包(策略/套餐/账单)+v1 网关切面(check_gateway_quota
租户级)+lifespan 月首出账薄壳;sys_invoice 状态 CHECK 已含
('PENDING','PAID','VOID') 但 PAID/VOID 无推进路径;用量唯一事实源=
sys_llm_usage(agent fire-and-forget 上报,可丢失偏低估);sys_quota_policy
唯一索引 (tenant_id, dimension, period);021 sys_llm_usage 有 trace_id
(同 trace 多行 LLM 调用共享)。约束:宪法 Article II(领域零框架)/
ADR-004(迁移演进制)/ADR-005(契约生成);lifespan 薄壳模式 015/031/034
三次复用。

## Goals / Non-Goals

**Goals:**
- 核销路径唯一:mock 与(未来)真实通道共用同一核销函数与同一状态机,
  收银台演练链路=真实链路形态。
- 资金语义安全:核销幂等(重复回调/重放/双击)、伪造签名拒、跨租户隔离、
  全审计;测试先行。
- v1 计费基准升级:bot 渠道用量请求内入账(响应返回时本次用量已可判定)。
- 034 挂账②③④⑥⑦⑧全部清偿,⑤⑨ spec 注记。

**Non-Goals:**
- 真实通道的签名算法/对账文件/退款(接入时按通道文档新增实现)。
- 支付单对账 job(mock 通道无资金真实流动,无对账对象)。

## Decisions

### D1 核销路径唯一:统一 reconcile,两入口

`BillingService.reconcile_payment(payment_id, trade_no, result, sign)` 是
唯一核销点:通道验签 → 状态机 CAS → invoice 联动 → 审计。两个入口:
- **mock-simulate 端点**(登录+门户权限+本人租户复校):收银台按钮调它,
  服务端用 mock 密钥**真实生成 HMAC 签名**后调 reconcile(reconcile 内
  照常验签——演练验签逻辑,非跳过);
- **callback/{channel} 端点**(无登录态,验签即鉴权,031 webhook 同构):
  未来真实通道的异步回调入口;v1 注册 MOCK 通道使 curl 可直接模拟回调
  (测试面/联调面)。

被否:收银台按钮直调「支付成功」端点改单——演练价值归零,未来接真通道时
核销路径分裂为二;前端持密钥自签回调——密钥泄漏。
被否:HTTP 自调 callback(服务端 httpx 调自己)——部署形态耦合(端口/
反代),函数级汇聚已保证路径唯一。

### D2 支付单状态机与幂等

状态机:CREATED → PAID / CANCELLED / EXPIRED(三终态)。票据:
- **每账单仅一张在途单**:partial unique index
  `(invoice_id) WHERE status='CREATED'`;同账单重复发起支付=旧 CREATED
  单同事务置 CANCELLED 再插新单(支付单号重新生成,无状态复用)。
- **核销 CAS**:`UPDATE ... SET status='PAID', paid_at=now()
  WHERE id=:p AND status='CREATED'`,rowcount=0=重复回调/已取消/已过期
  → 收敛 200(idempotent:true) 不报错(通道重试是常态,报错诱发
  无限重试);金额复核(回调 amount 与单快照不符=拒,审计
  payment.amount_mismatch)。
- **invoice 联动同事务**:CAS 成功即 UPDATE sys_invoice SET status='PAID'
  (WHERE status='PENDING' 防 VOID 后付款回写);审计 payment.paid。
- 模拟失败回调:单保持 CREATED 可再试(审计 payment.failed),不置终态
  ——失败非终态与主流通道语义一致。

被否:invoice 加 payment_id/paid_at 列——支付信息一对多(重付多张单),
派生查询(JOIN 最新单)即可,invoice 表不动。

### D3 惰性过期+每日薄壳兜底

CREATED 单 expires_at=创建+30min。读取路径(收银台查询/核销前)惰性判定:
过期即置 EXPIRED(审计 payment.expired);lifespan 每日薄壳批量兜底
(复用 015/031/034 模式)。纯薄壳方案演示周期太长(隔夜才过期),纯惰性
方案冷单永不归档,二者叠加。

### D4 请求内同步采集:agent 收集,api 落库

- **agent**:usage 收集改 contextvar request 级收集器;bot-run 端点入口
  挂收集器,出口把 records(带 seq=序号)附响应纯增字段 usage_records;
  **收集器在场时 LlmClient 不再 fire-and-forget**(bot 渠道唯一上报口=
  响应携带),收集器缺省(chat/recipe/titler 等)保持现状零回归。
- **api**:BotService.run 收响应后请求内批量 INSERT sys_llm_usage
  (tenant_id 取 api-key 归属[不再 tenant_key 反解],bot_id=key 绑定 Bot;
  ON CONFLICT (trace_id, seq) DO NOTHING 兜底重试双写);落库失败仅
  ERROR 日志不阻断应答(计费不可用不应放大为用户不可用;残余低估窗口
  远小于 fire-and-forget 整丢,挂账注记)。
- 0023 迁移:sys_llm_usage 加 bot_id/seq 列+(trace_id, seq) 部分唯一
  索引**谓词收窄为 bot 路径**(WHERE trace_id IS NOT NULL AND bot_id
  IS NOT NULL——chat 渠道 fire-and-forget 存量行为同 trace 多行 seq=0,
  裸 (trace_id,seq) 索引必撞,实施期推演抓回);存量无 bot_id 行,回填
  零行受影响。
- 配额切面的当月用量查询语义不变(读 sys_llm_usage),同步采集只是让
  bot 渠道数据更全更准——切面零改动获益。

被否:保留 fire-and-forget+api 按 trace_id 补量——双写口径漂移;
agent 全局改同步 await 上报——chat 路径每次 LLM 调用多一次 HTTP 往返
(ReAct 8 轮=8 次),为计费入口的正确性让全链路付延迟。

### D5 配额 scope 模型

sys_quota_policy 加 scope('TENANT'/'BOT')+scope_ref(BOT=bot_id,
TENANT=NULL,CHECK 互斥约束)。唯一索引重建为表达式形态
(tenant_id, dimension, period, scope, COALESCE(scope_ref, 零uuid))
——裸列索引 NULL 不参与判等会放过 TENANT 重复行。
判定:策略按 scope 分组;TENANT 级逻辑不变(套餐兜底);BOT 级
(scope_ref=当前 key 绑定 bot)查 bot 当月用量(sys_llm_usage bot_id
过滤),TOKENS/COST 两维;任一超限 429,拒绝维度文案带 scope 前缀
(BOT:TOKENS)审计归因。创建 BOT 策略时复校 scope_ref 为**该租户内**
存在的 Bot(跨租户引用 404)。
CALLS 维 Bot 级不立约(sys_tool_audit 无 bot 列)。

### D6 mock 密钥与回调验签

KM_BILLING_MOCK_SECRET(dev 默认占位,.env.example 登记;生产必须换)。
sign=HMAC-SHA256(secret, "{payment_id}.{trade_no}.{result}.{amount}")
hex;compare_digest 比对(031 同款)。交易号 mock 生成
`mock-{uuid4hex[:16]}`。

### D7 套餐删除保护

DELETE 前查 sys_tenant plan_id 引用计数,>0 → 409 PLAN_IN_USE(先解绑);
删除审计 billing.plan.delete。PATCH 全字段(名称/额度/单价)审计
billing.plan.update(detail 带变更前后)。**改价不回溯既有账单**
(usage_json 单价为出账时快照,既定)。

## Risks / Trade-offs

- [mock-simulate 端点被当「免费付款」滥用] → 它是 mock 通道语义本身
  (环境即无真实资金),且登录+门户权限+本人租户;生产部署可通过
  KM_BILLING_MOCK_ENABLED=false 整体关闭(env 闸,默认开——与
  027 沙箱默认关相反,因 mock 支付是 v1 唯一收款路径)。
- [(trace_id,seq) 唯一索引对存量数据炸] → 迁移内 ROW_NUMBER 回填后再
  建索引;conftest 全新建库无存量,dev 库量小。
- [同步落库增加 bot 链路尾延迟(一次批量 INSERT)] → 单事务批量,
  毫秒级;失败不阻断应答。
- [惰性过期与核销竞态:惰性置 EXPIRED 与并发回调 CAS] → 核销 CAS
  WHERE status='CREATED' 天然互斥,败者收敛幂等响应。
- [BOT 策略 scope_ref 悬空(Bot 后续被删)] → 策略保留但判定零命中
  (无 bot 用量),配置面列表展示 scope_ref 供清理;不立级联(配额
  策略是安全资产,静默删除不可取)。

## Migration Plan

0023 迁移单文件三对象:sys_payment 新表(部分唯一索引)+
sys_quota_policy 扩 scope 双列+CHECK+重建表达式唯一索引(存量行
scope 默认 TENANT 语义不变)+sys_llm_usage 扩 bot_id/seq+回填+
部分唯一索引。downgrade 逆向。与 0008–0022 链式同批按序提交;
新旧代码滚动兼容点:agent 旧版无 usage_records 时 api 落库步跳过
(响应字段缺省=空,零炸)。
