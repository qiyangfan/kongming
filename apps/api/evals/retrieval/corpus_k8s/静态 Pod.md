# 静态 Pod

**静态 Pod（Static Pod）** 由特定节点上的 kubelet 守护进程直接管理，
而不是由 API 服务器观察和管理。
与由控制平面管理的 Pod（例如由
Deployment 管理的 Pod）不同，
kubelet 会监视每个静态 Pod，并在其失败时自动重启。

静态 Pod 总是绑定到特定节点上的某个 kubelet。

静态 Pod 的主要用途是运行自托管控制平面：
换言之，使用 kubelet 来监管各个
[控制平面组件](/zh-cn/docs/concepts/overview/components/#control-plane-components)。
例如，[kubeadm](/zh-cn/docs/reference/setup-tools/kubeadm/)
就使用静态 Pod 在控制平面节点上运行
`kube-apiserver`、`kube-controller-manager`、`kube-scheduler` 和 `etcd`。

如果你的集群将控制平面组件作为 Pod 运行，那么它们很可能就是静态 Pod。
你可以通过查看 `kube-system` 名字空间中的镜像 Pod（mirror Pod）来识别它们；
这些 Pod 带有 `kubernetes.io/config.mirror` 注解。

## 镜像 Pod   {#mirror-pods}

kubelet 会自动尝试为每个静态 Pod 在 Kubernetes API 服务器上创建一个
镜像 Pod。
这意味着运行在某个节点上的这些 Pod 在 API 服务器中是可见的，
但你不能通过 API 服务器直接控制它们。
这些 Pod 的名称会在静态 Pod 原始名称后附加节点主机名，并以连字符作为前缀。

kubelet 会把静态 Pod 上的 标签
传播到镜像 Pod。你可以像平常一样通过
选择算符来使用这些标签。

如果你尝试使用 `kubectl` 从 API 服务器中删除镜像 Pod，
kubelet **不会**删除对应的静态 Pod。
kubelet 只会重新创建这个镜像 Pod。

## 限制   {#limitations}

静态 Pod 的规约不能引用其他 API 对象，
例如 ServiceAccount、
ConfigMap 或
Secret。

静态 Pod 不支持[临时容器](/zh-cn/docs/concepts/workloads/pods/ephemeral-containers/)。

## 静态 Pod 与 DaemonSet   {#static-pods-vs-daemonsets}

如果你运行的是一个集群化 Kubernetes，并且使用静态 Pod 在每个节点上运行某个 Pod，
那么你很可能应该改用
DaemonSet。

静态 Pod 不受控制平面管理，因此不能使用 Kubernetes 的标准机制来进行发布、回滚或扩缩容。
DaemonSet 提供了这些能力，也是运行节点级工作负载的推荐方式。

静态 Pod 会在 API 服务器可用之前由 kubelet 启动，
因此它们适合用于引导控制平面组件。DaemonSet 则依赖于一个已经运行中的控制平面。

## 

- 了解如何[创建静态 Pod](/zh-cn/docs/tasks/configure-pod-container/static-pod/)。
- 了解 [Kubernetes 组件](/zh-cn/docs/concepts/overview/components/)，以及控制平面如何使用静态 Pod。
- 了解可作为静态 Pod 替代方案的 [DaemonSet](/zh-cn/docs/concepts/workloads/controllers/daemonset/)。
