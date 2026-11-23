# Pod QoS 类

本页介绍 Kubernetes 中的 **服务质量（Quality of Service，QoS）** 类，
阐述 Kubernetes 如何根据为 Pod 中的容器指定的资源约束为每个 Pod 设置 QoS 类。
Kubernetes 依赖这种分类来决定当 Node 上没有足够可用资源时要驱逐哪些 Pod。

## QoS 类   {#qos-class}

Kubernetes 对你运行的 Pod 进行分类，并将每个 Pod 分配到特定的 **QoS 类**中。
Kubernetes 使用这种分类来影响不同 Pod 被处理的方式。Kubernetes 基于 Pod
中容器的[资源请求](/zh-cn/docs/concepts/configuration/manage-resources-containers/)进行分类，
同时确定这些请求如何与资源限制相关。
这称为服务质量 (QoS) 类。
Kubernetes 基于每个 Pod 中容器的资源请求和限制为 Pod 设置 QoS 类。Kubernetes 使用 QoS
类来决定从遇到[节点压力](/zh-cn/docs/concepts/scheduling-eviction/node-pressure-eviction/)的
Node 中驱逐哪些 Pod。可选的 QoS 类有 `Guaranteed`、`Burstable` 和 `BestEffort`。
当一个 Node 耗尽资源时，Kubernetes 将首先驱逐在该 Node 上运行的 `BestEffort` Pod，
然后是 `Burstable` Pod，最后是 `Guaranteed` Pod。当这种驱逐是由于资源压力时，
只有超出资源请求的 Pod 才是被驱逐的候选对象。

### Guaranteed

