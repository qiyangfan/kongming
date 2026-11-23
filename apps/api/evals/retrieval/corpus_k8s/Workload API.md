# Workload API

Workload API 资源允许你描述一个多 Pod 应用的调度需求和结构。
虽然工作负载控制器提供了运行时行为，但 Workload API 旨在为“真实”的工作负载（例如 Job 等）提供调度约束。

## 什么是 Workload？  {#what-is-a-workload}

Workload API 资源属于 `scheduling.k8s.io/v1alpha1`
API 组
（在使用此 API 之前，你的集群必须启用该 API 组以及 `GenericWorkload`
[特性门控](/zh-cn/docs/reference/command-line-tools-reference/feature-gates/)）。
此资源作为一种结构化、机器可读的定义，用于描述多 Pod 应用的调度需求。
面向用户的工作负载（例如 [Job](/zh-cn/docs/concepts/workloads/controllers/job/)）定义“运行什么”，
而 Workload 资源则决定一组 Pod 应该如何被调度，以及在其整个生命周期中如何管理其调度位置。

## API 结构  {#api-structure}

Workload 允许你定义一组 Pod，并为其应用调度策略。
Workload 由两个部分组成：Pod 分组列表和到某个控制器的引用。

### PodGroupTemplates

`spec.podGroupTemplates` 列表定义了你的工作负载的不同组件。
例如，一个机器学习作业可能有一个 `driver` 模板和一个 `worker` 模板。

`podGroupTemplates` 中的每个条目必须包含：
1. 一个唯一的 `name`，用于在 `PodGroup` 的 `spec.podGroupTemplateRef` 中引用此模板。
2. 一个[调度策略](/zh-cn/docs/concepts/workloads/workload-api/policies/)（`basic` 或 `gang`）。

如果启用了 [`WorkloadAwarePreemption`](/zh-cn/docs/reference/command-line-tools-reference/feature-gates/#WorkloadAwarePreemption)
[特性门控](/zh-cn/docs/reference/command-line-tools-reference/feature-gates/)，
则 `podGroups` 中的每个条目也可以有[优先级和干扰模式](/zh-cn/docs/concepts/workloads/workload-api/disruption-and-priority/)。

单个工作负载中 PodGroupTemplates 的最大数量为 8。

```yaml
apiVersion: scheduling.k8s.io/v1alpha2
kind: Workload
metadata:
  name: training-job-workload
  namespace: some-ns
spec:
  controllerRef:
    apiGroup: batch
    kind: Job
    name: training-job
  podGroupTemplates:
  - name: workers
    schedulingPolicy:
      gang:
        # 只有当 4 个 Pod 可以同时运行时，该进程才能被调度。
        minCount: 4
    priorityClassName: high-priority # 仅适用于启用 WorkloadAwarePreemption 特性门控的情况
    disruptionMode: PodGroup # 仅适用于启用 WorkloadAwarePreemption 特性门控的情况
```

当工作负载控制器根据这些模板之一创建 `PodGroup` 时，
它会将 `schedulingPolicy` 复制到 `PodGroup` 自身的规范中。
对 `Workload` 的更改只会影响新创建的 `PodGroup`，而不会影响已存在的 `PodGroup`。

### 引用工作负载控制对象

`controllerRef` 字段用于将 Workload 关联回定义此应用的具体高层对象，
例如 [Job](/zh-cn/docs/concepts/workloads/controllers/job/) 或定制 CRD。
这对于可观测性和工具链非常有用。此数据不会用于 Workload 的调度或管理。

## 使用 Job 进行 Gang 调度

当启用了 [`WorkloadWithJob`](/zh-cn/docs/reference/command-line-tools-reference/feature-gates/)
特性门控时，[Job](/zh-cn/docs/concepts/workloads/controllers/job/)
控制器会自动为并行索引 Job 创建 Workload 和 PodGroup 对象，
其中 `.spec.parallelism` 等于 `.spec.completions`。
Gang 策略的 `minCount` 被设置为 Job 的并行度，因此所有 Pod 必须能够一起调度，
然后它们中的任何一个才能绑定到节点。

这是使用 Job 进行 Gang 调度的内置路径。你不需要自己创建 Workload 或 PodGroup 对象，因为 Job 控制器会自动处理。
其他工作负载控制器（如 JobSet）可能会独立管理自己的 Workload 和 PodGroup 对象。

## 

* 了解 [PodGroup 调度策略](/zh-cn/docs/concepts/workloads/workload-api/policies/)。
* 在 [PodGroup API](/zh-cn/docs/concepts/workloads/podgroup-api/) 概述中查看如何从工作负载创建 PodGroup。
* 阅读有关 Pod 如何通过[调度组](/zh-cn/docs/concepts/workloads/pods/scheduling-group/)字段引用其 PodGroup 的信息。
* 了解[拓扑感知工作负载调度](/zh-cn/docs/concepts/workloads/workload-api/topology-aware-scheduling/)。
* 理解 [Gang 调度](/zh-cn/docs/concepts/scheduling-eviction/gang-scheduling/)算法。
