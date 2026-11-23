# PodGroup 生命周期

[PodGroup](/zh-cn/docs/concepts/workloads/podgroup-api/) 作为一个单元进行调度，
并在 Pod 仍在运行时受到保护，防止提前删除。

## 所有权和生命周期   {#ownership-and-lifecycle}

`PodGroup` 由创建它们的工作负载控制器（例如 Job）通过标准的 `ownerReferences` 拥有。
当拥有对象被删除时，`PodGroup` 会自动被垃圾回收。

PodGroup 名称在名字空间内必须是唯一的，并且必须是有效的
[DNS 子域名](/zh-cn/docs/concepts/overview/working-with-objects/names#dns-subdomain-names)。

## 创建顺序   {#creation-ordering}

控制器必须按以下顺序创建对象：

1. `Workload` — 调度策略模板。
2. `PodGroup` — 运行时实例。
3. `Pod` — 其 `spec.schedulingGroup.podGroupName` 指向 `PodGroup`。

如果 `PodGroup` 包含一个指向不存在（或正在被删除）的 `Workload` 的 `podGroupTemplateRef`，
API 服务器会拒绝 `PodGroup` 创建请求。
引用的 `Workload` 必须在 `PodGroup` 创建之前存在。

如果 `Pod` 引用了不存在的 `PodGroup`，则该 `Pod` 保持 pending 状态。
一旦 `PodGroup` 被创建，调度器会自动将 `Pod` 加入调度队列。

## 删除保护   {#deletion-protection}

当 PodGroup 的任何 Pod 仍在运行时，不能完全删除该 PodGroup。
一个专门的 finalizer 确保在所有引用该 PodGroup 的 Pod 达到终止阶段（`Succeeded` 或 `Failed`）之前阻止删除。

## 控制器管理的 PodGroup 和用户管理的 PodGroup   {#controller-managed-and-user-managed-podgroups}

在大多数情况下，工作负载控制器（例如 Job）自动创建 `PodGroup`（控制器管理）。
控制器在创建时为每个 Pod 确定 `podGroupName`，
类似于 `DaemonSet` 为每个 Pod 设置节点亲和性的方式。

如果你需要对命名和生命周期进行更多控制，可以直接创建 `PodGroup` 对象，
并在 Pod 模板中自己设置 `spec.schedulingGroup.podGroupName`（用户管理）。
这使你可以完全控制 `PodGroup` 的创建和命名。

## 限制   {#limitations}

* `PodGroup` 中的所有 Pod 必须使用相同的 `.spec.schedulerName`。
  如果检测到不匹配，调度器会拒绝组中的所有 Pod 为不可调度。

  
* `PodGroup` 中的所有 Pod 必须使用相同的 `.spec.priority`。
  如果检测到不一致，调度器会拒绝该组内的所有 Pod，并将其标记为不可调度。

* `PodGroup` 中的所有 Pod 必须使用相同的 `.spec.preemptionPolicy`。
  如果检测到不一致，调度器会拒绝该组内的所有 Pod，并将其标记为不可调度。

* Pod 上的 `spec.schedulingGroup` 字段是不可变的。
  设置后，Pod 无法移动到不同的 PodGroup 中。

* 单个 `Workload` 中 `PodGroupTemplates` 的最大数量为 8。

* 调度器不会更新 `PodGroup` 的持续运行状态，也不会记录初始调度之后的后续调度尝试。
  因此，当现有 Pod 失败、被驱逐或终止时，或者新观察到的 Pod 调度失败时，`PodGroup` 的状态都不会更新。
  
## 

* 了解 [PodGroup API](/zh-cn/docs/concepts/workloads/podgroup-api/) 概览和结构。
* 了解提供 `PodGroupTemplates` 的[工作负载 API](/zh-cn/docs/concepts/workloads/workload-api/)。
* 查看 Pod 如何通过[调度组](/zh-cn/docs/concepts/workloads/pods/scheduling-group/)字段引用其 PodGroup。
* 理解 [Gang 调度](/zh-cn/docs/concepts/scheduling-eviction/gang-scheduling/) 算法。
* 阅读 [PodGroup 调度策略](/zh-cn/docs/concepts/workloads/workload-api/policies/)了解更多关于 `basic` 和 `Gang` 的详细信息。
