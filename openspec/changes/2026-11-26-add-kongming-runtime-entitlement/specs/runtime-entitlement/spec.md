# Spec Delta

## Purpose

孔明的运行时权益执行：以锦官套餐、配额与权益快照为准执行并上报产品事件；本地不再拥有计划、账单、发票、支付与开发者门户控制面；LLM 用量只由 muniu 上报。

## ADDED Requirements

### Requirement: 权益与配额执行

kongming SHALL 按锦官权益/配额快照执行；超限 SHALL 拒绝并审计；缺失/非法安全类权益 SHALL fail-closed；本地 SHALL NOT 保存计划/账单/发票/支付状态；billing 控制面归锦官。

#### Scenario: 超限拒绝

- GIVEN 租户配额耗尽
- WHEN 新请求
- THEN 429，审计记录

#### Scenario: 本地无计费状态

- WHEN 检查本地表/端点
- THEN 无计划/账单/支付控制数据

### Requirement: 产品事件上报

kongming SHALL 将租户/用户/Bot 维度的产品事件推送到锦官；事件 SHALL 幂等并支持断网缓冲；锦官不可用 SHALL NOT 阻断本地执行；SHALL NOT 上报 LLM token/费用（由 muniu 唯一上报）；上报内容 SHALL NOT 含凭据。

#### Scenario: 正常上报

- WHEN 一轮问答完成
- THEN 产品事件发送，锦官聚合

#### Scenario: 不重复 LLM

- WHEN 产生 LLM 调用
- THEN 只记录本地引用，不上报 token/费用

#### Scenario: 控制面不可用

- GIVEN 锦官宕机
- WHEN 产生产品事件
- THEN 本地缓冲，恢复后补发
