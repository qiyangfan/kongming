# Proposal: 2026-11-26-add-kongming-control-snapshot-consumer（控制快照消费者（control-snapshot-consumer））

## Why

kongming 的租户/成员/粗角色/权益/共享策略包/目录必须改由锦官拥有；当前共享控制数据在本地，服务无法与锦官收敛。服务域配置与细粒度权限仍本地。

## What Changes

- 新增控制快照拉取、签名校验、缓存与版本收敛。
- 按能力投影租户/成员/粗角色/权益/共享策略包/目录。
- 服务身份认证；锦官缺席时 last-known；安全类 fail-closed。

## Non-goals

- 不把控制数据写回控制面。
- 不做控制面 UI。
- 不做服务运行时代理。

## Capabilities

### New Capabilities

- `control-snapshot-consumer`：控制快照拉取、缓存与 last-known 语义

### Modified Capabilities

无

## Impact

apps/api 新增快照客户端/缓存/启动逻辑；控制面只读；不写服务库。
