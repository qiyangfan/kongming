# PodGroup API

PodGroup 是一个运行时对象，代表一组作为单个单元一起调度的 Pod。

虽然[工作负载 API](/zh-cn/docs/concepts/workloads/workload-api/) 定义了调度策略模板，
但 PodGroup 是运行时对应的对象，它同时承载了策略和该组特定实例的调度状态。

## 什么是 PodGroup？   {#what-is-a-podgroup}

PodGroup API 资源是 `scheduling.k8s.io/v1beta1`
API 组的一部分，
在使用此 API 之前，你的集群必须启用该 API 组以及 `GenericWorkload`
[特性门控](/zh-cn/docs/reference/command-line-tools-reference/feature-gates/)。

PodGroup 是一个自包含的调度单元。它定义了应该一起调度的 Pod 组，
承载了控制放置的调度策略，并记录了该调度决策的运行时状态。

## API 结构   {#api-structure}

PodGroup 由定义所需调度行为的 `spec` 和反映当前调度状态的 `status` 组成。

### 调度策略   {#scheduling-policy}

每个 PodGroup 在 `spec.schedulingPolicy` 中承载一个[调度策略](/zh-cn/docs/concepts/workloads/workload-api/policies/)
（`basic` 或 `Gang`）。当工作负载控制器创建 PodGroup 时，
此策略在创建时从 Workload 的 PodGroupTemplate 复制。
对于独立的 PodGroup，你直接设置策略。

```yaml
spec:
  schedulingPolicy:
    gang:
      minCount: 4
```

### 模板引用   {#template-reference}

可选的 `spec.podGroupTemplateRef` 将 PodGroup 链接回它所创建自的 Workload 中的 PodGroupTemplate。
这对于可观测性和工具非常有用。

```yaml
spec:
  podGroupTemplateRef:
    workload:
      workloadName: training-policy
      podGroupTemplateName: worker
```

### 优先级和干扰模式   {#priority-and-disruption-mode}

每个 PodGroup 还可以定义自己的 `spec.priority`、`spec.preemptionPolicy` 和
`spec.disruptionMode`。优先级和抢占策略通过 `spec.priorityClassName` 字段设置，
该字段指向一个 `PriorityClass` 资源。有关这些字段的详细描述，请参阅
[PodGroup 干扰和优先级](/zh-cn/docs/concepts/workloads/workload-api/disruption-and-priority/)。

当工作负载控制器创建 PodGroup 时，这些字段在创建时从 Workload 的 PodGroupTemplate 复制。
对于独立的 PodGroup，你直接设置这些字段。

```yaml
spec:
  disruptionMode:
    all: {}
  priorityClassName: high-priority
```

### 为 PodGroup 请求 DRA 设备   {#requesting-dra-devices-for-a-podgroup}

通过动态资源分配（DRA）
可获得的设备可以由
PodGroup 通过其 `spec.resourceClaims` 字段请求：

```yaml
apiVersion: scheduling.k8s.io/v1beta1
kind: PodGroup
metadata:
  name: training-group
  namespace: some-ns
spec:
  ...
  resourceClaims:
  - name: pg-claim
    resourceClaimName: my-pg-claim
  - name: pg-claim-template
    resourceClaimTemplateName: my-pg-template
```

与 PodGroup 关联的
ResourceClaim
可以由属于该组的所有 Pod 共享。
只需在 ResourceClaim 的 `status.reservedFor` 中引用 PodGroup，而不是每个单独的 Pod，
同一 PodGroup 中的任意数量的 Pod 都可以共享一个 ResourceClaim。
还可以为每个 PodGroup 从
ResourceClaimTemplate
生成 ResourceClaim，允许分配给每个生成的 ResourceClaim 的设备由每个 PodGroup 中的 Pod 共享。

