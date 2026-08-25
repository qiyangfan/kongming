# 034 developer-portal-billing 实施任务

## 1. 迁移与域包骨架

- [x] 1.1 0022 迁移(sys_quota_policy/sys_billing_plan/sys_invoice,
  金额 INTEGER 厘)+conftest 清单
- [x] 1.2 billing 域包(models[配额判定纯函数]/ports/repository/
  service/router)+import-linter 契约
- [x] 1.3 权限点种子(developer:portal 27 推荐集/system:billing:
  manage 28)+种子测试计数

## 2. 配额切面(测试先行:安全类)

- [x] 2.1 失败测试:超额 429 拒+审计/未超放行/预警头/无策略零回归/
  套餐配额生效/用量仅服务端源
- [x] 2.2 配额判定服务(sys_llm_usage 聚合+api 渠道调用计数+成本
  估算)+v1 网关前置切面

## 3. 套餐与账单(测试先行:安全类)

- [x] 3.1 失败测试:月首出账(幂等不重复)/明细模型×单价/金额整数厘/
  跨租户 404
- [x] 3.2 套餐 CRUD+租户绑套餐+出账服务(lifespan 薄壳每日检查)

## 4. 门户与配置(测试先行)

- [x] 4.1 失败测试:门户聚合仅本人租户/超管配置 CRUD
- [x] 4.2 /portal/* 端点(key 列表复用+用量看板+账单)+/admin/billing
  子页+PortalPage

## 5. 验证与收尾

- [x] 5.1 make check 全绿;make contracts;016 评估 smoke 复验;
  冒烟(配置配额→超额 429→调低→放行→月首出账→门户看板)
- [x] 5.2 R1 独立评审+修复清零
- [x] 5.3 HANDOFF+挂账(支付网关/请求内同步采集/用户·Bot 维度配额/
  账单导出)+归档
