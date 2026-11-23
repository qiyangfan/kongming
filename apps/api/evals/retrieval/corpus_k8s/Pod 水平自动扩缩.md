# Pod 水平自动扩缩

在 Kubernetes 中，**HorizontalPodAutoscaler** 自动更新工作负载资源
（例如 Deployment 或者
StatefulSet），
目的是容量自动扩缩以满足需求。

水平扩缩意味着对增加的负载的响应是部署更多的 Pod。
这与“垂直（Vertical）”扩缩不同，对于 Kubernetes，
垂直扩缩意味着将更多资源（例如：内存或 CPU）分配给已经为工作负载运行的 Pod。

如果负载减少，并且 Pod 的数量高于配置的最小值，
HorizontalPodAutoscaler 会指示工作负载资源（Deployment、StatefulSet 或其他类似资源）缩减。

水平 Pod 自动扩缩不适用于无法扩缩的对象（例如：DaemonSet。）

HorizontalPodAutoscaler 被实现为 Kubernetes API
资源和控制器。资源决定了控制器的行为。
在 Kubernetes 控制平面内运行的水平
Pod 自动扩缩控制器会定期调整其目标（例如：Deployment）的所需规模，以匹配观察到的指标，
例如，平均 CPU 利用率、平均内存利用率或你指定的任何其他自定义指标。

使用水平 Pod 自动扩缩[演练示例](/zh-cn/docs/tasks/run-application/horizontal-pod-autoscale-walkthrough/)。

## HorizontalPodAutoscaler 是如何工作的？ {#how-does-a-horizontalpodautoscaler-work}

graph BT

hpa[HorizontalPodAutoscaler] --> scale[规模]

subgraph rc[Deployment]
    scale
end

scale -.-> pod1[Pod 1]
scale -.-> pod2[Pod 2]
scale -.-> pod3[Pod N]

classDef hpa fill:#D5A6BD,stroke:#1E1E1D,stroke-width:1px,color:#1E1E1D;
classDef rc fill:#F9CB9C,stroke:#1E1E1D,stroke-width:1px,color:#1E1E1D;
classDef scale fill:#B6D7A8,stroke:#1E1E1D,stroke-width:1px,color:#1E1E1D;
classDef pod fill:#9FC5E8,stroke:#1E1E1D,stroke-width:1px,color:#1E1E1D;
class hpa hpa;
class rc rc;
class scale scale;
class pod1,pod2,pod3 pod

图 1. HorizontalPodAutoscaler 控制 Deployment 及其 ReplicaSet 的规模

Kubernetes 将水平 Pod 自动扩缩实现为一个间歇运行的控制回路（它不是一个连续的过程）。间隔由
[`kube-controller-manager`](/zh-cn/docs/reference/command-line-tools-reference/kube-controller-manager/)
的 `--horizontal-pod-autoscaler-sync-period` 参数设置（默认间隔为 15 秒）。

在每个时间段内，控制器管理器都会根据每个 HorizontalPodAutoscaler 定义中指定的指标查询资源利用率。
控制器管理器找到由 `scaleTargetRef` 定义的目标资源，然后根据目标资源的 `.spec.selector` 标签选择 Pod，
并从资源指标 API（针对每个 Pod 的资源指标）或自定义指标获取指标 API（适用于所有其他指标）。

