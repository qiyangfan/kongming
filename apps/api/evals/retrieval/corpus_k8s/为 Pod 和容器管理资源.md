# 为 Pod 和容器管理资源

当你定义 Pod
时可以选择性地为每个容器设定所需要的资源数量。
最常见的可设定资源是 CPU 和内存（RAM）大小；此外还有其他类型的资源。

当你为 Pod 中的 Container 指定了资源 **requests（请求）** 时，
kube-scheduler
会利用该信息决定将 Pod 调度到哪个节点上。
当你为 Container 指定了资源 **limits（限制）** 时，kubelet
可以确保运行的容器不会使用超出所设限制的资源。
kubelet 还会为容器预留 **requests（请求）** 所指定数量的系统资源，供其使用。

## 请求和限制  {#requests-and-limits}

如果 Pod 运行所在的节点具有足够的可用资源，容器可能（且可以）使用超出对应资源
`requests` 属性所设置的资源量。

例如，如果你将容器的 `memory` 的请求量设置为 256 MiB，而该容器所处的 Pod
被调度到一个具有 8 GiB 内存的节点上，并且该节点上没有其他 Pod 运行，
那么该容器就可以尝试使用更多的内存。

限制是另一个话题。`cpu` 限制和 `memory` 限制都由 kubelet
（以及容器运行时）来实施，
最终由内核强制执行。在 Linux 节点上，Linux 内核通过
CGroup 来强制执行限制。
`cpu` 限制和 `memory` 限制的执行行为略有不同。

`cpu` 限制通过 CPU 节流机制（CPU Throttling）来强制执行。
当某容器接近其 `cpu` 限制值时，内核会基于容器的限制值来限制其对 CPU 的访问。
因此，`cpu` 限制是内核强制执行的一个硬性限制。
容器不得使用超出其 `cpu` 限制所指定的 CPU 核数。

`memory` 限制由内核使用 OOM（内存不足）杀死机制来强制执行。
当某容器使用的内存超过其 `memory` 限制时，内核可以终止此容器。
然而，终止操作只会在内核检测到内存压力时才会发生。
因此，内存分配过量的容器可能不会被立即终止。
这意味着 `memory` 限制是被动执行的。
某容器可以使用超过其 `memory` 限制的内存，但如果这样做了，它可能会被终止。