`Guaranteed` Pod 具有最严格的资源限制，并且最不可能面临驱逐。
在这些 Pod 超过其自身的限制或者没有可以从 Node 抢占的低优先级 Pod 之前，
这些 Pod 保证不会被杀死。这些 Pod 不可以获得超出其指定 limit 的资源。这些 Pod 也可以使用
[`static`](/zh-cn/docs/tasks/administer-cluster/cpu-management-policies/#static-policy-configuration)
CPU 管理策略来使用独占的 CPU。

#### 判据   {#criteria}

Pod 被赋予 `Guaranteed` QoS 类的几个判据：

* Pod 中的每个容器必须有内存 limit 和内存 request，两者都必须大于零。
* 对于 Pod 中的每个容器，内存 limit 必须等于内存 request。
* Pod 中的每个容器必须有 CPU limit 和 CPU request，两者都必须大于零。
* 对于 Pod 中的每个容器，CPU limit 必须等于 CPU request。

如果 Pod 使用的是
[Pod 级别资源](/zh-cn/docs/concepts/configuration/manage-resources-containers/#pod-level-resource-specification)：

* Pod 必须设置 Pod 级别的内存 limit 和内存 request，并且这两个值必须相等。
* Pod 必须设置 Pod 级别的 CPU limit 和 CPU request，并且这两个值必须相等。

### Burstable

`Burstable` Pod 有一些基于 request 的资源下限保证，但不需要特定的 limit。
如果未指定 limit，则默认为其 limit 等于 Node 容量，这允许 Pod 在资源可用时灵活地增加其资源。
在由于 Node 资源压力导致 Pod 被驱逐的情况下，只有在所有 `BestEffort` Pod 被驱逐后
这些 Pod 才会被驱逐。因为 `Burstable` Pod 可以包括没有资源 limit 或资源 request 的容器，
所以 `Burstable` Pod 可以尝试使用任意数量的节点资源。

#### 判据   {#criteria-1}

Pod 被赋予 `Burstable` QoS 类的几个判据：

* Pod 不满足针对 QoS 类 `Guaranteed` 的判据。
* Pod 中至少一个容器有内存或 CPU 的 request 或 limit，或者 Pod 本身设置了 Pod 级别的内存或
  CPU 的 request 或 limit。

### BestEffort

`BestEffort` QoS 类中的 Pod 可以使用未专门分配给其他 QoS 类中的 Pod 的节点资源。
例如若你有一个节点有 16 核 CPU 可供 kubelet 使用，并且你将 4 核 CPU 分配给一个 `Guaranteed` Pod，
那么 `BestEffort` QoS 类中的 Pod 可以尝试任意使用剩余的 12 核 CPU。

如果节点遇到资源压力，kubelet 将优先驱逐 `BestEffort` Pod。

#### 判据   {#criteria-2}

如果 Pod 不满足 `Guaranteed` 或 `Burstable` 的判据，则它的 QoS 类为 `BestEffort`。
换言之，只有当 Pod 中的所有容器没有内存 limit 或内存 request，也没有 CPU limit 或
CPU request，且 Pod 本身 也没有设置任何 Pod 级别的内存或 CPU 的 limit 或 request 时，
Pod 才是 `BestEffort`。Pod 中的容器可以请求（除 CPU 或内存之外的）
其他资源并且仍然被归类为 `BestEffort`。

## 使用 cgroup v2 的内存 QoS   {#memory-qos-with-cgroup-v2}

[内存 QoS](/zh-cn/docs/reference/command-line-tools-reference/feature-gates/#memoryqos)
使用 CGroup v2 的内存控制器来管理 Kubernetes 中的内存抑制和保护。
它使用 Pod 的 QoS 类来决定应用哪些 CGroup 设置，但不会改变 Pod 的分类方式。

默认的 `kubelet` 配置未启用内存节流或内存保护。
要启用任一行为，请分别[配置](/zh-cn/docs/reference/config-api/kubelet-config.v1beta1/)
`memoryThrottlingFactor` 或 `memoryReservationPolicy`。

### 内存抑制

内存节流由 `kubelet` 配置字段 `memoryThrottlingFactor` 控制。
其默认值为 `nil`，这意味着 `kubelet` 不会设置 `memory.high`。
要启用内存节流，请将 `memoryThrottlingFactor` 设置为大于 0 且小于或等于 1 的值。

对于 Burstable 级别的 Pod，kubelet 使用 `memory.high`
来在工作负载达到其硬限制（`memory.max`）之前节流内存分配。
容器的抑制阈值的计算方式为：

```
memory.high = requests + memoryThrottlingFactor * (limits - requests)
```

例如，将 `memoryThrottlingFactor` 设置为 `0.9` 时，一个 `request`
为 `256 MiB`、`limit` 为 `1 GiB` 的容器，其 `memory.high` 会被设置为约 `947 MiB`。
如果一个 Burstable Pod 没有内存限制，kubelet 会使用节点可分配内存来代替该限制。

对于 BestEffort 级别的 Pod（没有内存 request 或 limit），kubelet 会以零作为
request、以节点可分配内存代替 limit 来计算 `memory.high`。
Guaranteed 级别的 Pod 不会被设置 `memory.high`，因为其 request 等于 limit。

### 配置内存预留

内存预留通过 kubelet 配置字段 `memoryReservationPolicy` 进行控制：

- `None` （默认）：kubelet 不为容器和 Pod 设置 `memory.min` 或 `memory.low`。内核不会硬锁定任何内存。
- `TieredReservation`：kubelet 根据 Pod 的 QoS 类设置分层内存保护：
  - **Guaranteed** Pod：设置 `memory.min` 为内存请求值。内核在任何情况下都不会回收此内存。
  - **Burstable** Pod：设置 `memory.low` 为内存请求值。内核优先保留此内存，但在极端压力下可能会回收它。
  - **BestEffort** Pod：不设置内存保护。

例如，以下 kubelet 配置启用了系数为 `0.9` 的内存限流以及分层内存保护：

```yaml
apiVersion: kubelet.config.k8s.io/v1beta1
kind: KubeletConfiguration
memoryThrottlingFactor: 0.9
memoryReservationPolicy: TieredReservation
```

你可以独立配置任一字段。省略 `memoryThrottlingFactor` 可使 kubelet 不设置
`memory.high`，或设置 `memoryReservationPolicy: None` 以禁用内存保护。

由于 CGroup v2 的内存保护是分层的，kubelet 还会配置祖先 CGroup。它在 kubepods 根
CGroup 上将 `memory.min` 设置为 Guaranteed 和 Burstable Pod 的内存 requests 之和；
在 kubepods 根 CGroup 和 Burstable QoS CGroup 上将 `memory.low` 设置为 Burstable Pod
的内存 requests 之和。如果没有这种祖先覆盖，按 Pod 和按容器的保护将无法生效。

对于 Guaranteed Pod，其内存 requests 等于 limits。
在 `memoryReservationPolicy: TieredReservation` 下，`memory.min` 因此等于 `memory.max`。
对于使用大量页缓存的工作负载，内核可能无法在 cgroup 达到 `memory.max`
之前回收足够的页缓存，从而导致 OOM 终止。请调整内存 limit 的大小，为页缓存预留足够的缓冲空间。

### 禁用或回滚 Memory QoS

要禁用 Memory QoS，请将 `MemoryQoS` 特性门控设置为 `false`，确保 `memoryReservationPolicy`
未设置或设置为 `None`，然后重启 kubelet。启动时，kubelet 会将 kubepods 根 CGroup 上的
`memory.min` 和 `memory.low`、以及 Burstable QoS CGroup 上的 `memory.low` 重置为零。
Pod 级和容器级的 `memory.min`、`memory.low` 值可以保留，但由于对应的祖先保护为零，这些值不起作用。

当 Memory QoS 禁用时，每当容器运行时将资源配置应用到新的、重启的或原地调整大小的容器时，
kubelet 会将 `memory.high` 设置为 `max`。对于已经在运行但未被重启或调整大小的容器，
其之前的 `memory.high` 值可以保留，直到下次重启或原地调整大小为止。

### 系统要求

Memory QoS 需要使用 CGroup v2 的 Linux 系统。
推荐使用 5.9 或更高版本的内核，因为在旧版本的内核上，
`memory.high` 节流可能会触发一个已知的
[livelock bug](https://lore.kernel.org/all/20200710012662.GA29679@chep.ntu.edu.tw/)。
如果在较旧的内核上启用了 `MemoryQoS` 特性门控，kubelet 在启动时会记录一条警告日志。

## 某些行为独立于 QoS 类 {#class-independent-behavior}

某些行为独立于 Kubernetes 分配的 QoS 类。例如：

* 所有超过资源 limit 的容器都将被 kubelet 杀死并重启，而不会影响该 Pod 中的其他容器。
* 如果一个容器超出了自身的资源 request，且该容器运行的节点面临资源压力，则该容器所在的 Pod
  就会成为被[驱逐](/zh-cn/docs/concepts/scheduling-eviction/node-pressure-eviction/)的候选对象。
  如果出现这种情况，Pod 中的所有容器都将被终止。Kubernetes 通常会在不同的节点上创建一个替代的 Pod。

* Pod 的资源 request 等于其成员容器的资源 request 之和，Pod 的资源 limit 等于其成员容器的资源 limit 之和。
* kube-scheduler 在选择要[抢占](/zh-cn/docs/concepts/scheduling-eviction/pod-priority-preemption/#preemption)的
  Pod 时不考虑 QoS 类。当集群没有足够的资源来运行你所定义的所有 Pod 时，就会发生抢占。

* QoS 类在 Pod 创建时确定，并在 Pod 的整个生命周期内保持不变。
  如果你之后尝试进行一次[原地资源调整](/zh-cn/docs/concepts/workloads/pods/pod-lifecycle/#pod-resize)，
  且该调整会导致 QoS 类发生变化，则该调整请求会在准入阶段被拒绝。

## 

* 进一步了解[为 Pod 和容器管理资源](/zh-cn/docs/concepts/configuration/manage-resources-containers/)。
* 进一步了解[节点压力驱逐](/zh-cn/docs/concepts/scheduling-eviction/node-pressure-eviction/)。
* 进一步了解 [Pod 优先级和抢占](/zh-cn/docs/concepts/scheduling-eviction/pod-priority-preemption/)。
* 进一步了解 [Pod 干扰](/zh-cn/docs/concepts/workloads/pods/disruptions/)。
* 进一步了解如何[为容器和 Pod 分配内存资源](/zh-cn/docs/tasks/configure-pod-container/assign-memory-resource/)。
* 进一步了解如何[为容器和 Pod 分配 CPU 资源](/zh-cn/docs/tasks/configure-pod-container/assign-cpu-resource/)。
* 进一步了解如何[配置 Pod 的服务质量](/zh-cn/docs/tasks/configure-pod-container/quality-service-pod/)。