* 对于按 Pod 统计的资源指标（如 CPU），控制器从资源指标 API 中获取每一个
  HorizontalPodAutoscaler 指定的 Pod 的度量值，如果设置了目标使用率，控制器获取每个 Pod
  中的容器[资源请求使用](/zh-cn/docs/concepts/configuration/manage-resources-containers/#requests-and-limits)情况，
  并计算资源使用率。如果设置了目标原始值，将直接使用原始度量值（不再计算百分比）。
  接下来，控制器根据平均的资源使用率或原始值（取决于指定的目标的类型）计算出扩缩的比例，进而计算出目标副本数。

  

  需要注意的是，如果 Pod 某些容器未设置相关的资源请求，那么此 Pod 的 CPU 利用率将不会被定义。
  自动扩缩器将不会为该指标采取任何操作。下面的[算法细节](#algorithm-details)章节将会介绍自动扩缩的详细算法。

* 如果 Pod 使用自定义指示，控制器机制与资源指标类似，区别在于自定义指标只使用原始值，而不是使用率。

* 如果 Pod 使用对象指标和外部指标（每个指标描述一个对象信息）。
  这个指标将直接根据目标设定值相比较，并生成一个上面提到的扩缩比例。
  在 `autoscaling/v2` 版本 API 中，这个指标也可以根据 Pod 数量平分后再计算。

HorizontalPodAutoscaler 的常见用途是将其配置为从聚合 API
（`metrics.k8s.io`、`custom.metrics.k8s.io` 或 `external.metrics.k8s.io`）获取指标。
`metrics.k8s.io` API 通常由名为 Metrics Server 的插件提供，需要单独启动。有关资源指标的更多信息，
请参阅 [Metrics Server](/zh-cn/docs/tasks/debug/debug-cluster/resource-metrics-pipeline/#metrics-server)。

对 [Metrics API 的支持](#support-for-metrics-apis)解释了这些不同 API 的稳定性保证和支持状态。

HorizontalPodAutoscaler 控制器访问支持扩缩的相应工作负载资源（例如：Deployment 和 StatefulSet）。
这些资源每个都有一个名为 `scale` 的子资源，该接口允许你动态设置副本的数量并检查它们的每个当前状态。
有关 Kubernetes API 子资源的一般信息，
请参阅 [Kubernetes API 概念](/zh-cn/docs/reference/using-api/api-concepts/)。

### 算法细节   {#algorithm-details}

从最基本的角度来看，Pod 水平自动扩缩控制器根据当前指标和期望指标来计算扩缩比例。

```math
\begin{equation*}
\text{期望副本数} = ceil\left\lceil \text{当前副本数} \times \frac{\text{当前指标}}{\text{期望指标}} \right\rceil
\end{equation*}
```

例如，如果当前指标值为 `200m`，而期望值为 `100m`，则副本数将加倍，
因为 \\( { 200.0 \div 100.0 } = 2.0 \\)。如果当前值为 `50m`，则副本数将减半，
因为  \\( { 50.0 \div 100.0 } = 0.5 \\)。如果比率足够接近 1.0（在[可配置的容差范围内](#tolerance)，默认为 0.1），
则控制平面会跳过扩缩操作。

如果 HorizontalPodAutoscaler 指定的是 `targetAverageValue` 或 `targetAverageUtilization`，
那么将会把指定 Pod 度量值的平均值做为 `currentMetricValue`。

在检查容差并决定最终值之前，控制平面还会考虑是否缺少任何指标，
以及有多少 Pod [`Ready`](/zh-cn/docs/concepts/workloads/pods/pod-lifecycle/#pod-conditions)。

对于 Pod 级资源指标，所有设置了删除时间戳（即正在终止或删除中的对象）的 Pod 都会被忽略，
同时会剔除所有处于 Failed 状态的 Pod。对于外部指标和对象指标，副本数基于处于 Running 且
Ready 状态的 Pod 数量进行计算；对于已进入终止流程但仍处于 Ready 状态的 Pod，仍会被计入副本总数。

如果某个 Pod 缺失度量值，它将会被搁置，只在最终确定扩缩数量时再考虑。

当使用 CPU 指标来扩缩时，任何还未就绪（还在初始化，或者可能是不健康的）状态的 Pod
**或**最近的指标度量值采集于就绪状态前的 Pod，该 Pod 也会被搁置。

由于技术限制，HorizontalPodAutoscaler 控制器在确定是否保留某些 CPU 指标时无法准确确定 Pod 首次就绪的时间。
相反，如果 Pod 未准备好并在其启动后的一个可配置的短时间窗口内转换为准备好，它会认为 Pod “尚未准备好”。
此值使用 `--horizontal-pod-autoscaler-initial-readiness-delay` 命令行选项配置，默认值为 30 秒。
一旦 Pod 准备就绪，如果它发生在自启动后较长的、可配置的时间内，它就会认为任何向准备就绪的转换都是第一个。
此值由 `--horizontal-pod-autoscaler-cpu-initialization-period` 命令行选项配置，默认值为 5 分钟。

在排除掉被搁置的 Pod 后，扩缩比例就会根据 \\( 当前指标 \over 预期指标 \\)
计算出来。

如果缺失某些度量值，控制平面会更保守地重新计算平均值，在需要缩小时假设这些 Pod 消耗了目标值的 100%，
在需要放大时假设这些 Pod 消耗了 0% 目标值。这可以在一定程度上抑制扩缩的幅度。

此外，如果存在任何尚未就绪的 Pod，工作负载会在不考虑遗漏指标或尚未就绪的 Pod 的情况下进行扩缩，
控制器保守地假设尚未就绪的 Pod 消耗了期望指标的 0%，从而进一步降低了扩缩的幅度。

考虑到尚未准备好的 Pod 和缺失的指标后，控制器会重新计算使用率。
如果新的比率与扩缩方向相反，或者在容差范围内，则控制器不会执行任何扩缩操作。
在其他情况下，新比率用于决定对 Pod 数量的任何更改。

注意，平均利用率的**原始**值是通过 HorizontalPodAutoscaler 状态体现的，
而不考虑尚未准备好的 Pod 或缺少的指标，即使使用新的使用率也是如此。

如果创建 HorizontalPodAutoscaler 时指定了多个指标，
那么会按照每个指标分别计算扩缩副本数，取最大值进行扩缩。
如果任何一个指标无法顺利地计算出扩缩副本数（比如，通过 API 获取指标时出错），
并且可获取的指标建议缩容，那么本次扩缩会被跳过。
这表示，如果一个或多个指标给出的 `desiredReplicas` 值大于当前值，HPA 仍然能实现扩容。

最后，在 HPA 控制器执行扩缩操作之前，会记录扩缩建议信息。
控制器会在操作时间窗口中考虑所有的建议信息，并从中选择得分最高的建议。
你可以使用 `--horizontal-pod-autoscaler-downscale-stabilization` 命令行选项来配置这个值，
默认值为 5 分钟。这个配置可以让系统更为平滑地进行缩容操作，从而消除短时间内指标值快速波动产生的影响。

## Pod 准备就绪和自动伸缩指标   {#pod-readiness-and-autoscaling-metrics}

HorizontalPodAutoscaler（HPA）控制器有两个命令行选项会影响 Pod 启动期间如何收集其 CPU 指标：

1. `--horizontal-pod-autoscaler-cpu-initialization-period`（默认：5 分钟）

   此命令行选项所定义的是 Pod 启动后的一个时间窗口，在此期间内其 **CPU 使用率被忽略**，除非：

   - Pod 处于 `Ready` 状态**且**
   - 指标样本完全是在它处于 `Ready` 状态期间采集的。

   

   此命令行选项有助于**排除初始化 Pod 中的误导性高 CPU 使用率**
   （例如，Java 应用程序预热）对 HPA 扩缩决策的影响。

2. `--horizontal-pod-autoscaler-initial-readiness-delay`（默认：30 秒）

   这定义了一个短暂的延迟期，在 Pod 启动后，HPA 控制器将当前为 `Unready`
   的 Pod 视为仍在初始化中，**即使它们之前曾短暂转变为 `Ready`**。

   

   其设计目的是：

   - 避免包含在启动期间快速在 `Ready` 和 `Unready` 之间波动的 Pod。
   - 确保在 HPA 认为它们的指标有效之前，初始就绪信号的稳定性。

你可以在集群范围设置这些命令行选项。

### Pod 就绪的关键行为  {#pod-readiness-key-behaviors}

- 如果一个 Pod 是 `Ready` 状态并且保持 `Ready`，即使在延迟期间，
  它也可以被视为贡献指标。
- 如果一个 Pod 在 `Ready` 和 `Unready` 之间快速切换，那么它的指标将被忽略，
  直到它被认为稳定在 `Ready` 状态。

### Pod 就绪的良好实践  {#pod-readiness-good-practices}

- 配置 `startupProbe`，使其在高 CPU 使用率结束之前不会传递，或
- 使用 `initialDelaySeconds`，确保你的 `readinessProbe` **在 CPU 峰值过后**才报告为 `Ready`。

理想情况下，还应设置 `--horizontal-pod-autoscaler-cpu-initialization-period`，以**覆盖启动持续时间**。

## API 对象   {#api-object}

HorizontalPodAutoscaler 是 Kubernetes `autoscaling` API 组中的一个 API 类别。
当前的稳定版本可以在 `autoscaling/v2` API 版本中找到，其中包括对基于内存和自定义指标执行扩缩的支持。
在使用 `autoscaling/v1` 时，`autoscaling/v2` 中引入的新字段作为注释保留。

创建 HorizontalPodAutoscaler 对象时，需要确保所给的名称是一个合法的
[DNS 子域名](/zh-cn/docs/concepts/overview/working-with-objects/names#dns-subdomain-names)。
有关 API 对象的更多信息，请查阅
[HorizontalPodAutoscaler 对象文档](/docs/reference/generated/kubernetes-api//#horizontalpodautoscaler-v2-autoscaling)。

## 工作量规模的稳定性 {#flapping}

在使用 HorizontalPodAutoscaler 管理一组副本的规模时，由于评估的指标的动态特性，
副本的数量可能会经常波动。这有时被称为 **抖动（thrashing）** 或 **波动（flapping）**。
它类似于控制论中的 **滞后（hysteresis）** 概念。

## 滚动升级时扩缩   {#autoscaling-during-rolling-update}

Kubernetes 允许你在 Deployment 上执行滚动更新。在这种情况下，Deployment 为你管理下层的 ReplicaSet。
当你为一个 Deployment 配置自动扩缩时，你要为每个 Deployment 绑定一个 HorizontalPodAutoscaler。
HorizontalPodAutoscaler 管理 Deployment 的 `replicas` 字段。
Deployment Controller 负责设置下层 ReplicaSet 的 `replicas` 字段，
以便确保在上线及后续过程副本个数合适。

如果你对一个副本个数被自动扩缩的 StatefulSet 执行滚动更新，该 StatefulSet
会直接管理它的 Pod 集合（不存在类似 ReplicaSet 这样的中间资源）。

## 对资源指标的支持   {#support-for-resource-metrics}

HPA 的任何目标资源都可以基于其中的 Pod 的资源用量来实现扩缩。
在定义 Pod 规约时，类似 `cpu` 和 `memory` 这类资源请求必须被设定。
这些设定值被用来确定资源利用量并被 HPA 控制器用来对目标资源完成扩缩操作。
要使用基于资源利用率的扩缩，可以像下面这样指定一个指标源：

```yaml
type: Resource
resource:
  name: cpu
  target:
    type: Utilization
    averageUtilization: 60
```

基于这一指标设定，HPA 控制器会维持扩缩目标中的 Pod 的平均资源利用率在 60%。
利用率是 Pod 的当前资源用量与其请求值之间的比值。
关于如何计算利用率以及如何计算平均值的细节可参考[算法](#algorithm-details)小节。

由于所有的容器的资源用量都会被累加起来，Pod 的总体资源用量值可能不会精确体现各个容器的资源用量。
这一现象也会导致一些问题，例如某个容器运行时的资源用量非常高，但因为 Pod
层面的资源用量总值让人在可接受的约束范围内，HPA 不会执行扩大目标对象规模的操作。

### 容器资源指标   {#container-resource-metrics}

HorizontalPodAutoscaler API 也支持容器指标源，这时 HPA 可以跟踪记录一组 Pod
中各个容器的资源用量，进而触发扩缩目标对象的操作。
容器资源指标的支持使得你可以为特定 Pod 中最重要的容器配置规模扩缩阈值。
例如，如果你有一个 Web 应用和一个提供记录日志功能的边车容器，你可以基于 Web
应用的资源用量来执行扩缩，忽略边车容器的存在及其资源用量。

如果你更改扩缩目标对象，令其使用新的、包含一组不同的容器的 Pod 规约，你就需要修改
HPA 的规约才能基于新添加的容器来执行规模扩缩操作。
如果指标源中指定的容器不存在或者仅存在于部分 Pod 中，那么这些 Pod 会被忽略，
HPA 会重新计算资源用量值。参阅[算法](#algorithm-details)小节进一步了解计算细节。
要使用容器资源用量来完成自动扩缩，可以像下面这样定义指标源：

```yaml
type: ContainerResource
containerResource:
  name: cpu
  container: application
  target:
    type: Utilization
    averageUtilization: 60
```

在上面的例子中，HPA 控制器会对目标对象执行扩缩操作以确保所有 Pod 中
`application` 容器的平均 CPU 用量为 60%。

如果你要更改 HorizontalPodAutoscaler 所跟踪记录的容器的名称，你可以按一定顺序来执行这一更改，
确保在应用更改的过程中用来判定扩缩行为的容器可用。
在更新定义容器的资源（如 Deployment）之前，你需要更新相关的 HPA，
使之能够同时跟踪记录新的和老的容器名称。这样，HPA 就能够在整个更新过程中继续计算并提供扩缩操作建议。

一旦你已经将容器名称变更这一操作应用到整个负载对象至上，就可以从 HPA
的规约中去掉老的容器名称，完成清理操作。

## 扩展自定义指标 {#scaling-on-custom-metrics}

（之前的 `autoscaling/v2beta2` API 版本将此功能作为 Beta 功能提供）

如果你使用 `autoscaling/v2` API 版本，则可以将 HorizontalPodAutoscaler
配置为基于自定义指标（未内置于 Kubernetes 或任何 Kubernetes 组件）进行扩缩。
HorizontalPodAutoscaler 控制器能够从 Kubernetes API 查询这些自定义指标。

有关要求，请参阅对 [Metrics API 的支持](#support-for-metrics-apis)。

## 基于多个指标来执行扩缩 {#scaling-on-multiple-metrics}

（之前的 `autoscaling/v2beta2` API 版本将此功能作为 Beta 功能提供）

如果你使用 `autoscaling/v2` API 版本，你可以为 HorizontalPodAutoscaler 指定多个指标以进行扩缩。
HorizontalPodAutoscaler 控制器评估每个指标，并根据该指标提出一个新的比例。
HorizontalPodAutoscaler 采用为每个指标推荐的最大比例，
并将工作负载设置为该大小（前提是这不大于你配置的总体最大值）。

## 对 Metrics API 的支持   {#support-for-metrics-apis}

默认情况下，HorizontalPodAutoscaler 控制器会从一系列的 API 中检索度量值。
集群管理员需要确保下述条件，以保证 HPA 控制器能够访问这些 API：

- 启用了 [API 聚合层](/zh-cn/docs/tasks/extend-kubernetes/configure-aggregation-layer/)

- 相应的 API 已注册：

  - 对于资源指标，将使用 `metrics.k8s.io` [API](/zh-cn/docs/reference/external-api/metrics.v1beta1/)，
    一般由 [metrics-server](https://github.com/kubernetes-incubator/metrics-server) 提供。
    它可以作为集群插件启动。

    
    
    HorizontalPodAutoscaler 目前支持用于资源指标的 `metrics.k8s.io/v1beta1` API。
    它尚不支持 `metrics.k8s.io/v1`。
    

  

  - 对于自定义指标，将使用 `custom.metrics.k8s.io` [API](/zh-cn/docs/reference/external-api/metrics.v1beta1/)。
    它由其他度量指标方案厂商的“适配器（Adapter）” API 服务器提供。
    检查你的指标管道以查看是否有可用的 Kubernetes 指标适配器。

  - 对于外部指标，将使用 `external.metrics.k8s.io` [API](/zh-cn/docs/reference/external-api/metrics.v1beta1/)。
    可能由上面的自定义指标适配器提供。

关于指标来源以及其区别的更多信息，请参阅相关的设计文档，
[HPA V2](https://git.k8s.io/design-proposals-archive/autoscaling/hpa-v2.md)，
[custom.metrics.k8s.io](https://git.k8s.io/design-proposals-archive/instrumentation/custom-metrics-api.md) 和
[external.metrics.k8s.io](https://git.k8s.io/design-proposals-archive/instrumentation/external-metrics-api.md)。

关于如何使用它们的示例，
请参考[使用自定义指标的教程](/zh-cn/docs/tasks/run-application/horizontal-pod-autoscale-walkthrough/#autoscaling-on-multiple-metrics-and-custom-metrics)
和[使用外部指标的教程](/zh-cn/docs/tasks/run-application/horizontal-pod-autoscale-walkthrough/#autoscaling-on-metrics-not-related-to-kubernetes-objects)。

## 可配置的扩缩行为 {#configurable-scaling-behavior}

（之前的 `autoscaling/v2beta2` API 版本将此功能作为 Beta 功能提供）

如果你使用 `v2` HorizontalPodAutoscaler API，你可以使用 `behavior` 字段
（请参阅 [API 参考](/zh-cn/docs/reference/kubernetes-api/workload-resources/horizontal-pod-autoscaler-v2/#HorizontalPodAutoscalerSpec)）
来配置单独的放大和缩小行为。你可以通过在行为字段下设置 `scaleUp` 和/或 `scaleDown` 来指定这些行为。

扩缩策略允许你在扩缩容时控制副本数量变化的速率。
此外，还可以通过两个设置来防止频繁[波动](#flapping)：
你可以指定一个“稳定窗口“来平滑副本数量的变化，
也可以设置一个容差值，用于忽略低于指定阈值的小幅指标波动。

### 扩缩策略 {#scaling-policies}

可以在规约的 `behavior` 部分中指定一个或多个扩缩策略。当指定多个策略时，
允许最大更改量的策略是默认选择的策略。以下示例显示了缩小时的这种行为：

```yaml
behavior:
  scaleDown:
    policies:
    - type: Pods
      value: 4
      periodSeconds: 60
    - type: Percent
      value: 10
      periodSeconds: 60
```

`periodSeconds` 表示在过去的多长时间内要求策略值为真。
你可以设置 `periodSeconds` 的最大值为 1800（半小时）。
第一个策略（Pods）允许在一分钟内最多缩容 4 个副本。第二个策略（Percent）
允许在一分钟内最多缩容当前副本个数的百分之十。

由于默认情况下会选择容许更大程度作出变更的策略，只有 Pod 副本数大于 40 时，
第二个策略才会被采用。如果副本数为 40 或者更少，则应用第一个策略。
例如，如果有 80 个副本，并且目标必须缩小到 10 个副本，那么在第一步中将减少 8 个副本。
在下一轮迭代中，当副本的数量为 72 时，10% 的 Pod 数为 7.2，但是这个数字向上取整为 8。
在自动扩缩控制器的每个循环中，将根据当前副本的数量重新计算要更改的 Pod 数量。
当副本数量低于 40 时，应用第一个策略（Pods），一次减少 4 个副本。

可以指定扩缩方向的 `selectPolicy` 字段来更改策略选择。
通过设置 `Min` 的值，它将选择副本数变化最小的策略。
将该值设置为 `Disabled` 将完全禁用该方向的扩缩。

### 稳定窗口 {#stabilization-window}

当用于扩缩的指标不断波动时，稳定窗口用于限制副本计数的[波动](#flapping)。
自动扩缩算法使用此窗口来推断先前的期望状态并避免对工作负载规模进行不必要的更改。

例如，在以下示例代码段中，为 `scaleDown` 指定了稳定窗口。

```yaml
behavior:
  scaleDown:
    stabilizationWindowSeconds: 300
```

当指标显示目标应该缩容时，自动扩缩算法查看之前计算的期望状态，并使用指定时间间隔内的最大值。
在上面的例子中，过去 5 分钟的所有期望状态都会被考虑。

这近似于滚动最大值，并避免了扩缩算法频繁删除 Pod 而又触发重新创建等效 Pod。

### 容忍阈值 {#tolerance}

`tolerance` 字段用于配置指标波动的阈值，避免自动扩缩器因小幅变化而触发扩缩容操作。

该容忍阈值指的是在期望指标值附近的一个波动范围，在这个范围内不会触发扩缩容操作。
例如，假设 HorizontalPodAutoscaler 配置了目标内存使用量为 100MiB，并设置了 5% 的扩容容忍阈值：

```yaml
behavior:
  scaleUp:
    tolerance: 0.05 # 5% 容忍度用于扩容
```

在这种配置下，只有当内存使用量超过 105MiB（即比目标值高出 5%）时，HPA 算法才会考虑进行扩容。

如果你未设置该字段，HPA 将使用集群范围内默认的 10% 容忍阈值。你可以通过
[kube-controller-manager](/zh-cn/docs/reference/command-line-tools-reference/kube-controller-manager/)
的 `--horizontal-pod-autoscaler-tolerance` 命令行参数，分别调整扩容和缩容的默认容忍阈值。
（注意，无法通过 Kubernetes API 来配置这个默认值。）

### 默认行为 {#default-behavior}

要使用自定义扩缩，不必指定所有字段。只有需要自定义的字段才需要指定。
这些自定义值与默认值合并。默认值与 HPA 算法中的现有行为匹配。

```yaml
behavior:
  scaleDown:
    stabilizationWindowSeconds: 300
    policies:
    - type: Percent
      value: 100
      periodSeconds: 15
  scaleUp:
    stabilizationWindowSeconds: 0
    policies:
    - type: Percent
      value: 100
      periodSeconds: 15
    - type: Pods
      value: 4
      periodSeconds: 15
    selectPolicy: Max
```

用于缩小稳定窗口的时间为 **300**
秒（或是 `--horizontal-pod-autoscaler-downscale-stabilization` 命令行选项设定值）。
只有一种缩容的策略，允许 100% 删除当前运行的副本，这意味着扩缩目标可以缩小到允许的最小副本数。
对于扩容，没有稳定窗口。当指标显示目标应该扩容时，目标会立即扩容。
这里有两种策略，每 15 秒最多添加 4 个 Pod 或 100% 当前运行的副本数，直到 HPA 达到稳定状态。

### 示例：更改缩容稳定窗口 {#example-change-downscale-stabilization-window}

将下面的 behavior 配置添加到 HPA 中，可提供一个 1 分钟的自定义缩容稳定窗口：

```yaml
behavior:
  scaleDown:
    stabilizationWindowSeconds: 60
```

### 示例：限制缩容速率 {#example-limit-scale-down-rate}

将下面的 behavior 配置添加到 HPA 中，可限制 Pod 被 HPA 删除速率为每分钟 10%：

```yaml
behavior:
  scaleDown:
    policies:
    - type: Percent
      value: 10
      periodSeconds: 60
```

为了确保每分钟删除的 Pod 数不超过 5 个，可以添加第二个缩容策略，大小固定为 5，并将 `selectPolicy` 设置为最小值。
将 `selectPolicy` 设置为 `Min` 意味着自动扩缩器会选择影响 Pod 数量最小的策略：

```yaml
behavior:
  scaleDown:
    policies:
    - type: Percent
      value: 10
      periodSeconds: 60
    - type: Pods
      value: 5
      periodSeconds: 60
    selectPolicy: Min
```

### 示例：禁用缩容 {#example-disable-scale-down}

`selectPolicy` 的值 `Disabled` 会关闭对给定方向的缩容。
因此使用以下策略，将会阻止缩容：

```yaml
behavior:
  scaleDown:
    selectPolicy: Disabled
```

## kubectl 对 HorizontalPodAutoscaler 的支持 {#support-for-horizontalpodautoscaler-in-kubectl}

与每个 API 资源一样，HorizontalPodAutoscaler 都被 `kubectl` 以标准方式支持。
你可以使用 `kubectl create` 命令创建一个新的自动扩缩器。
你可以通过 `kubectl get hpa` 列出自动扩缩器或通过 `kubectl describe hpa` 获取详细描述。
最后，你可以使用 `kubectl delete hpa` 删除自动扩缩器。

此外，还有一个特殊的 `kubectl autoscale` 命令用于创建 HorizontalPodAutoscaler 对象。
例如，执行 `kubectl autoscale rs foo --min=2 --max=5 --cpu=80%`
将为 ReplicaSet **foo** 创建一个自动扩缩器，目标 CPU 利用率设置为 `80%`，副本数在 2 到 5 之间。

## 扩缩到零和从零扩缩   {#scaling-to-and-from-zero}

对于基于[自定义指标](#scaling-on-custom-metrics)（对象）或外部指标进行扩缩的
HorizontalPodAutoscaler，你可以将 `spec.minReplicas` 设置为 `0`。
当没有负载时，工作负载会一直缩容到零个副本，
而当指标表明至少需要一个副本时再扩容回来。这对于长时间空闲且运行成本高昂的工作负载非常有用，
例如偶尔使用的队列消费者或需要 GPU 等专用硬件的作业。

扩缩到零仅支持对象和外部指标。它不适用于资源指标（例如 CPU 或内存利用率），
因为这些指标只能在运行中的 Pod 上测量。设置 `minReplicas: 0` 要求至少配置一个对象或外部指标；
否则 API 服务器将拒绝该 HorizontalPodAutoscaler。

此行为由 `HPAScaleToZero`
[特性门控](/zh-cn/docs/reference/command-line-tools-reference/feature-gates/)控制，默认已启用。
该特性门控必须在
kube-apiserver（允许 `minReplicas: 0`）
和 kube-controller-manager
（执行扩缩操作）上同时启用。

当 HPA 将某个工作负载保持在零个副本时，它会在 HorizontalPodAutoscaler 的状态上记录一个
`ScaledToZero` 状况并将其设置为 `True`。HPA 使用此状况来区分是由其自身缩容到零的工作负载
（当指标恢复时会重新扩容），还是通过将副本数设置为 `0`
而[手动停用](#implicit-maintenance-mode-deactivation)的工作负载。
一旦工作负载被重新扩容，该状况将被设置为 `False`，原因为 `NotScaledToZero`。

## 隐式维护状态禁用 {#implicit-maintenance-mode-deactivation}

你可以在不必更改 HPA 配置的情况下隐式地为某个目标禁用 HPA。
如果此目标的期望副本个数被设置为 0，而 HPA 的最小副本个数大于 0，
则 HPA 会停止调整目标（并将其自身的 `ScalingActive` 状况设置为 `false`），
直到你通过手动调整目标的期望副本个数或 HPA 的最小副本个数来重新激活。

### 将 Deployment 和 StatefulSet 迁移到水平自动扩缩 {#migrating-deployments-and-statefulsets-to-horizontal-autoscaling}

当启用 HPA 时，建议从它们的清单中删除
Deployment 和/或 StatefulSet 的 `spec.replicas` 的值。
如果不这样做，则只要应用对该对象的更改，例如通过 `kubectl apply -f deployment.yaml`，
这将指示 Kubernetes 将当前 Pod 数量扩缩到 `spec.replicas` 键的值。这可能不是所希望的，
并且当 HPA 处于活动状态时，可能会导致波动或反复变化的行为，进而带来麻烦。

请记住，删除 `spec.replicas` 可能会导致 Pod 计数一次性降级，因为此键的默认值为 1
（参考 [Deployment 副本](/zh-cn/docs/concepts/workloads/controllers/deployment#replicas)）。
更新后，除 1 之外的所有 Pod 都将开始其终止程序。之后的任何部署应用程序都将正常运行，
并根据需要遵守滚动更新配置。你可以根据修改部署的方式选择以下两种方法之一来避免这种降级：

1. `kubectl apply edit-last-applied deployment/<Deployment 名称>`
2. 在编辑器中，删除 `spec.replicas`。当你保存并退出编辑器时，`kubectl` 会应用更新。
   在此步骤中不会更改 Pod 计数。
3. 你现在可以从清单中删除 `spec.replicas`。如果你使用源代码管理，
   还应提交你的更改或采取任何其他步骤来修改源代码，以适应你如何跟踪更新。
4. 从这里开始，你可以运行 `kubectl apply -f deployment.yaml`

使用[服务器端 Apply](/zh-cn/docs/reference/using-api/server-side-apply/) 机制，
你可以遵循[交出所有权](/zh-cn/docs/reference/using-api/server-side-apply/#transferring-ownership)说明，
该指南涵盖了这个确切的用例。

## 

如果你在集群中配置自动扩缩，
你可能还需要考虑使用[节点自动扩缩](/doc/concepts/cluster-administration/node-autoscaling/)来确保所运行的节点数目合适。

有关 HorizontalPodAutoscaler 的更多信息：

* 阅读水平 Pod 自动扩缩的[演练示例](/zh-cn/docs/tasks/run-application/horizontal-pod-autoscale-walkthrough/)。
* 阅读 [`kubectl autoscale`](/zh-cn/docs/reference/generated/kubectl/kubectl-commands/#autoscale) 的文档。
* 如果你想编写自己的自定义指标适配器，
  请查看 [boilerplate](https://github.com/kubernetes-sigs/custom-metrics-apiserver) 以开始使用。
* 阅读 [API 参考](/zh-cn/docs/reference/kubernetes-api/workload-resources/horizontal-pod-autoscaler-v2/)。
