# 高级 Pod 配置

本页涵盖高级 Pod 配置主题，包括 [PriorityClass](#priorityclasses)、[RuntimeClass](#runtimeclasses)、
Pod 内的[安全上下文](#security-context)，并介绍[调度](/zh-cn/docs/concepts/scheduling-eviction/#scheduling)相关内容。

## PriorityClass   {#priorityclasses}

**PriorityClass** 允许你设置 Pod 相对于其他 Pod 的重要性。
如果你为 Pod 分配了优先级类，Kubernetes 会根据你所指定的 PriorityClass 为该 Pod 设置 `.spec.priority` 字段
（你不能直接设置 `.spec.priority`）。如果 Pod 无法被调度，且原因是资源不足，

会尝试抢占较低优先级的 Pod，
以使较高优先级的 Pod 能够被调度。

PriorityClass 是一个集群级别的 API 对象，它将优先级类名称映射到一个整数优先级值。数值越大，优先级越高。

### 定义 PriorityClass   {#defining-a-priorityclass}

```yaml
apiVersion: scheduling.k8s.io/v1
kind: PriorityClass
metadata:
  name: high-priority
value: 10000
globalDefault: false
description: "Priority class for high-priority workloads"
```

### 使用 PriorityClass 指定 Pod 优先级   {#specify-pod-priority-using-a-priorityclass}

apiVersion: v1
kind: Pod
metadata:
  name: nginx
spec:
  containers:
  - name: nginx
    image: nginx
  priorityClassName: high-priority

### 内置 PriorityClass   {#built-in-priorityclasses}

Kubernetes 提供两个内置的 PriorityClass：

- `system-cluster-critical`：用于对集群至关重要的系统组件。
- `system-node-critical`：用于对单个节点至关重要的系统组件。这是 Kubernetes 中 Pod 可以具有的最高优先级。

更多信息请参阅 [Pod 优先级与抢占](/zh-cn/docs/concepts/scheduling-eviction/pod-priority-preemption/)。

## RuntimeClass   {#runtimeclasses}

**RuntimeClass** 允许你为 Pod 指定底层的容器运行时。当你需要为不同类型的 Pod 指定不同的容器运行时，
例如需要不同的隔离级别或运行时特性时，这一机制非常有用。

### 示例 Pod   {#runtimeclass-pod-example}

apiVersion: v1
kind: Pod
metadata:
  name: mypod
spec:
  runtimeClassName: myclass
  containers:
  - name: mycontainer
    image: nginx

[RuntimeClass](/zh-cn/docs/concepts/containers/runtime-class/) 是一个集群级别的对象，
表示在某些或所有节点上可用的容器运行时。

集群管理员负责安装和配置支撑 RuntimeClass 的具体运行时。

他们可以在所有节点上设置这种特殊的容器运行时配置，也可以只在部分节点上设置。

更多信息请参阅 [RuntimeClass](/zh-cn/docs/concepts/containers/runtime-class/) 文档。

## Pod 和容器级别的安全上下文配置   {#security-context}

Pod 规约中的 `Security context` 字段提供了对 Pod 和容器安全设置的精细化控制。

### Pod 范围的 `securityContext`   {#pod-level-security-context}

安全设置的某些方面适用于整个 Pod；对于其他方面，
你可能希望设置一个默认值，而不允许容器级别的覆盖。

以下是在 Pod 层面使用 `securityContext` 的示例：

#### 示例 Pod   {#pod-level-security-context-example}

apiVersion: v1
kind: Pod
metadata:
  name: security-context-demo
spec:
  securityContext:  # 此项应用到整个 Pod
    runAsUser: 1000
    runAsGroup: 3000
    fsGroup: 2000
  containers:
  - name: sec-ctx-demo
    image: registry.k8s.io/e2e-test-images/agnhost:2.45
    command: ["sh", "-c", "sleep 1h"]

### 容器级别的安全上下文   {#container-level-security-context}

你可以仅为特定容器指定安全上下文。以下是一个示例：

#### 示例 Pod   {#container-level-security-context-example}

apiVersion: v1
kind: Pod
metadata:
  name: security-context-demo-2
spec:
  containers:
  - name: sec-ctx-demo-2
    image: gcr.io/google-samples/node-hello:1.0
    securityContext:
      allowPrivilegeEscalation: false
      runAsNonRoot: true
      runAsUser: 1000
      capabilities:
        drop:
        - ALL
      seccompProfile:
        type: RuntimeDefault

### 安全上下文选项   {#security-context-options}

- **用户和组 ID**：控制容器以哪个用户/组身份运行。
- **Capabilities**：添加或删除 Linux 权能。
- **Seccomp Profile**：设置安全计算配置文件。
- **SELinux 选项**：配置 SELinux 上下文。
- **AppArmor**：配置 AppArmor 配置文件以实现额外的访问控制。
- **Windows 选项**：配置 Windows 特定的安全设置。

你也可以使用 Pod 的 `securityContext` 来允许 Linux
容器进入[**特权模式**](/zh-cn/docs/concepts/security/linux-kernel-security-constraints/#privileged-containers)。
特权模式会覆盖 `securityContext` 中的许多其他安全设置。
除非无法通过 `securityContext` 中的其他字段授予等效权限，否则应避免使用此设置。
你可以通过在 Pod 级别的安全上下文中设置 `windowsOptions.hostProcess` 标志，
以类似的特权模式运行 Windows 容器。有关详细信息和操作说明，
参阅[创建 Windows HostProcess Pod](/zh-cn/docs/tasks/configure-pod-container/create-hostprocess-pod/)。

更多信息请参阅[为 Pod 或容器配置安全上下文](/zh-cn/docs/tasks/configure-pod-container/security-context/)。

## 影响 Pod 调度决策   {#scheduling}

Kubernetes 提供了多种机制来控制 Pod 被调度到哪些节点上。

### 节点选择算符   {#node-selectors}

最简单的节点选择约束形式：

apiVersion: v1
kind: Pod
metadata:
  name: nginx
spec:
  containers:
  - name: nginx
    image: nginx
  nodeSelector:
    disktype: ssd

### 节点亲和性   {#node-affinity}

节点亲和性允许你指定规则，限制 Pod 可以被调度到哪些节点上。以下示例中的 Pod 倾向于运行在带有特定大洲标签的节点上，
选择基于 [`topology.kubernetes.io/zone`](/zh-cn/docs/reference/labels-annotations-taints/#topologykubernetesiozone) 标签的值。

apiVersion: v1
kind: Pod
metadata:
  name: with-node-affinity
spec:
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
        - matchExpressions:
          - key: topology.kubernetes.io/zone
            operator: In
            values:
            - antarctica-east1
            - antarctica-west1
  containers:
  - name: with-node-affinity
    image: registry.k8s.io/pause:3.8

### Pod 亲和性与反亲和性   {#pod-affinity-and-anti-affinity}

除了节点亲和性之外，你还可以基于节点上**已运行的其他 Pod** 的标签来限制 Pod 可以被调度到哪些节点。
Pod 亲和性允许你指定规则，说明 Pod 应相对于其他 Pod 放置在何处。

apiVersion: v1
kind: Pod
metadata:
  name: with-pod-affinity
spec:
  affinity:
    podAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
      - labelSelector:
          matchExpressions:
          - key: app
            operator: In
            values:
            - database
        topologyKey: topology.kubernetes.io/zone
  containers:
  - name: with-pod-affinity
    image: registry.k8s.io/pause:3.8

### 容忍度   {#tolerations}

**容忍度（Toleration）** 允许 Pod 被调度到带有匹配污点的节点上：

apiVersion: v1
kind: Pod
metadata:
  name: mypod
spec:
  containers:
  - name: myapp
    image: nginx
  tolerations:
  - key: "key"
    operator: "Equal"
    value: "value"
    effect: "NoSchedule"

更多信息请参阅[将 Pod 指派到节点](/zh-cn/docs/concepts/scheduling-eviction/assign-pod-node/)。

## Pod 开销   {#pod-overhead}

Pod 开销允许你在容器请求和限制之外，考虑 Pod 基础设施所消耗的资源。

---
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: kvisor-runtime
handler: kvisor-runtime
overhead:
  podFixed:
    memory: "2Gi"
    cpu: "500m"
---
apiVersion: v1
kind: Pod
metadata:
  name: mypod
spec:
  runtimeClassName: kvisor-runtime
  containers:
  - name: myapp
    image: nginx
    resources:
      requests:
        memory: "64Mi"
        cpu: "250m"
      limits:
        memory: "128Mi"
        cpu: "500m"

## 

* 阅读 [Pod 优先级与抢占](/zh-cn/docs/concepts/scheduling-eviction/pod-priority-preemption/)。
* 阅读 [RuntimeClass](/zh-cn/docs/concepts/containers/runtime-class/)。
* 探索[为 Pod 或容器配置安全上下文](/zh-cn/docs/tasks/configure-pod-container/security-context/)。
* 了解 Kubernetes 如何[将 Pod 指派到节点](/zh-cn/docs/concepts/scheduling-eviction/assign-pod-node/)。
* [Pod 开销](/zh-cn/docs/concepts/scheduling-eviction/pod-overhead/)。
