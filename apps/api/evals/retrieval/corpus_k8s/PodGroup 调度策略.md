# PodGroup 调度策略

每个 [PodGroup](/zh-cn/docs/concepts/workloads/podgroup-api/)
都必须在其 `spec.schedulingPolicy` 字段中声明一个调度策略。
此策略决定了调度器如何处理组中的 Pod 集合。

## 策略类型  {#policy-types}

`schedulingPolicy` 字段支持两种策略类型：`basic` 和 `gang`。你必须指定其中一种。

### Basic 策略   {#basic-policy}

`basic` 策略指示调度器尽力评估所有 Pod。与 `gang` 策略不同，使用 `basic`
策略的 PodGroup 无论其当前有多少 Pod 可调度，都被认为是可行的。

使用 `basic` 策略的主要原因是将 Pod 组织成组，以提升可观测性和管理能力，
同时仍然在单个原子
[PodGroup 调度周期](/zh-cn/docs/concepts/scheduling-eviction/podgroup-scheduling/#podgroup-scheduling-cycle)内对它们进行一起评估。

此策略可用于那些不需要同时启动但逻辑上属于同一组的群体，
或者为不涉及“全有或全无”的组约束提供扩展空间。

```yaml
schedulingPolicy:
  basic: {}
```

### Gang 策略  {#gang-policy}

`gang` 策略强制执行“全有或全无”的调度机制。这对于紧密耦合的工作负载非常重要，因为部分启动可能导致死锁或资源浪费。

此策略常用于 [Job](/zh-cn/docs/concepts/workloads/controllers/job/)
或其他需要所有 Worker 同时运行才能推进的批处理任务。

`gang` 策略需要一个 `minCount` 字段，该字段表示为了使该组可行，必须同时调度的最小 Pod 数量：

```yaml
schedulingPolicy:
  gang:
    # 允许以组同时调度所需的最小 Pod 数量
    minCount: 4
```

## 通过 PodGroupTemplates 设置策略  {#setting-policies-via-podgrouptemplates}

使用 [Workload API](/zh-cn/docs/concepts/workloads/workload-api/) 时，
你需要在 `PodGroupTemplates` 中定义调度策略。
工作负载控制器会将模板中的策略复制到它创建的每个 PodGroup 中，
从而使 PodGroup 成为自包含的。对工作负载模板的更改只会影响新创建的 PodGroup，
而不会影响已存在的 PodGroup。

对于独立 PodGroup（未通过工作负载创建），你可以直接在
PodGroup 本身上设置 `spec.schedulingPolicy`。

## CompositePodGroup 中的策略   {#policies-in-compositepodgroups}

当 [`CompositePodGroup`](/zh-cn/docs/reference/command-line-tools-reference/feature-gates/#CompositePodGroup)
特性门控和 `scheduling.k8s.io/v1alpha3` API 组启用时，
Workload 中的 `CompositePodGroupTemplate` 和 `CompositePodGroup` 对象也可以声明调度策略。

`PodGroup` 中的调度策略管理的是一组单个 Pod，而
`CompositePodGroup` 的调度策略管理的是其直接的**子组**
（可以是 `CompositePodGroup` 和 `PodGroup` 对象）。

### CompositePodGroup 的策略类型   {#policy-types-for-compositepodgroups}

与 `PodGroup` 类似，`CompositePodGroup` 的 `spec.schedulingPolicy` 字段支持两种类型：

- **`basic`**：`CompositePodGroup` 中的子组被独立评估和准入。
- **`gang`**：在子组之间强制执行多级"全有或全无"的调度机制。
  只有当至少 `minGroupCount` 个子组可以同时被调度时，`CompositePodGroup` 才是可调度的。

```yaml
schedulingPolicy:
  gang:
    # 允许复合组同时调度的最小子组数量
    minGroupCount: 2
```

### 通过模板设置复合策略   {#setting-composite-policies-via-templates}

使用 [Workload API](/zh-cn/docs/concepts/workloads/workload-api/) 时，
`CompositePodGroup` 的调度策略在 `CompositePodGroupTemplate` 中定义。
工作负载控制器会将模板中指定的 `schedulingPolicy` 复制到运行时创建的每个 `CompositePodGroup` 中。
与可以更新 `minCount` 的叶子 `PodGroupTemplate` 不同，
`CompositePodGroupTemplate` 中的 `minGroupCount` 是不可变的。

## 

* 请参阅 [PodGroup API](/zh-cn/docs/concepts/workloads/podgroup-api/)，了解策略如何在运行时执行。
* 了解定义 PodGroupTemplate 的 [Workload API](/zh-cn/docs/concepts/workloads/workload-api/)。
* 阅读 [PodGroup 调度](/zh-cn/docs/concepts/scheduling-eviction/podgroup-scheduling/)。
* 阅读 [Gang 调度](/zh-cn/docs/concepts/scheduling-eviction/gang-scheduling/)算法。
* 学习控制器用于编译这些策略的[调度构建块和 workloadbuilder 库](/zh-cn/docs/concepts/workloads/workload-api/workloadbuilder/)。
