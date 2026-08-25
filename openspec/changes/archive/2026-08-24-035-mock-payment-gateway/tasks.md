# 035 mock-payment-gateway 实施任务

## 1. 迁移与域包骨架

- [x] 1.1 0023 迁移:sys_payment 新表(状态/通道 CHECK+部分唯一索引
  每账单一在途单)+sys_quota_policy 扩 scope/scope_ref(CHECK 互斥+
  存量默认 TENANT)+重建表达式唯一索引(COALESCE 零 uuid)+
  sys_llm_usage 扩 bot_id/seq(存量 ROW_NUMBER 回填)+(trace_id,seq)
  部分唯一索引;conftest 清单登记
- [x] 1.2 billing 域包扩:payment 模型(状态机纯函数/签名生成与验签
  HMAC)/ports(PaymentChannel+repo 扩)/MockChannel/service(reconcile
  唯一核销点+创建/取消/惰性过期/作废)/import-linter 契约同步
- [x] 1.3 配置:KM_BILLING_MOCK_SECRET/KM_BILLING_MOCK_ENABLED 进
  settings 与 .env.example

## 2. 支付闭环(测试先行:资金类)

- [x] 2.1 失败测试:创建(首付/重复付作废旧单/已付 409/跨租户 404/
  审计);核销(成功 CAS+invoice 联动/重复回调幂等/伪签 401/金额
  不符拒/失败回调非终态/过期惰性 EXPIRED);取消 CAS;作废(在途
  单连带 CANCELLED/已付 409);mock 开关关闭收敛
- [x] 2.2 实现:门户端点(创建/查询/取消/mock-simulate)+callback
  端点(验签即鉴权)+管理面 void+lifespan 过期薄壳兜底
- [x] 2.3 web 收银台页(/portal/pay/{id}:金额/账期/模拟成功/失败按钮
  经 mock-simulate+状态轮询+终态展示)+账单行「去支付」入口与
  支付状态列

## 3. 请求内同步采集(测试先行)

- [x] 3.1 失败测试:bot-run 响应带 usage_records(seq 序号);api 同步
  落库(bot_id/tenant 归属正确;trace_id+seq 重试去重);fire-and-forget
  在 bot-run 上下文不再触发;chat 路径采集零回归;落库失败不阻断
  应答
- [x] 3.2 agent:contextvar 收集器+LlmClient 双路(收集器在场只收集)+
  bot-run 端点出口附 usage_records;api:BotService.run 批量落库
  (ON CONFLICT DO NOTHING)

## 4. Bot 维度配额(测试先行:安全类)

- [x] 4.1 失败测试:BOT 级超限 429(BOT:TOKENS 归因)/Bot 级与租户级
  独立/跨租户 scope_ref 404/同组合 upsert 唯一/BOT 策略 bot 删除后
  零命中/CALLS 维 BOT 级拒配
- [x] 4.2 实现:策略 CRUD 扩 scope/scope_ref(传输层校验+存在性复校)
  +check_gateway_quota 双路判定+Bot 级用量聚合查询

## 5. 套餐 U/D、租户清单与分页(测试先行)

- [x] 5.1 失败测试:PATCH 更新+审计/绑定中删除 409/无绑定删除+审计/
  租户清单端点仅 billing:manage 可用(无 user:manage)/管理面账单
  page/size/total 分页
- [x] 5.2 实现三端点+仓储分页改造(弃 LIMIT 200)

## 6. 前端清偿(挂账⑦)

- [x] 6.1 策略删除 AlertDialog 确认(013 模式:busy 期禁关)
- [x] 6.2 套餐表单数值受控校验(非法行内报错,禁静默归零)
- [x] 6.3 BillingPage:套餐编辑/删除交互+租户下拉改 billing 清单源+
  账单分页组件+支付状态列+作废按钮

## 7. 验证与收尾

- [x] 7.1 make check 全绿;make contracts 重生成;016 评估 smoke 复验
  零劣化
- [x] 7.2 真冒烟:出账→门户去支付→收银台成功→账单 PAID→重复回调
  幂等→伪签 401→新支付模拟失败→再成功→取消→过期;BOT 策略
  超限 429;套餐编辑/绑定中 409;单授 billing:manage 角色配置
  全流程;分页翻页
- [x] 7.3 R1 独立评审+修复清零
- [x] 7.4 HANDOFF+挂账(真实通道接入/用户·TEAM 配额/BOT 级 CALLS/
  落库失败残余低估)+归档
