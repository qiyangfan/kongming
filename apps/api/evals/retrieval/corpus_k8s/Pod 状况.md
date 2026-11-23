# Pod 状况

在 Kubernetes 中，许多对象都有**状况（condition）**。
状况是对象所代表事物的实际状态某些方面的标记。
Pod 有状况，Kubernetes Pod 状况是控制器（以及进行故障排除的人员）了解 Pod 健康状况的重要方面。

Pod 的[阶段（phase）](/zh-cn/docs/concepts/workloads/pods/pod-lifecycle/#pod-phase)提供了
Pod 在其生命周期中所处位置的高级摘要，但单个值无法捕捉全貌。
例如，Pod 可能处于 Running 阶段，但尚未准备好提供流量。
Pod 状况通过独立跟踪 Pod 状态的多个方面来补充阶段，
例如是否已调度、其容器是否就绪、是否正在进行调整大小，
或者 Pod 是否即将由于污点而受到干扰。

## Pod 状况的结构   {#structure-of-a-pod-condition}

Pod 的状态（status）包括一个
[PodConditions](/zh-cn/docs/reference/generated/kubernetes-api//#podcondition-v1-core)
数组，用于指示 Pod 是否已通过某些检查点。

PodCondition 数组的每个元素都有以下字段：

| Field name           | Description                                                                                          |
|:---------------------|:-----------------------------------------------------------------------------------------------------|
| `type`               | Name of this Pod condition.                                                                          |
| `status`             | Indicates whether that condition is applicable, with possible values `"True"`, `"False"`, or `"Unknown"`. |
| `lastProbeTime`      | Timestamp of when the Pod condition was last probed.                                                 |
| `lastTransitionTime` | Timestamp for when the Pod last transitioned from one status to another.                             |
| `reason`             | Machine-readable, UpperCamelCase text indicating the reason for the condition's last transition.     |
| `message`            | Human-readable message indicating details about the last status transition.                          |
| `observedGeneration` | The `.metadata.generation` of the Pod at the time the condition was recorded. See [Pod generation](/docs/concepts/workloads/pods/#pod-generation). |

-->

| 字段名称              | 描述                                                   |
|:---------------------|:------------------------------------------------------|
| `type`               | 此 Pod 状况的名称。                                      |
| `status`             | 此 Pod 状况是否适用，可能的值为 `True`、`False`、`Unknown`。 |
| `lastProbeTime`      | 最后一次探查 Pod 状况的时间。                              |
| `lastTransitionTime` | 最后一次 Pod 状况转换的时间。                              |
| `reason`             | 机器可读的、大驼峰式文本，表示条件最后一次转换的原因。          |
| `message`            | 人可读的消息，指示状态转换的详细信息。                       |
| `observedGeneration` | 当记录此 Pod 状况时，Pod 的 `.metadata.generation`。请参阅 [Pod 生成](/zh-cn/docs/concepts/workloads/pods/#pod-generation)。    |

## 内置 Pod 状况   {#built-in-pod-conditions}

Kubernetes 管理以下 Pod 状况：

[生命周期状况](#lifecycle-pod-conditions)：随着 Pod 经历其生命周期而设置，大致按此顺序：
`PodScheduled`、`PodReadyToStartContainers`、`Initialized`、`ContainersReady`、`Ready`。

[其他状况](#other-pod-conditions)：响应特定操作或事件而设置：
`DisruptionTarget`、`PodResizePending`、`PodResizeInProgress`。

除了上述内置状况外，你还可以使用 [Pod 就绪门控](#enhanced-pod-readiness)定义自定义状况。

## 生命周期 Pod 状况   {#lifecycle-pod-conditions}

随着 Pod 经历其生命周期，kubelet 大致按以下顺序设置以下状况：

1. `PodScheduled`：Pod 已调度到节点。
1. `PodReadyToStartContainers`：Pod sandbox 已成功创建并配置了网络。
   sandbox 和网络由容器运行时和
   CNI 插件设置。
1. `Initialized`：所有[初始容器](/zh-cn/docs/concepts/workloads/pods/init-containers/)均已成功完成。
   对于没有初始容器的 Pod，此状况在 sandbox 创建之前设置为 `True`。
1. `ContainersReady`：Pod 中的所有容器都已就绪。
   容器的就绪状态由其[就绪探针](/zh-cn/docs/concepts/workloads/pods/probes/#readiness-probe)确定（如果已配置）。
1. `Ready`：Pod 能够处理请求，应添加到所有匹配的 [Service](/zh-cn/docs/concepts/services-networking/service/) 的负载均衡池中。
   未 `Ready` 的 Pod 会从 Service 端点中移除。

`Ready` 状况不仅取决于 `ContainersReady`。
如果 Pod 指定了 `readinessGates`，则所有这些自定义状况也必须为 `True`，Pod 才能为 `Ready`。
有关详细信息，请参阅 [Pod 就绪](#enhanced-pod-readiness)。

你可以使用 kubectl 检查 Pod 的状况：

```shell
kubectl get pod <pod-name> -o yaml
```

以下显示了运行中的 Pod 的 `status.conditions` 的样子：

```yaml
status:
  conditions:
    - type: PodScheduled
      status: "True"
      lastProbeTime: null
      lastTransitionTime: "2026-03-29T08:52:21Z"
      observedGeneration: 1
    - type: PodReadyToStartContainers
      status: "True"
      lastProbeTime: null
      lastTransitionTime: "2026-04-11T06:02:16Z"
      observedGeneration: 1
    - type: Initialized
      status: "True"
      lastProbeTime: null
      lastTransitionTime: "2026-03-29T08:52:21Z"
      observedGeneration: 1
    - type: ContainersReady
      status: "True"
      lastProbeTime: null
      lastTransitionTime: "2026-04-11T06:02:45Z"
      observedGeneration: 1
    - type: Ready
      status: "True"
      lastProbeTime: null
      lastTransitionTime: "2026-04-11T06:02:45Z"
      observedGeneration: 1
```

### PodReadyToStartContainers   {#pod-ready-to-start-containers}

在早期开发期间，此状况名为 `PodHasNetwork`。

Pod 在节点上调度后，需要由 kubelet 准入并挂载任何所需的存储卷。
这些阶段完成后，kubelet 与容器运行时（使用容器运行时接口（CRI））
协作，为 Pod 设置运行时 Sandbox 并配置网络。
`PodReadyToStartContainers` 状况会被添加到 Pod 的 `status.conditions` 字段。

当 kubelet 检测到 Pod 缺少已配置网络功能的运行时沙箱（runtime sandbox）时，
该状况会被设置为 `False`。这种情况发生在以下场景中：

- 在 Pod 生命周期的早期，kubelet 尚未开始使用容器运行时为 Pod 设置 sandbox。
- 在 Pod 生命周期的后期，Pod sandbox 已被销毁，原因是：
  - 节点重新启动，而 Pod 未被驱逐
  - 对于使用虚拟机进行隔离的容器运行时，Pod sandbox 虚拟机重新启动，这需要创建新的 sandbox 和新的容器网络配置

运行时插件成功完成 Pod 的 sandbox 创建和网络配置后，kubelet 将 `PodReadyToStartContainers` 状况设置为 `True`。
在 `PodReadyToStartContainers` 状况设置为 `True` 后，kubelet 可以开始拉取容器镜像并创建容器。

对于具有 Init 容器的 Pod，kubelet 在 Init 容器成功完成后
（这发生在运行时插件成功创建 sandbox 和配置网络之后）将 `Initialized` 状况设置为 `True`。
对于没有初始容器的 Pod，kubelet 在 sandbox 创建和网络配置开始之前将 `Initialized` 状况设置为 `True`。

## 其他 Pod 状况   {#other-pod-conditions}

以下状况不是正常 Pod 生命周期进程的一部分。
它们响应特定操作或事件而设置。

### DisruptionTarget   {#disruption-target}

添加专用的 Pod `DisruptionTarget` 状况以指示 Pod 即将由于
而被删除。
该状况的 `reason` 字段还指示 Pod 终止的以下原因之一：

`PreemptionByScheduler`
: Pod 即将被调度器 ，以便容纳具有更高优先级的新 Pod。
  有关更多信息，请参阅 [Pod 优先级抢占](/zh-cn/docs/concepts/scheduling-eviction/pod-priority-preemption/)。

`DeletionByTaintManager`
: Pod 即将被 Taint Manager（`kube-controller-manager` 内节点生命周期控制器的一部分）删除，
  原因是 Pod 不容忍的 `NoExecute` 污点；
  请参阅基于的驱逐。

`EvictionByEvictionAPI`
: Pod 已被标记为。

`DeletionByPodGC`
: 绑定到不再存在的节点的 Pod 即将被
  [Pod 垃圾回收](/zh-cn/docs/concepts/workloads/pods/pod-lifecycle/#pod-garbage-collection)删除。

`TerminationByKubelet`
: Pod 已被 kubelet 终止，原因是、
  [节点体面关闭](/zh-cn/docs/concepts/architecture/nodes/#graceful-node-shutdown)或
  为[系统关键 Pod](/zh-cn/docs/tasks/administer-cluster/guaranteed-scheduling-critical-addon-pods/) 进行抢占。

在所有其他干扰场景中，例如由于超过
[Pod 容器限制](/zh-cn/docs/concepts/configuration/manage-resources-containers/)而导致的驱逐，
Pod 不会收到 `DisruptionTarget` 状况，因为干扰可能是由 Pod 引起的，并且会在重试时再次发生。

Pod 干扰可能会被中断。
控制平面可能会重新尝试继续干扰同一个 Pod，但这不是保证的。
因此，`DisruptionTarget` 状况可能会添加到 Pod，但该 Pod 可能实际上不会被删除。
在这种情况下，一段时间后，Pod 干扰状况将被清除。

除了清理 Pod 外，Pod 垃圾回收器（PodGC）还会将它们标记为失败（如果它们处于非终止阶段）
（另请参阅 [Pod 垃圾回收](/zh-cn/docs/concepts/workloads/pods/pod-lifecycle/#pod-garbage-collection)）。

使用 Job（或 CronJob）时，你可能希望将这些 Pod 干扰状况用作 Job 的
[Pod 失败策略](/zh-cn/docs/concepts/workloads/controllers/job#pod-failure-policy)的一部分。

有关更多详细信息，请参阅[干扰](/zh-cn/docs/concepts/workloads/pods/disruptions/)。

### PodResizePending 和 PodResizeInProgress   {#pod-resize-conditions}

kubelet 更新 Pod 的状态状况以指示调整大小请求的状态：

- `type: PodResizePending`：kubelet 无法立即批准请求。
  `message` 字段提供原因说明。
  - `reason: Infeasible`：请求的调整大小在当前节点上不可能（例如，请求的资源超过节点拥有的资源）。
  - `reason: Deferred`：请求的调整大小目前不可能，但以后可能变得可行（例如，如果另一个 Pod 被移除）。
    kubelet 将重试调整大小。
- `type: PodResizeInProgress`：kubelet 已接受调整大小并分配了资源，但更改仍在应用中。
  这通常很短暂，但可能根据资源类型和运行时行为花费更长时间。
  执行期间的任何错误都在 `message` 字段中报告（以及 `reason: Error`）。

如果请求的调整大小被 `Deferred`，kubelet 将定期重试调整大小，例如当另一个 Pod 被移除或缩容时。

有关 Pod 调整大小的更多详细信息，
请参阅[调整分配给容器的 CPU 和内存资源](/zh-cn/docs/tasks/configure-pod-container/resize-container-resources/)。

## 增强 Pod 就绪   {#enhanced-pod-readiness}

你的应用可以将额外的反馈或信号注入 Pod 的 `.status`；
这称为**增强 Pod 就绪**。
要使用此功能，请在 Pod 的 `spec` 中设置 `readinessGates`，
以指定 kubelet 评估 Pod 就绪的其他状况列表。
然后你实现或安装一个管理这些自定义状况的控制器，
kubelet 使用该控制器作为额外输入来决定 Pod 是否就绪。

就绪门由 Pod 的 `status.condition` 字段的当前状态确定。
如果 Kubernetes 在 Pod 的 `status.conditions` 字段中找不到这样的状况，
则该状况的状态默认为 "`False`"。

```yaml
kind: Pod
...
spec:
  readinessGates:
    - conditionType: "www.example.com/feature-1"
status:
  conditions:
    - type: Ready                              # 内置的 PodCondition
      status: "False"
      lastProbeTime: null
      lastTransitionTime: 2018-01-01T00:00:00Z
    - type: "www.example.com/feature-1"        # 额外的 PodCondition
      status: "False"
      lastProbeTime: null
      lastTransitionTime: 2018-01-01T00:00:00Z
  containerStatuses:
    - containerID: docker://abcd...
      ready: true
...
```

你添加的 Pod 状况必须具有符合 Kubernetes
[标签键格式](/zh-cn/docs/concepts/overview/working-with-objects/labels/#syntax-and-character-set)的名称。

### Pod 就绪的状态   {#status-for-pod-readiness}

要为 Pod 设置这些 `status.conditions`，应用和
 应使用
Pod 状态子资源的 `PATCH` 操作。
你可以使用 `kubectl patch` 和 `--subresource=status`，
或使用 [Kubernetes 客户端库](/zh-cn/docs/reference/using-api/client-libraries/)编写代码来设置自定义
Pod 状况以实现 Pod 就绪。

对于使用自定义状况的 Pod，仅当以下两个陈述都适用时，该 Pod 才被评估为就绪：

- Pod 中的所有容器都已就绪。
- `readinessGates` 中指定的所有状况都为 `True`。

当 Pod 的容器为 Ready 但至少缺少一个自定义状况或为 `False` 时，
kubelet 将 Pod 的 `Ready` 状况设置为 `status: "False"` 及 `reason: ReadinessGatesNotReady`。

## 

- 了解 [Pod 生命周期](/zh-cn/docs/concepts/workloads/pods/pod-lifecycle/)。
- 了解[干扰](/zh-cn/docs/concepts/workloads/pods/disruptions/)。
- 了解[容器探针](/zh-cn/docs/concepts/workloads/pods/probes/)以及它们如何影响 Pod 就绪。
- 了解如何[就地调整 Pod 资源](/zh-cn/docs/tasks/configure-pod-container/resize-container-resources/)。
