# Pod 组干扰和优先级

PodGroup 可以声明一个干扰模式。此模式规定了调度器如何干扰运行中的 PodGroup，
例如为了容纳更高优先级的 PodGroup。PodGroup 还具有优先级，
该优先级会在[工作负载感知抢占](/zh-cn/docs/concepts/scheduling-eviction/workload-aware-preemption/)
事件中覆盖来自该组的单个 Pod 的优先级。

## 干扰模式类型   {#disruption-mode-types}

截至 1.36 版本，PodGroup 的 `priority` 或 `disruptionMode`
字段仅被[工作负载感知抢占](/zh-cn/docs/concepts/scheduling-eviction/workload-aware-preemption/)所尊重。
在 Pod 调度阶段，调度器不考虑 PodGroup 的 `priority` 或 `disruptionMode` 字段。

API 支持两种干扰模式：`Pod` 和 `PodGroup`。默认模式是 `Pod`。

### Pod

`Pod` 模式指示调度器将组中的所有 Pod 视为单独的实体，
允许从 PodGroup 中独立干扰单个 Pod。

### PodGroup

`PodGroup` 模式强调干扰的 "全有或全无" 语义。
它指示调度器必须同时干扰 PodGroup 中的所有 Pod。

## Pod 组优先级   {#pod-group-priority}

PodGroup 使用与单个 Pod 相同的 [PriorityClass](/zh-cn/docs/concepts/scheduling-eviction/pod-priority-preemption/#priorityclass) 概念。
创建一个或多个 PriorityClass 后，你可以创建一个 PodGroup，
并在其规约中指定这些 PriorityClass 名称之一。
优先级准入控制器使用 `priorityClassName` 字段并填充优先级的整数值。
如果未找到优先级类，则 PodGroup 会被拒绝。
当 PodGroup 未设置 `priorityClassName` 时，Kubernetes 会寻找默认值（`globalDefault` 设置为 true 的 PriorityClass）。
如果没有 `globalDefault` 设置为 true 的 PriorityClass，
则没有 `priorityClassName` 的 PodGroup 优先级为零。

PodGroup 的优先级是该组中所有 Pod 在
[工作负载感知抢占](/zh-cn/docs/concepts/scheduling-eviction/workload-aware-preemption/)
事件中的权威优先级，
即使组成该 PodGroup 的各个 Pod 的优先级不同也是如此。

以下 YAML 是使用 `high-priority` PriorityClass 的 PodGroup 配置示例，
该 PriorityClass 映射到整数值 1000000 的优先级。
优先级准入控制器检查规约并将 PodGroup 的优先级解析为 1000000。

```yaml
apiVersion: scheduling.k8s.io/v1alpha2
kind: PodGroup
metadata:
  namespace: ns-1
  name: job-1
spec:
  priorityClassName: high-priority
```

## 

* 阅读有关[工作负载感知抢占](/zh-cn/docs/concepts/scheduling-eviction/workload-aware-preemption/)算法的信息。
* 了解[工作负载 API](/zh-cn/docs/concepts/workloads/workload-api/)。
