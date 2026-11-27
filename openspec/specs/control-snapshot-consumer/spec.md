# control-snapshot-consumer Specification

## Purpose

孔明的共享控制快照消费者：以服务身份从锦官拉取并验签租户/成员/粗角色/权益/共享策略包/目录快照，本地缓存 last-known 并按版本收敛；服务域配置不在此快照。

## Requirements

### Requirement: 控制快照消费

kongming SHALL 以服务身份拉取控制快照并验签，缓存 last-known；快照 SHALL 带版本/ETag/observed_at；旧版本 SHALL NOT 覆盖新版本；匿名/未签名快照 SHALL 拒绝；锦官不可用 SHALL NOT 阻止启动；快照 SHALL NOT 含服务域配置/权限码。

#### Scenario: 正常拉取

- WHEN 锦官带 ETag 返回签名有效的新快照
- THEN 本地缓存更新，版本递增

#### Scenario: 控制面缺席

- GIVEN 锦官不可用
- WHEN kongming 启动
- THEN 用 last-known 启动，标 stale

#### Scenario: 签名失败

- GIVEN 快照签名无效
- WHEN 应用
- THEN 拒绝，保留 last-known

#### Scenario: 服务域配置不越界

- WHEN 检查快照
- THEN 无数据源/RAG/工具/key/服务权限码字段

### Requirement: 安全类 fail-closed

租户、RBAC、访问策略等安全类快照缺失或非法时，kongming SHALL 拒绝新的受保护操作；非安全类 SHALL 使用 last-known 并标记陈旧；快照 SHALL NOT 含凭据。

#### Scenario: 安全快照缺失

- GIVEN 无有效租户/RBAC 快照
- WHEN 新请求到达
- THEN 拒绝，不绕过隔离

#### Scenario: 非安全陈旧

- GIVEN 价目/目录快照陈旧
- WHEN 展示/执行
- THEN 使用 last-known 并标 stale
