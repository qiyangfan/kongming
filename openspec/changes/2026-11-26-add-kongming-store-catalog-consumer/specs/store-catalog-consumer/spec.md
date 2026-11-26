# Spec Delta

## Purpose

孔明的商店目录消费者：从控制面拉取目录、权益与可见性快照，驱动货架展示与运行前权益校验，Bot 定义、版本与运行仍归 kongming。

## ADDED Requirements

### Requirement: 目录与权益消费

kongming SHALL 从控制面目录/权益快照驱动货架展示；运行前 SHALL 校验租户/团队权益；目录缺失/非法 SHALL 不展示或拒绝运行，不伪造内容；Bot 定义/版本仍本地。

#### Scenario: 货架展示

- WHEN 租户打开商店
- THEN 只展示其权益内目录条目

#### Scenario: 权益拒绝

- GIVEN 租户无权益
- WHEN 运行条目
- THEN 拒绝，审计

### Requirement: 目录控制集中

目录/权益/运营标记 SHALL 只由控制面写；kongming SHALL NOT 提供本地目录编辑；运行时控制台 SHALL 只读目录；Bot 定义/版本编辑仍属产品面。

#### Scenario: 本地编辑被拒

- WHEN 调用本地目录编辑
- THEN 端点不存在或拒绝

#### Scenario: 定义仍可编辑

- GIVEN Bot 创建者
- WHEN 编辑定义/版本
- THEN 允许，目录引用不变