有一个 `MemoryQoS` 特性，它在使用 CGroup v2 的 Linux 节点上添加内存节流以及可选的分层内存预留功能。
详细信息参阅[使用 CGroup v2 的内存 QoS](/zh-cn/docs/concepts/workloads/pods/pod-qos/#memory-qos-with-cgroup-v2)。

如果你为某个资源指定了限制值，但不指定请求值，
并且没有应用某种准入时机制为该资源设置默认请求值，
那么 Kubernetes 会复制你所指定的限制值，将其用作资源的请求值。

## 资源类型  {#resource-types}

资源类型具有基本单位，并且可以被请求、被限制或两者兼具。
Kubernetes 内置了以下资源类型：

| 资源类型 | 描述 | 基本单位 |
|---|---|---|
| `cpu` | 计算处理 | CPU（核心）|
| `memory` | RAM | 字节 |
| `ephemeral-storage` | [本地临时存储](/zh-cn/docs/concepts/storage/ephemeral-storage/) | 字节 |
| `hugepages-<size>` | [巨页](#huge-pages)（仅限 Linux） | 字节 |

集群还可以提供[扩展资源](/zh-cn/docs/concepts/configuration/manage-resources-containers/#extended-resources)
（具有自定义名称的资源，通常由设备插件暴露）。

### 内存巨页

对于 Linux 工作负载，你可以指定内存**巨页**资源。
巨页是 Linux 特有的功能，其中节点内核分配的内存块远大于默认页面大小。

例如，在默认页面大小为 4KiB 的系统上，你可以指定限制 `hugepages-2Mi: 80Mi`。
如果容器尝试分配 40 个 2MiB 大小的巨页（总共 80MiB ），则分配请求会失败。

你不能过量使用 `hugepages- *` 资源。
这与 `memory` 和 `cpu` 资源不同。

CPU 和内存统称为**计算资源**，或简称为**资源**。
计算资源的数量是可测量的，可以被请求、被分配、被消耗。
它们与 [API 资源](/zh-cn/docs/concepts/overview/kubernetes-api/)不同。
API 资源（如 Pod 和 [Service](/zh-cn/docs/concepts/services-networking/service/)）是可通过
Kubernetes API 服务器读取和修改的对象。

## Pod 和容器的资源请求和限制  {#resource-requests-and-limits-of-pod-and-container}

针对每个容器，你都可以指定其资源限制和请求，包括如下选项：

* `spec.containers[].resources.limits.cpu`
* `spec.containers[].resources.limits.memory`
* `spec.containers[].resources.limits.ephemeral-storage`
* `spec.containers[].resources.limits.hugepages-<size>`
* `spec.containers[].resources.requests.cpu`
* `spec.containers[].resources.requests.memory`
* `spec.containers[].resources.requests.ephemeral-storage`
* `spec.containers[].resources.requests.hugepages-<size>`

尽管你只能逐个容器地指定请求和限制值，但考虑 Pod 的总体资源请求和限制也是有用的。
对特定资源而言，**Pod 的资源请求/限制值**是 Pod 中各容器对该类型资源的请求/限制值的总和。

## Pod 级资源规约

如果你的集群启用了 `PodLevelResources`
[特性门控](/zh-cn/docs/reference/command-line-tools-reference/feature-gates/)，
你可以在 Pod 级别指定资源请求和限制值。
在 Pod 级别，Kubernetes 
仅支持为特定资源类型设置资源请求或限制值，
具体包括 `cpu` 和/或 `memory` 和/或 `hugepages`。
启用此特性时，Kubernetes 允许你为 Pod 声明一个资源总预算，
这在处理大量容器时特别有用，因为在这种情况下很难准确评估各个容器的资源需求。
此外，这一特性还允许 Pod 内的容器之间共享空闲资源，从而提高资源利用率。

对于一个 Pod，你可以通过包含以下内容来指定 CPU 和内存的资源限制和请求：

* `spec.resources.limits.cpu`
* `spec.resources.limits.memory`
* `spec.resources.limits.hugepages-<size>`
* `spec.resources.requests.cpu`
* `spec.resources.requests.memory`
* `spec.resources.requests.hugepages-<size>`

## Kubernetes 中的资源单位  {#resource-units-in-kubernetes}

### CPU 资源单位    {#meaning-of-cpu}

CPU 资源的限制和请求以 **cpu** 为单位。
在 Kubernetes 中，一个 CPU 等于 **1 个物理 CPU 核**或者 **1 个虚拟核**，
取决于节点是一台物理主机还是运行在某物理主机上的虚拟机。

你也可以表达带小数 CPU 的请求。
当你定义一个容器，将其 `spec.containers[].resources.requests.cpu` 设置为 `0.5` 时，
你所请求的 CPU 是你请求 `1.0` CPU 时的一半。对于 CPU 资源单位，
[数量](/zh-cn/docs/reference/kubernetes-api/common-definitions/quantity/)表达式 `0.1`
等价于表达式 `100m`，可以看作 “100 millicpu”。
有些人说成是“一百毫核”，其实说的是同样的事情。

CPU 资源总是设置为资源的绝对数量而非相对数量值。
例如，无论容器运行在单核、双核或者 48 核的机器上，`500m` CPU
表示的是大约相同的算力。

Kubernetes 不允许设置精度小于 `1m` 或 `0.001` 的 CPU 资源。
为了避免意外使用无效的 CPU 数量，当使用少于 1 个 CPU 单元时，使用
milliCPU 形式而不是十进制形式指定 CPU 单元非常有用。

例如，你有一个使用 `5m` 或 `0.005` 核 CPU 的 Pod，并且希望减少其 CPU 资源。
通过使用十进制形式，更难发现 `0.0005` CPU 是无效值，而通过使用 milliCPU 形式，
更容易发现 `0.5m` 是无效值。

### 内存资源单位      {#meaning-of-memory}

`memory` 的限制和请求以字节为单位。你可以使用普通的整数，
或者带有以下[数量](/zh-cn/docs/reference/kubernetes-api/common-definitions/quantity/)后缀的定点数字来表示内存：
E、P、T、G、M、k。你也可以使用对应的 2 的幂数：Ei、Pi、Ti、Gi、Mi、Ki。
Kubernetes API 也允许使用 m 作为后缀（表示毫字节：1/1000 字节），
但这并不实用：你必须始终分配整数个字节，或者有时是更大的块，例如 1 GiB 的倍数。

以下是一些表示近似相同值的内存数量示例：

```shell
128974848, 129e6, 129M,  128974848000m, 123Mi
```

请注意后缀的大小写。如果你请求 `400m` 临时存储，实际上所请求的是 0.4 字节。
"M" 代表兆字节，"m" 代表毫字节。如果你请求 "400m" 的内存，则表示请求 0.4 字节。
如果有人这样设定资源请求或限制，可能他的实际想法是申请 400Mi 字节（`400Mi`）
或者 400M 字节。

## 容器资源示例     {#example-1}

以下 Pod 有两个容器。每个容器的请求为 0.25 CPU 和 64MiB（2<sup>26</sup> 字节）内存，
每个容器的资源限制为 0.5 CPU 和 128MiB 内存。
你可以认为该 Pod 的资源请求为 0.5 CPU 和 128MiB 内存，资源限制为 1 CPU 和 256MiB 内存。

```yaml
---
apiVersion: v1
kind: Pod
metadata:
  name: frontend
spec:
  containers:
  - name: app
    image: images.my-company.example/app:v4
    resources:
      requests:
        memory: "64Mi"
        cpu: "250m"
      limits:
        memory: "128Mi"
        cpu: "500m"
  - name: log-aggregator
    image: images.my-company.example/log-aggregator:v6
    resources:
      requests:
        memory: "64Mi"
        cpu: "250m"
      limits:
        memory: "128Mi"
        cpu: "500m"
```

## Pod 资源示例 {#example-2}

此特性可以通过设置 `PodLevelResources`
[特性门控](/zh-cn/docs/reference/command-line-tools-reference/feature-gates)来启用。
以下 Pod 明确请求了 1 个 CPU 和 100 MiB 的内存，并设置了明确的限制值为 1 个 CPU 和 200 MiB 的内存。
`pod-resources-demo-ctr-1` 容器设置了明确的资源请求和限制值。不过 `pod-resources-demo-ctr-2`
容器没有设置明确的资源请求和限制，因此它将共享 Pod 资源边界内的可用资源。

## 带资源请求的 Pod 如何调度  {#how-pods-with-resource-limits-are-run}

当你创建一个 Pod 时，Kubernetes 调度程序将为 Pod 选择一个节点。
每个节点对每种资源类型都有一个容量上限：可为 Pod 提供的 CPU 和内存量。
调度程序确保对于每种资源类型，所调度的容器的资源请求的总和小于节点的容量。
请注意，尽管节点上的实际内存或 CPU 资源使用量非常低，如果容量检查失败，
调度程序仍会拒绝将 Pod 放置在该节点上。
当稍后节点上资源用量增加，例如到达请求率的每日峰值区间时，节点上也不会出现资源不足的问题。

## Kubernetes 处理资源请求与限制的方式 {#how-pods-with-resource-limits-are-run}

当 kubelet 将容器作为 Pod 的一部分启动时，它会将容器的 CPU
和内存请求与限制值信息传递给容器运行时。

在 Linux 系统上，容器运行时通常会配置内核
CGroup，负责应用并实施所定义的请求。

- CPU 限制定义的是容器可使用的 CPU 时间的硬性上限。
  在每个调度周期（时间片）期间，Linux 内核检查是否已经超出该限制；
  内核在允许该 CGroup 恢复执行之前会等待。

- CPU 请求值定义的是一个权重值。如果若干不同的容器（CGroup）需要在一个共享的系统上竞争运行，
  CPU 请求值大的负载会获得比请求值小的负载更多的 CPU 时间。

- 内存请求值主要用于（Kubernetes）Pod 调度期间。在一个启用了 CGroup v2 的节点上，
  容器运行时可能会使用内存请求值作为设置 `memory.min` 和 `memory.low` 的提示值。

- 内存限制定义的是 CGroup 的内存限制。如果容器尝试分配的内存量超出限制，
  则 Linux 内核的 out-of-memory （内存不足）子系统会被激活，并停止尝试分配内存的容器中的某个进程。
  如果该进程在容器中 PID 为 1，而容器被标记为可重新启动，则 Kubernetes
  会重新启动该容器。

- Pod 或容器的内存限制也适用于以内存为介质的卷，例如 `emptyDir` 卷。
  kubelet 会跟踪 `tmpfs` 形式的 `emptyDir` 卷用量，将其作为容器的内存用量，
  而不是[临时存储](/zh-cn/docs/concepts/storage/ephemeral-storage/)用量。
  当使用内存作为介质的 `emptyDir` 时，
  请务必查看[下面](#memory-backed-emptydir)的注意事项。

如果某容器内存用量超过其内存请求值并且所在节点内存不足时，容器所处的 Pod
可能被驱逐。

每个容器可能被允许也可能不被允许使用超过其 CPU 限制值的处理时间。
但是，容器运行时不会由于 CPU 使用率过高而杀死 Pod 或容器。

要确定某容器是否会由于资源限制而无法调度或被杀死，请参阅[问题诊断](#troubleshooting)节。

### 调整容器资源

创建 Pod 后，你可能需要根据实际使用情况调整其 CPU 或内存资源。Kubernetes
提供了两种调整 Pod 资源大小的方法：

#### 就地调整   {#pod-resize-inplace}

你无需重新创建 Pod 即可修改正在运行的 Pod 中容器的 CPU 和内存请求及限制。
这称为 "Pod 就地垂直扩容"或 "Pod 就地调整大小"。
要执行就地调整大小，请使用 Pod 的 `/resize` 子资源更新容器的资源规约。
你可以通过设置容器规约中的 `resizePolicy` 字段来控制是否需要重启容器。

目前，就地调整大小仅适用于容器级别的资源。要调整 Pod 级别的资源大小，
请参阅[调整 Pod CPU 和内存资源](/zh-cn/docs/tasks/configure-pod-container/resize-pod-resources/)。

当启用 `InPlacePodVerticalScalingSchedulerPreemption`
特性门控（Feature Gate）时，被延后的就地资源调整请求可以触发
`kube-scheduler` 在已分配的节点上抢占优先级较低的 Pod，以为资源调整腾出空间。
更多细节请参见[就地 Pod 资源调整的抢占](/zh-cn/docs/concepts/scheduling-eviction/pod-priority-preemption/#preemption-for-in-place-pod-resize)。

#### 通过启动替换 Pod 来调整 Pod 的资源

云原生方式更改 Pod 资源的方法是更新工作负载对象（例如 Deployment 或 StatefulSet）中的
Pod 模板，并让工作负载控制器将旧 Pod 替换为具有更新资源的新 Pod。
此方法适用于任何 Kubernetes 版本，并且可以更改任何 Pod 规约。

有关 Pod 调整大小的更多详细信息，
请参阅[调整 Pod 大小](/zh-cn/docs/concepts/workloads/pods/pod-lifecycle/#pod-resize)。
有关就地调整大小的详细说明，
请参阅[调整分配给容器的 CPU 和内存资源](/zh-cn/docs/tasks/configure-pod-container/resize-container-resources/)。
你还可以使用[垂直 Pod 自动扩缩器](/zh-cn/docs/concepts/workloads/autoscaling/vertical-pod-autoscale/)来自动管理
Pod 资源建议。

### 监控计算和内存资源用量  {#monitoring-compute-memory-resource-usage}

kubelet 会将 Pod 的资源使用情况作为 Pod
[`status`](/zh-cn/docs/concepts/overview/working-with-objects/#object-spec-and-status)
的一部分来报告的。

如果为集群配置了可选的[监控工具](/zh-cn/docs/tasks/debug/debug-cluster/resource-usage-monitoring/)，
则可以直接从[指标 API](/zh-cn/docs/tasks/debug/debug-cluster/resource-metrics-pipeline/#metrics-api)
或者监控工具获得 Pod 的资源使用情况。

### 使用内存作为介质的 `emptyDir` 卷的注意事项 {#memory-backed-emptydir}

当启用 `InPlacePodVerticalScalingMemoryBackedVolumes`
特性门控时，你可以在运行中的 Pod 里动态调整基于内存的（`medium: Memory`）`emptyDir`
卷的 `sizeLimit`，而无需重建 Pod 或重启容器。有关分步说明，
请参见[调整分配给容器的 CPU 和内存资源](/zh-cn/docs/tasks/onfigure-pod-container/resize-container-resources/#resizing-memory-backed-emptydir-volumes)。

如果你没有为 `emptyDir` 卷指定 `sizeLimit`，该卷就会消耗 Pod 的内存，
卷的用量上限为 Pod 的内存限制（`Pod.spec.containers[].resources.limits.memory`）。
如果你没有设置内存限制，Pod 的内存消耗将没有上限，并且可能会用掉节点上的所有可用内存。
Kubernetes 基于资源请求（`Pod.spec.containers[].resources.requests`）调度 Pod，
并且在决定另一个 Pod 是否适合调度到某个给定的节点上时，不会考虑超出请求的内存用量。
这可能导致拒绝服务，并使得操作系统需要处理内存不足（OOM）的情况。
用户可以创建任意数量的 `emptyDir`，可能会消耗节点上的所有可用内存，使得 OOM 更有可能发生。

从内存管理的角度来看，进程使用内存作为工作区与使用内存作为 `emptyDir` 的介质有一些相似之处。
但当将内存用作存储卷（例如内存为介质的 `emptyDir` 卷）时，你需要额外注意以下几点：

* 存储在内存为介质的卷上的文件几乎完全由用户应用所管理。
  与用作进程工作区的用法不同，你无法依赖语言级别垃圾回收这类机制。
* 将文件写入某个卷的目的是保存数据或在应用之间传递数据。
  Kubernetes 或操作系统都不会自动从卷中删除文件，
  因此当系统或 Pod 面临内存压力时，将无法回收这些文件所使用的内存。
* 以内存为介质的 `emptyDir` 因性能较好而很有用，但内存通常比其他存储介质（如磁盘或 SSD）小得多且成本更高。
  为 `emptyDir` 卷使用大量内存可能会影响 Pod 或整个节点的正常运行，因此你应谨慎使用。

如果你在管理集群或命名空间，还可以设置限制内存使用的
[ResourceQuota](/zh-cn/docs/concepts/policy/resource-quotas/)；
你可能还希望定义一个 [LimitRange](/zh-cn/docs/concepts/policy/limit-range/)
以施加额外的限制。如果为每个 Pod 指定 `spec.containers[].resources.limits.memory`，
那么 `emptyDir` 卷的最大尺寸将是 Pod 的内存限制。

作为一种替代方案，集群管理员可以使用诸如
[ValidatingAdmissionPolicy](/zh-cn/docs/reference/access-authn-authz/validating-admission-policy)
之类的策略机制来强制对新 Pod 的 `emptyDir` 卷进行大小限制。

## 本地临时存储   {#local-ephemeral-storage}

节点具有本地临时存储，由本地连接的可写设备支持，或者有时由 RAM 支持。
"临时"意味着不对持久性提供长期保证。

Pod 使用临时本地存储作为临时空间、缓存和日志。
kubelet 可以使用本地临时存储为 Pods 提供临时空间，
通过将 [`emptyDir`](/zh-cn/docs/concepts/storage/volumes/#emptydir)
**卷**挂载到容器中。

对于本地临时存储的一般概念以及为容器配置临时存储的请求和/或限制的提示，
请参阅[本地临时存储](/zh-cn/docs/concepts/storage/ephemeral-storage/)页面。

### 本地临时存储的资源监控

kubelet 可以测量本地临时存储的使用量。只要你启用了本地临时存储容量隔离，
kubelet 就会进行该测量。

Kubernetes 会从以下几个方面跟踪 Pod 的临时存储使用量：
* 对容器的可写层（rootfs）、容器镜像，或两者的写入。
* 对本地 `emptyDir` 卷的写入。
* Pod 自身的日志（通常存放在 `/var/log/pods` 下）。
* 由 Kubernetes 管理并映射进 Pod 的系统文件，例如 `/etc/hosts`。

## 扩展资源（Extended Resources）   {#extended-resources}

扩展资源是 `kubernetes.io` 域名之外的标准资源名称。
它们使得集群管理员能够颁布非 Kubernetes 内置资源，而用户可以使用他们。

使用扩展资源需要两个步骤。首先，集群管理员必须颁布扩展资源。
其次，用户必须在 Pod 中请求扩展资源。

### 管理扩展资源   {#managing-extended-resources}

#### 节点级扩展资源     {#node-level-extended-resources}

节点级扩展资源绑定到节点。

##### 设备插件管理的资源   {#device-plugin-managed-resources}

有关如何颁布在各节点上由设备插件所管理的资源，
请参阅[设备插件](/zh-cn/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/)。

##### 其他资源   {#other-resources}

为了颁布新的节点级扩展资源，集群操作员可以向 API 服务器提交 `PATCH` HTTP 请求，
以在集群中节点的 `status.capacity` 中为其配置可用数量。
完成此操作后，节点的 `status.capacity` 字段中将包含新资源。
kubelet 会异步地对 `status.allocatable` 字段执行自动更新操作，使之包含新资源。

由于调度器在评估 Pod 是否适合在某节点上执行时会使用节点的 `status.allocatable` 值，
调度器只会考虑异步更新之后的新值。
在更新节点容量使之包含新资源之后和请求该资源的第一个 Pod 被调度到该节点之间，
可能会有短暂的延迟。

**示例：**

这是一个示例，显示了如何使用 `curl` 构造 HTTP 请求，公告主节点为 `k8s-master`
的节点 `k8s-node-1` 上存在五个 `example.com/foo` 资源。

```shell
curl --header "Content-Type: application/json-patch+json" \
--request PATCH \
--data '[{"op": "add", "path": "/status/capacity/example.com~1foo", "value": "5"}]' \
http://k8s-master:8080/api/v1/nodes/k8s-node-1/status
```

在前面的请求中，`~1` 是在 patch 路径中对字符 `/` 的编码。
JSON-Patch 中的操作路径的值被视为 JSON-Pointer 类型。
有关更多详细信息，请参见
[IETF RFC 6901 第 3 节](https://datatracker.ietf.org/doc/html/rfc6901#section-3)。

#### 集群层面的扩展资源   {#cluster-level-extended-resources}

集群层面的扩展资源并不绑定到具体节点。
它们通常由调度器扩展程序（Scheduler Extender）管理，这些程序处理资源消耗和资源配额。

你可以在[调度器配置](/zh-cn/docs/reference/config-api/kube-scheduler-config.v1/)
中指定由调度器扩展程序处理的扩展资源。

**示例：**

下面的调度器策略配置标明集群层扩展资源 "example.com/foo" 由调度器扩展程序处理。

- 仅当 Pod 请求 "example.com/foo" 时，调度器才会将 Pod 发送到调度器扩展程序。
- `ignoredByScheduler` 字段指定调度器不要在其 `PodFitsResources` 断言中检查
  "example.com/foo" 资源。

```json
{
  "kind": "Policy",
  "apiVersion": "v1",
  "extenders": [
    {
      "urlPrefix": "<extender-endpoint>",
      "bindVerb": "bind",
      "managedResources": [
        {
          "name": "example.com/foo",
          "ignoredByScheduler": true
        }
      ]
    }
  ]
}
```

#### DRA 扩展资源分配

DRA 扩展资源分配允许集群管理员在 DeviceClass 中指定一个
`extendedResourceName`，然后与所指定 DeviceClass 匹配的设备可以使用
Pod 的扩展资源请求来获取。
进一步阅读关于[使用 DRA 进行扩展资源分配](/zh-cn/docs/concepts/scheduling-eviction/dynamic-resource-allocation/#extended-resource)的内容。

### 使用扩展资源  {#consuming-extended-resources}

就像 CPU 和内存一样，用户可以在 Pod 的规约中使用扩展资源。
调度器负责资源的核算，确保同时分配给 Pod 的资源总量不会超过可用数量。

API 服务器将扩展资源的数量限制为整数。
**有效**数量的示例是 `3`、`3000m` 和 `3Ki`。
**无效**数量的示例是 `0.5` 和 `1500m`（因为 `1500m` 结果等同于 `1.5`）。

扩展资源取代了非透明整数资源（Opaque Integer Resources，OIR）。
用户可以使用 `kubernetes.io`（保留）以外的任何域名前缀。

要在 Pod 中使用扩展资源，请在容器规约的 `spec.containers[].resources.limits`
映射中包含资源名称作为键。

扩展资源不能过量使用，因此如果容器规约中同时存在请求和限制，则它们的取值必须相同。

仅当所有资源请求（包括 CPU、内存和任何扩展资源）都被满足时，Pod 才能被调度。
在资源请求无法满足时，Pod 会保持在 `PENDING` 状态。

**示例：**

下面的 Pod 请求 2 个 CPU 和 1 个 "example.com/foo"（扩展资源）。

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: my-pod
spec:
  containers:
  - name: my-container
    image: myimage
    resources:
      requests:
        cpu: 2
        example.com/foo: 1
      limits:
        example.com/foo: 1
```

## PID 限制   {#pid-limiting}

进程 ID（PID）限制允许对 kubelet 进行配置，以限制给定 Pod 可以消耗的 PID 数量。
有关信息，请参见 [PID 限制](/zh-cn/docs/concepts/policy/pid-limiting/)。

## 问题诊断  {#troubleshooting}

### 我的 Pod 处于悬决状态且事件信息显示 `FailedScheduling`  {#my-pods-are-pending-with-event-message-failedscheduling}

如果调度器找不到该 Pod 可以匹配的任何节点，则该 Pod 将保持未被调度状态，
直到找到一个可以被调度到的位置。每当调度器找不到 Pod 可以调度的地方时，
会产生一个 [Event](/zh-cn/docs/reference/kubernetes-api/cluster-resources/event-v1/)。
你可以使用 `kubectl` 来查看 Pod 的事件；例如：

```shell
kubectl describe pod frontend | grep -A 9999999999 Events
```

```
Events:
  Type     Reason            Age   From               Message
  ----     ------            ----  ----               -------
  Warning  FailedScheduling  23s   default-scheduler  0/42 nodes available: insufficient cpu
```

在上述示例中，由于节点上的 CPU 资源不足，名为 “frontend” 的 Pod 无法被调度。
由于内存不足（PodExceedsFreeMemory）而导致失败时，也有类似的错误消息。
一般来说，如果 Pod 处于悬决状态且有这种类型的消息时，你可以尝试如下几件事情：

- 向集群添加更多节点。
- 终止不需要的 Pod，为悬决的 Pod 腾出空间。
- 检查 Pod 所需的资源是否超出所有节点的资源容量。例如，如果所有节点的容量都是 `cpu: 1`，
  那么一个请求为 `cpu: 1.1` 的 Pod 永远不会被调度。
- 检查节点上的污点设置。如果集群中节点上存在污点，而新的 Pod 不能容忍污点，
  调度器只会考虑将 Pod 调度到不带有该污点的节点上。

你可以使用 `kubectl describe nodes` 命令检查节点容量和已分配的资源数量。例如：

```shell
kubectl describe nodes e2e-test-node-pool-4lw4
```

```
Name:            e2e-test-node-pool-4lw4
[ ... 这里忽略了若干行以便阅读 ...]
Capacity:
 cpu:                               2
 memory:                            7679792Ki
 pods:                              110
Allocatable:
 cpu:                               1800m
 memory:                            7474992Ki
 pods:                              110
[ ... 这里忽略了若干行以便阅读 ...]
Non-terminated Pods:        (5 in total)
  Namespace    Name                                  CPU Requests  CPU Limits  Memory Requests  Memory Limits
  ---------    ----                                  ------------  ----------  ---------------  -------------
  kube-system  fluentd-gcp-v1.38-28bv1               100m (5%)     0 (0%)      200Mi (2%)       200Mi (2%)
  kube-system  coredns-3297075139-61lj3              260m (13%)    0 (0%)      100Mi (1%)       170Mi (2%)
  kube-system  kube-proxy-e2e-test-...               100m (5%)     0 (0%)      0 (0%)           0 (0%)
  kube-system  monitoring-influxdb-grafana-v4-z1m12  200m (10%)    200m (10%)  600Mi (8%)       600Mi (8%)
  kube-system  node-problem-detector-v0.1-fj7m3      20m (1%)      200m (10%)  20Mi (0%)        100Mi (1%)
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  CPU Requests    CPU Limits    Memory Requests    Memory Limits
  ------------    ----------    ---------------    -------------
  680m (34%)      400m (20%)    920Mi (11%)        1070Mi (13%)
```

在上面的输出中，你可以看到如果 Pod 请求超过 1.120 CPU 或者
6.23Gi 内存，节点将无法满足。

通过查看 "Pods" 部分，你将看到哪些 Pod 占用了节点上的资源。

Pod 可用的资源量低于节点的资源总量，因为系统守护进程也会使用一部分可用资源。
在 Kubernetes API 中，每个 Node 都有一个 `.status.allocatable` 字段
（详情参见 [NodeStatus](/zh-cn/docs/reference/kubernetes-api/cluster-resources/node-v1/#NodeStatus)）。

字段 `.status.allocatable` 描述节点上可以用于 Pod 的资源总量（例如：15 个虚拟
CPU、7538 MiB 内存）。关于 Kubernetes 中节点可分配资源的信息，
可参阅[为系统守护进程预留计算资源](/zh-cn/docs/tasks/administer-cluster/reserve-compute-resources/)。

你可以配置[资源配额](/zh-cn/docs/concepts/policy/resource-quotas/)功能特性以限制每个名字空间可以使用的资源总量。
当某名字空间中存在 ResourceQuota 时，Kubernetes 会在该名字空间中的对象强制实施配额。
例如，如果你为不同的团队分配名字空间，你可以为这些名字空间添加 ResourceQuota。
设置资源配额有助于防止一个团队占用太多资源，以至于这种占用会影响其他团队。

你还需要考虑为这些名字空间设置授权访问：
为名字空间提供**全部**的写权限时，具有合适权限的人可能删除所有资源，
包括所配置的 ResourceQuota。

### 我的容器被终止了  {#my-container-is-terminated}

你的容器可能因为资源紧张而被终止。要查看容器是否因为遇到资源限制而被杀死，
请针对相关的 Pod 执行 `kubectl describe pod`：

```shell
kubectl describe pod simmemleak-hra99
```

输出类似于：

```
Name:                           simmemleak-hra99
Namespace:                      default
Image(s):                       saadali/simmemleak
Node:                           kubernetes-node-tf0f/10.240.216.66
Labels:                         name=simmemleak
Status:                         Running
Reason:
Message:
IP:                             10.244.2.75
Containers:
  simmemleak:
    Image:  saadali/simmemleak:latest
    Limits:
      cpu:          100m
      memory:       50Mi
    State:          Running
      Started:      Tue, 07 Jul 2019 12:54:41 -0700
    Last State:     Terminated
      Reason:       OOMKilled
      Exit Code:    137
      Started:      Fri, 07 Jul 2019 12:54:30 -0700
      Finished:     Fri, 07 Jul 2019 12:54:33 -0700
    Ready:          False
    Restart Count:  5
Conditions:
  Type      Status
  Ready     False
Events:
  Type    Reason     Age   From               Message
  ----    ------     ----  ----               -------
  Normal  Scheduled  42s   default-scheduler  Successfully assigned simmemleak-hra99 to kubernetes-node-tf0f
  Normal  Pulled     41s   kubelet            Container image "saadali/simmemleak:latest" already present on machine
  Normal  Created    41s   kubelet            Created container simmemleak
  Normal  Started    40s   kubelet            Started container simmemleak
  Normal  Killing    32s   kubelet            Killing container with id ead3fb35-5cf5-44ed-9ae1-488115be66c6: Need to kill Pod
```

在上面的例子中，`Restart Count: 5` 意味着 Pod 中的 `simmemleak`
容器被终止并且（到目前为止）重启了五次。
原因 `OOMKilled` 显示容器尝试使用超出其限制的内存量。

你接下来要做的或许是检查应用代码，看看是否存在内存泄露。
如果你发现应用的行为与你所预期的相同，则可以考虑为该容器设置一个更高的内存限制
（也可能需要设置请求值）。

## 

* 获取[分配内存资源给容器和 Pod](/zh-cn/docs/tasks/configure-pod-container/assign-memory-resource/)
  的实践经验
* 获取[分配 CPU 资源给容器和 Pod](/zh-cn/docs/tasks/configure-pod-container/assign-cpu-resource/)
  的实践经验
* 阅读 API 参考如何定义[容器](/zh-cn/docs/reference/kubernetes-api/workload-resources/pod-v1/#Container)
  及其[资源请求](/zh-cn/docs/reference/kubernetes-api/workload-resources/pod-v1/#resources)。
* 阅读更多关于[本地临时存储](/zh-cn/docs/concepts/storage/ephemeral-storage/)的内容。
* 进一步阅读 [kube-scheduler 配置参考（v1）](/zh-cn/docs/reference/config-api/kube-scheduler-config.v1/)
* 进一步阅读 [Pod 的服务质量等级](/zh-cn/docs/concepts/workloads/pods/pod-qos/)
* 进一步阅读[使用 DRA 进行扩展资源分配](/zh-cn/docs/concepts/scheduling-eviction/dynamic-resource-allocation/#extended-resource)
