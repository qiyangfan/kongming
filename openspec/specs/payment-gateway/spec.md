## Purpose

账单收款闭环:支付通道抽象(mock 实现先行)+ 支付单生命周期 + 回调验签
核销幂等 + 账单支付联动,使 PENDING 账单可被支付、取消、过期与作废;
真实通道(微信支付/支付宝)接入时仅新增通道实现,核销主链不变。

## Requirements

### Requirement: 支付单创建

系统 SHALL 支持门户用户对本人租户的 PENDING 账单发起支付:创建支付单
(sys_payment:invoice_id/tenant_id/channel=MOCK/amount[厘,快照自账单]/
status=CREATED/expires_at=创建+30min);每张账单 MUST 至多一张 CREATED
在途单,同账单再次发起支付 MUST 先将旧在途单置 CANCELLED 再插新单;
已 PAID 或 VOID 账单 MUST 拒绝创建(409 同形);跨租户账单 MUST 404
同形。创建 MUST 落审计(payment.create)。并发双击 MUST 幂等收敛
(返回既有在途单,不甩 500);账单状态判定与插入 MUST 原子(防竞态
二次收款)。

#### Scenario: 首次发起支付

- GIVEN 本人租户一张 PENDING 账单
- WHEN 发起支付
- THEN 创建 CREATED 支付单(金额=账单快照,30min 过期)并审计

#### Scenario: 重复发起作废旧单

- GIVEN 该账单已有一张 CREATED 支付单
- WHEN 再次发起支付
- THEN 旧单置 CANCELLED,新单创建成功(在途单恒一)

#### Scenario: 已支付账单拒再付

- GIVEN 账单已 PAID
- WHEN 发起支付
- THEN 409 拒绝,不创建支付单

#### Scenario: 并发双击幂等

- GIVEN 两个并发发起支付请求(同一 PENDING 账单)
- WHEN 其一已在途
- THEN 另一请求幂等返回既有在途单,无重复落单,无 500

### Requirement: 回调核销(统一路径)

系统 SHALL 提供统一核销逻辑供各通道入口共用:验签(HMAC 通道密钥,
compare_digest)→ 金额复核(回调金额与单快照不符 MUST 拒绝并审计
payment.amount_mismatch)→ 状态机 CAS(仅 CREATED 可转 PAID)→
同事务将账单置 PAID(仅 PENDING 可转,VOID 后付款不回写;联动失败
MUST 落异常审计 payment.invoice_not_pending,不静默)→ 审计
payment.paid。重复回调/已取消/已过期单据 MUST 收敛幂等成功响应,
不报错、不重复联动。模拟失败回调 MUST 仅审计 payment.failed,支付单
保持 CREATED 可再试。伪造签名 MUST 拒绝(401 同形)并审计。

#### Scenario: 成功回调核销

- GIVEN 一张 CREATED 支付单
- WHEN 通道回调成功(签名合法,金额一致)
- THEN 支付单 CAS 置 PAID(记 paid_at/trade_no),账单同事务置 PAID,
  审计落库

#### Scenario: 重复回调幂等

- GIVEN 支付单已 PAID
- WHEN 同一回调重放
- THEN 幂等成功响应,无重复状态变更,无重复审计

#### Scenario: 伪造签名拒绝

- GIVEN 签名不合法的回调
- WHEN 到达回调入口
- THEN 401 拒绝,支付单状态不变,落审计

#### Scenario: 金额不符拒绝

- GIVEN 签名合法但金额与支付单快照不符
- WHEN 回调
- THEN 拒绝并审计 amount_mismatch,单据状态不变

#### Scenario: 失败回调非终态

- GIVEN 一张 CREATED 支付单
- WHEN 通道回调失败结果
- THEN 支付单保持 CREATED 可再次支付,审计 payment.failed

### Requirement: 支付通道抽象与 mock 通道

支付通道 SHALL 以端口抽象(创建/验签/解析),通道实现注册可替换;
v1 提供 MOCK 通道:与真实通道同构的 HMAC-SHA256 签名(密钥经环境
变量配置,开发默认值仅供本地),交易号 mock 前缀生成。mock 通道
SHALL 支持经环境开关整体关闭(默认开启——mock 是 v1 唯一收款
路径)。回调入口 POST /billing/payments/callback/{channel} SHALL
无登录态、以验签为鉴权(未注册通道 404)。

#### Scenario: mock 通道签名验通

- GIVEN 配置 mock 密钥
- WHEN mock 签名回调到达
- THEN 验签通过进入核销

#### Scenario: mock 通道关闭

- GIVEN 环境开关关闭 mock 通道
- WHEN 发起支付或回调
- THEN 404/403 收敛,无支付面暴露

### Requirement: 收银台

系统 SHALL 提供门户收银台页(/portal/pay/{payment_id}):展示支付单
金额/账单期/模拟收款码区;「模拟支付成功」「模拟支付失败」按钮 MUST
调用服务端 mock 模拟端点(登录+门户权限+本人租户复校),由服务端
真实生成通道签名后走统一核销路径——前端 MUST NOT 直连改单,也
MUST NOT 持有通道密钥;支付状态经轮询刷新,终态(PAID/CANCELLED/
EXPIRED)展示对应结果态。

#### Scenario: 模拟支付成功

- GIVEN 收银台打开一张 CREATED 支付单
- WHEN 点击「模拟支付成功」
- THEN 服务端生成签名经统一核销置 PAID,页面轮询到已支付态,
  账单列表该账单同步已支付

#### Scenario: 模拟支付失败可重试

- GIVEN 收银台打开一张 CREATED 支付单
- WHEN 点击「模拟支付失败」
- THEN 支付单保持 CREATED,页面仍可再次发起模拟支付

### Requirement: 支付单查询与取消

门户用户 SHALL 可查询本人租户支付单详情(归属复校,跨租户 404
同形);CREATED 单 SHALL 可取消(CAS 置 CANCELLED,审计
payment.cancel);CREATED 单过期 MUST 惰性判定(读取/核销前置
EXPIRED,审计 payment.expired)并以每日调度批量兜底。

#### Scenario: 取消在途单

- GIVEN 一张 CREATED 支付单
- WHEN 取消
- THEN 置 CANCELLED,该账单可重新发起支付

#### Scenario: 过期惰性置态

- GIVEN 一张 CREATED 支付单已过 expires_at
- WHEN 查询或回调核销
- THEN 单据置 EXPIRED 并审计,核销按幂等收敛不再生效

#### Scenario: 跨租户不可见

- GIVEN 租户 B 的支付单
- WHEN 租户 A 查询/取消
- THEN 404 同形

### Requirement: 账单作废

超管 SHALL 可将 PENDING 账单置 VOID(审计 billing.invoice.void);
VOID 账单 MUST 拒绝再创建支付单;已 PAID 账单 MUST 拒绝作废
(409)。作废前若存在 CREATED 在途支付单 MUST 同事务置 CANCELLED。

#### Scenario: 作废待付账单

- GIVEN 一张 PENDING 账单(含在途支付单)
- WHEN 超管作废
- THEN 账单置 VOID,在途单同事务 CANCELLED,审计落库

#### Scenario: 已支付账单拒作废

- GIVEN 账单已 PAID
- WHEN 超管作废
- THEN 409 拒绝
