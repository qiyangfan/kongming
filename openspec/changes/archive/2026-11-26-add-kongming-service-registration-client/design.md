# Design: 服务注册客户端（service-registration-client）

## Context

锦官 service-registry 已定义接收面；kongming 缺少注册/心跳客户端，控制台无法获得实例、能力、版本和健康。注册是 push 契约，与快照 pull 契约分离。

## Goals / Non-Goals

**Goals:**
- 服务身份注册/心跳/能力/版本上报。
- 锦官缺席不阻塞启动与核心流程。
- 幂等、退避重试、恢复补报。

**Non-Goals:**
- 不做服务发现/负载均衡。
- 不做控制面 UI。
- 不写共享控制数据。
- 不把注册当健康伪造。

## Decisions

1. 注册/心跳使用服务身份 + TLS；payload 含 service_id/instance_id/version/capabilities/admin API 能力/权益键。
2. 启动异步注册，后台周期心跳；失败指数退避，锦官恢复后补报；不阻塞核心流程。
3. instance_id + 幂等键去重；重复上报只更新 last_seen/能力，不创建重复实例。
4. 能力与版本来自代码/配置单一来源；能力键与锦官 service-registry 契约一致。
5. 不上报凭据、连接串、服务域配置或服务只读 API endpoint；服务地址由锦官静态配置；注册失败不改变本地配置。
6. 注册客户端与服务身份/快照客户端共用认证配置，但不合并 push/pull 语义。

## Risks / Trade-offs

- 心跳丢失 → 控制面 stale，不自动删；服务不停机。
- 能力漂移 → 契约测试；发布版本与能力同源。
- 重复实例 → instance_id 幂等/租约；控制面按 last_seen 收敛。
- 控制面缺席 → 本地缓冲/重试；不把注册失败当致命。

## Migration Plan

greenfield：新增客户端与启动接线；无数据迁移；服务域配置/运行时保留。
