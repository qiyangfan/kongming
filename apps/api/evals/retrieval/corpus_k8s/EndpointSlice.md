# EndpointSlice

## EndpointSlice API {#endpointslice-resource}

在 Kubernetes 中，`EndpointSlice` 包含对一组网络端点的引用。
控制面会自动为设置了选择算符的
Kubernetes Service 创建 EndpointSlice。
这些 EndpointSlice 将包含对与 Service 选择算符匹配的所有 Pod 的引用。
EndpointSlice 通过唯一的 IP 地址簇、协议、端口号和 Service 名称将网络端点组织在一起。
EndpointSlice 的名称必须是合法的
[DNS 子域名](/zh-cn/docs/concepts/overview/working-with-objects/names#dns-subdomain-names)。

例如，下面是 Kubernetes Service `example` 所拥有的 EndpointSlice 对象示例。

```yaml
apiVersion: discovery.k8s.io/v1
kind: EndpointSlice
metadata:
  name: example-abc
  labels:
    kubernetes.io/service-name: example
addressType: IPv4
ports:
  - name: http
    protocol: TCP
    port: 80
endpoints:
  - addresses:
      - "10.1.2.3"
    conditions:
      ready: true
    hostname: pod-1
    nodeName: node-1
    zone: us-west2-a
```

默认情况下，控制面创建和管理的 EndpointSlice 将包含不超过 100 个端点。
你可以使用 kube-controller-manager
的 `--max-endpoints-per-slice` 标志设置此值，最大值为 1000。

当涉及如何路由内部流量时，EndpointSlice 充当

的决策依据。

### 地址类型

EndpointSlice 支持两种地址类型：

* IPv4
* IPv6

每个 `EndpointSlice` 对象代表一个特定的 IP 地址类型。如果你有一个支持 IPv4 和 IPv6 的 Service，
那么将至少有两个 `EndpointSlice` 对象（一个用于 IPv4，一个用于 IPv6）。

### 状况

EndpointSlice API 存储了可能对使用者有用的、有关端点的状况。
这三个状况分别是 `serving`、`terminating` 和 `ready`。

#### Serving（服务中）

`serving` 状况表示端点目前正在提供响应，且因此应将其用作 Service 流量的目标。
对于由 Pod 支持的端点，此状况对应于 Pod 的 `Ready` 状况。

#### Terminating（终止中）

`terminating` 状况表示端点正在终止中。对于由 Pod 支持的端点，
当 Pod 首次被删除时（即收到删除时间戳时，但很可能在容器实际退出之前），会设置此状况。

服务代理通常会忽略处于 `terminating` 状态的端点，但如果所有可用端点都处于 `terminating`，
服务代理可能仍会将流量路由到同时具有 `serving` 和 `terminating` 的端点。
（这样有助于在底层 Pod 滚动更新过程中确保 Service 流量不会中断。）

#### Ready（就绪）

`ready` 状况本质上是检查 "`serving` 且不是 `terminating`" 的一种简化方式
（不过对于将 `spec.publishNotReadyAddresses` 设置为 `true` 的 Service，`ready` 状况始终设置为 `true`）。

### 拓扑信息   {#topology}

EndpointSlice 中的每个端点都可以包含一定的拓扑信息。
拓扑信息包括端点的位置，对应节点、可用区的信息。
这些信息体现为 EndpointSlices 的如下端点字段：

* `nodeName` - 端点所在的 Node 名称；
* `zone` - 端点所处的可用区。

### 管理   {#management}

通常，控制面（尤其是端点切片的控制器）
会创建和管理 EndpointSlice 对象。EndpointSlice 对象还有一些其他使用场景，
例如作为服务网格（Service Mesh）的实现。
这些场景都会导致有其他实体或者控制器负责管理额外的 EndpointSlice 集合。

为了确保多个实体可以管理 EndpointSlice 而且不会相互产生干扰，
Kubernetes 定义了
`endpointslice.kubernetes.io/managed-by`，用来标明哪个实体在管理某个 EndpointSlice。
端点切片控制器会在自己所管理的所有 EndpointSlice 上将该标签值设置为
`endpointslice-controller.k8s.io`。
管理 EndpointSlice 的其他实体也应该为此标签设置一个唯一值。

### 属主关系   {#ownership}

在大多数场合下，EndpointSlice 都由某个 Service 所有，
（因为）该端点切片正是为该服务跟踪记录其端点。这一属主关系是通过为每个 EndpointSlice
设置一个属主（owner）引用，同时设置 `kubernetes.io/service-name` 标签来标明的，
目的是方便查找隶属于某 Service 的所有 EndpointSlice。

### EndpointSlices 的分布问题  {#distribution-of-endpointslices}

每个 EndpointSlice 都有一组端口值，适用于资源内的所有端点。
当为 Service 使用命名端口时，Pod 可能会就同一命名端口获得不同的端口号，
因而需要不同的 EndpointSlice。

控制面尝试尽量将 EndpointSlice 填满，不过不会主动地在若干 EndpointSlice
之间执行再平衡操作。这里的逻辑也是相对直接的：

1. 列举所有现有的 EndpointSlices，移除那些不再需要的端点并更新那些已经变化的端点。
2. 列举所有在第一步中被更改过的 EndpointSlices，用新增加的端点将其填满。
3. 如果还有新的端点未被添加进去，尝试将这些端点添加到之前未更改的切片中，
   或者创建新切片。

这里比较重要的是，与在 EndpointSlice 之间完成最佳的分布相比，第三步中更看重限制
EndpointSlice 更新的操作次数。例如，如果有 10 个端点待添加，有两个 EndpointSlice
中各有 5 个空位，上述方法会创建一个新的 EndpointSlice 而不是将现有的两个
EndpointSlice 都填满。换言之，与执行多个 EndpointSlice 更新操作相比较，
方法会优先考虑执行一个 EndpointSlice 创建操作。

由于 kube-proxy 在每个节点上运行并监视 EndpointSlice 状态，EndpointSlice
的每次变更都变得相对代价较高，因为这些状态变化要传递到集群中每个节点上。
这一方法尝试限制要发送到所有节点上的变更消息个数，即使这样做可能会导致有多个
EndpointSlice 没有被填满。

在实践中，上面这种并非最理想的分布是很少出现的。大多数被 EndpointSlice
控制器处理的变更都是足够小的，可以添加到某已有 EndpointSlice 中去的。
并且，假使无法添加到已有的切片中，不管怎样都很快就会创建一个新的
EndpointSlice 对象。Deployment 的滚动更新为重新为 EndpointSlice
打包提供了一个自然的机会，所有 Pod 及其对应的端点在这一期间都会被替换掉。

### 重复的端点   {#duplicate-endpoints}

由于 EndpointSlice 变化的自身特点，端点可能会同时出现在不止一个 EndpointSlice
中。鉴于不同的 EndpointSlice 对象在不同时刻到达 Kubernetes 的监视/缓存中，
这种情况的出现是很自然的。

EndpointSlice API 的客户端必须遍历与 Service 关联的所有现有 EndpointSlices，
并构建唯一网络端点的完整列表。值得一提的是端点可能在不同的 EndpointSlices 中重复。

你可以在 `kube-proxy` 中的 `EndpointSliceCache` 代码中找到有关如何执行此端点聚合和重复数据删除的参考实现。

### EndpointSlice 镜像    {#endpointslice-mirroring}

EndpointSlice API 是旧版 Endpoints API 的替代方案。
为了保持与旧版控制器和用户工作负载的兼容性
（例如期望由  基于 Endpoints 资源来路由流量），
集群的控制平面会将大多数用户创建的 Endpoints 资源镜像到相应的 EndpointSlice 中。

（不过，与 Endpoints API 的其他部分一样，此特性也已被弃用。
对于无选择算符的 Service，用户如果需要手动指定端点，应该直接创建 EndpointSlice 资源，
而不是创建 Endpoints 资源并允许其被镜像。）

控制面对 Endpoints 资源进行映射的例外情况有：

* Endpoints 资源上标签 `endpointslice.kubernetes.io/skip-mirror` 值为 `true`。
* Endpoints 资源包含标签 `control-plane.alpha.kubernetes.io/leader`。
* 对应的 Service 资源不存在。
* 对应的 Service 的选择算符不为空。

每个 Endpoints 资源可能会被转译到多个 EndpointSlices 中去。
当 Endpoints 资源中包含多个子网或者包含多个 IP 协议族（IPv4 和 IPv6）的端点时，
就有可能发生这种状况。
每个子网最多有 1000 个地址会被镜像到 EndpointSlice 中。

## 

* 遵循[使用 Service 连接到应用](/zh-cn/docs/tutorials/services/connect-applications-service/)教程
* 阅读 EndpointSlice API 的 [API 参考](/zh-cn/docs/reference/kubernetes-api/service-resources/endpoint-slice-v1/)
* 阅读 Endpoints API 的 [API 参考](/zh-cn/docs/reference/kubernetes-api/service-resources/endpoints-v1/)
