# runtime-policy Specification

## Purpose

孔明的运行时策略执行：以锦官共享策略包快照为准执行注入拦截、PII 脱敏、输出防护与工具审核；本地保留服务域规则体/规则类型与执行实现。

## Requirements

### Requirement: 策略包快照执行

kongming SHALL 按锦官共享策略包快照执行护栏/访问策略，并在本地规则目录解析包引用；拒绝/脱敏 SHALL 审计；安全类策略缺失或非法 SHALL fail-closed；本地 SHALL NOT 编辑共享策略包激活/作用域。

#### Scenario: 拦截

- GIVEN 策略包命中注入
- WHEN 请求到达
- THEN 拒绝并审计

#### Scenario: 脱敏

- GIVEN 策略包命中 PII
- WHEN 输出生成
- THEN 脱敏后返回，审计

### Requirement: 共享激活集中、规则体本地

共享策略包激活/作用域/版本 SHALL 只在锦官；kongming SHALL 保留服务域规则体/规则类型编辑入口；运行时控制台 SHALL 只读共享激活、可写本地规则；策略内容 SHALL NOT 含凭据。

#### Scenario: 本地改共享激活被拒

- WHEN 调用本地共享策略包激活
- THEN 端点不存在或拒绝

#### Scenario: 本地规则仍可编辑

- WHEN ADMIN 编辑本地护栏规则体
- THEN 允许，服务落库并审计；锦官只读展示

#### Scenario: 控制台分离

- WHEN 查看策略页
- THEN 共享激活只读，本地规则可写
