# Design: 共享控制快照消费者（control-snapshot-consumer）

## Context

当前 kongming 租户/成员/粗角色/套餐/共享策略/商店目录都在本地；没有共享控制快照概念。服务域配置与细权限仍本地。

## Goals / Non-Goals

**Goals:**
- 共享控制来自锦官。
- 锦官缺席仍可 last-known 启动。
- 服务身份/验签。

**Non-Goals:**
- 不编辑共享控制。
- 不下发服务域配置。
- 不做实时推送。

## Decisions

1. 快照客户端按服务投影拉取，缓存到本地受控存储；版本单调。
2. 服务身份 + 签名校验；匿名/未签名拒绝。
3. 安全类快照缺失/非法 fail-closed；非安全类 last-known + stale。
4. 启动不依赖锦官；后台刷新。
5. 服务域配置/权限码不从快照读取；本地 admin API 写，锦官只读展示。

## Risks / Trade-offs

- 快照陈旧 → observed_at/stale；安全类拒绝。
- 本地缓存损坏 → 从锦官重建；无法重建则 fail-closed。
- 签名密钥轮换 → JWKS 缓存。

## Migration Plan

greenfield：删除本地共享控制数据；新增快照缓存；服务域配置保留。