有关更多详细信息和更完整的示例，请参阅
[DRA 文档](/zh-cn/docs/concepts/scheduling-eviction/dynamic-resource-allocation/#workload-resource-claims)。

### 状态   {#status}

调度器更新 `status.conditions` 以报告组是否已成功调度。
主要条件是 `PodGroupInitiallyScheduled`，当所有必需的 Pod 已放置时为 `True`，
当调度失败时为 `False`。

`PodGroupInitiallyScheduled` 条件仅反映初始调度决策。
如果 Pod 后续失败或被驱逐，调度器不会更新它。
有关详细信息，请参阅[限制](/zh-cn/docs/concepts/workloads/podgroup-api/lifecycle/#limitations)。

有关条件和原因的完整列表，请参阅
[PodGroup 生命周期](/zh-cn/docs/concepts/workloads/podgroup-api/lifecycle/#podgroup-status)页面。

## 创建 PodGroup   {#creating-a-podgroup}

PodGroup API 资源是 `scheduling.k8s.io/v1beta1`
API 组的一部分。
（在使用此 API 之前，你的集群必须启用该 API 组以及 `GenericWorkload`
[特性门控](/zh-cn/docs/reference/command-line-tools-reference/feature-gates/)。）

以下清单创建一个具有 gang 调度策略的 PodGroup，该策略要求至少 4 个 Pod 同时可调度：

```yaml
apiVersion: scheduling.k8s.io/v1beta1
kind: PodGroup
metadata:
  name: training-worker-0
  namespace: default
spec:
  schedulingPolicy:
    gang:
      minCount: 4
```

你可以在集群中检查 PodGroup：

```shell
kubectl get podgroups
```

要查看包括调度条件在内的完整状态：

```shell
kubectl describe podgroup training-worker-0
```

## 如何组合使用   {#how-it-fits-together}

控制器、Workload、PodGroup 和 Pod 之间的关系遵循以下模式：

1. 工作负载控制器创建一个 Workload，该 Workload 定义带有调度策略的 PodGroupTemplate。
2. 对于每个运行时实例，控制器从 Workload 的其中一个 PodGroupTemplate 创建一个 PodGroup。
3. 控制器创建通过 `spec.schedulingGroup.podGroupName` 字段引用 PodGroup 的 Pod。

[Job](/zh-cn/docs/concepts/workloads/controllers/job/) 控制器是目前唯一遵循此模式的内置工作负载控制器。
自定义控制器可以为自己的工作负载类型实现相同的流程。

```yaml
apiVersion: scheduling.k8s.io/v1beta1
kind: Workload
metadata:
  name: training-policy
spec:
  podGroupTemplates:
  - name: worker
    schedulingPolicy:
      gang:
        minCount: 4
---
apiVersion: scheduling.k8s.io/v1beta1
kind: PodGroup
metadata:
  name: training-worker-0
spec:
  podGroupTemplateRef:
    workload:
      workloadName: training-policy
      podGroupTemplateName: worker
  schedulingPolicy:
    gang:
      minCount: 4
---
apiVersion: v1
kind: Pod
metadata:
  name: worker-0
spec:
  schedulingGroup:
    podGroupName: training-worker-0
  containers:
  - name: ml-worker
    image: training:v1
```

Workload 充当长期存在的策略定义，而 PodGroup 处理瞬态的、每个实例的运行时状态。
这种分离意味着单个 PodGroup 的状态更新不会与共享的 Workload 对象发生冲突。

## 父组   {#parent-group}

当 [`CompositePodGroup`](/zh-cn/docs/reference/command-line-tools-reference/feature-gates/#CompositePodGroup)
特性门控启用时，`PodGroup` 可以作为由 `PodGroup` 和 `CompositePodGroup` 组成的多级组层级中的叶节点。
`PodGroup` 可以使用 `spec.parentCompositePodGroupName` 字段指定其父组。
将工作负载组织为组层级可用于表达多级编组调度需求、多级拓扑约束以及工作负载各部分之间的干扰命运共享。

有关层级组结构和多级编组调度的更多详细信息，请参阅
[CompositePodGroup API](/zh-cn/docs/concepts/workloads/compositepodgroup-api/) 概述。

指定了 `spec.parentCompositePodGroupName` 的 `PodGroup` 还必须指定 `spec.workloadRef`，
将 `PodGroup` 关联回其在 `Workload` 中的模板。不允许独立的 `PodGroup` 对象
（创建时没有 `spec.workloadRef`）指定父 `CompositePodGroup`。

## 

* 详细了解 [PodGroup 生命周期](/zh-cn/docs/concepts/workloads/podgroup-api/lifecycle/)。
* 阅读提供 PodGroupTemplate 的 [Workload API](/zh-cn/docs/concepts/workloads/workload-api/)。
* 查看 Pod 如何通过[调度组](/zh-cn/docs/concepts/workloads/pods/scheduling-group/)字段引用其 PodGroup。
* 理解 [Gang 调度](/zh-cn/docs/concepts/scheduling-eviction/gang-scheduling/)算法。
