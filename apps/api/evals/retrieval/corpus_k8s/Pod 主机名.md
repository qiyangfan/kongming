# Pod 主机名

本文讲述如何设置 Pod 的主机名、配置主机名后的潜在副作用以及底层机制。

## 默认 Pod 主机名 {#default-pod-hostname}

当 Pod 被创建时，其主机名（从 Pod 内部观察）来源于 Pod 的 `metadata.name` 值。
主机名和其对应的完全限定域名（FQDN）都会被设置为 `metadata.name` 值（从 Pod 的角度）。

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: busybox-1
spec:
  containers:
  - image: busybox:1.28
    command:
      - sleep
      - "3600"
    name: busybox
```

由上述清单创建的 Pod 将其主机名和完全限定域名（FQDN）设置为 `busybox-1`。

## 使用 Pod 的 hostname 和 subdomain 字段设置主机名 {#hostname-with-pod-s-hostname-and-subdomain-fields}

Pod 规约包含一个可选的 `hostname` 字段。
当此字段被设置时，其取值优先于 Pod 的 `metadata.name`，作为 Pod 的主机名（从 Pod 内部观察）。
例如，如果将 Pod 的 `spec.hostname` 设置为 `my-host`，则 Pod 的主机名会被设置为 `my-host`。

Pod 规约还包含一个可选的 `subdomain` 字段，表示 Pod 属于其命名空间中的某个子域。
如果 Pod 的 `spec.hostname` 设置为 `foo`，`spec.subdomain` 设置为 `my-namespace` 命名空间中的 `bar`，
则其主机名为 `foo`，完全限定域名（FQDN）为 `foo.bar.my-namespace.svc.cluster-domain.example`（从 Pod 内部观察）。

当 `hostname` 和 `subdomain` 都被设置时，集群的 DNS 服务器会基于这些字段创建 A 和/或 AAAA 记录。参考
[Pod 的 hostname 和 subdomain 字段](/zh-cn/docs/concepts/services-networking/dns-pod-service/#pod-hostname-and-subdomain-field)。

## 使用 Pod 的 setHostnameAsFQDN 字段设置主机名 {#hostname-with-pod-s-sethostnameasfqdn-fields}

当 Pod 被配置为使用完全限定域名（FQDN）时，则其主机名是短的主机名。
例如，如果 Pod 的完全限定域名是 `busybox-1.busybox-subdomain.my-namespace.svc.cluster-domain.example`，
那么该 Pod 内的 `hostname` 命令默认返回 `busybox-1`，而 `hostname --fqdn` 命令返回 FQDN。

当在 Pod 规约中同时设置了 `setHostnameAsFQDN: true` 和 `subdomain` 字段时，
kubelet 会将 Pod 的 FQDN 写入该 Pod 命名空间的主机名中。
在这种情况下，`hostname` 和 `hostname --fqdn` 都会返回 Pod 的 FQDN。

Pod 的 FQDN 构建方式与前面定义的方式相同。
它由 Pod 的 `spec.hostname`（如果指定）或 `metadata.name` 字段、
`spec.subdomain`、`namespace` 名称以及集群域名后缀组成。

在 Linux 中，内核的 hostname 字段（`struct utsname` 的 `nodename` 字段）限制为 64 个字符。

如果 Pod 启用了此特性，而其 FQDN 长于 64 个字符，则此 Pod 将无法启动。
Pod 将保持在 `Pending` 状态（在 `kubectl` 中显示为 `ContainerCreating`），并生成错误事件，
例如 “Failed to construct FQDN from Pod hostname and cluster domain”。

这意味着在使用此字段时，你必须确保 Pod 的 `metadata.name`（或 `spec.hostname`）
与 `spec.subdomain` 字段组合后的 FQDN 不超过 64 个字符。

## 使用 Pod 的 hostnameOverride 设置主机名 {#hostname-with-pod-s-hostnameoverride}

在 Pod 规约中为 `hostnameOverride` 设置一个值，会导致 kubelet 无条件地将 Pod 的主机名和完全限定域名（FQDN）
都设置为 `hostnameOverride` 值。

`hostnameOverride` 字段的长度限制为 64 个字符，并且必须符合
[RFC 1123](https://datatracker.ietf.org/doc/html/rfc1123) 所定义的 DNS 子域名标准。

示例：

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: busybox-2-busybox-example-domain
spec:
  hostnameOverride: busybox-2.busybox.example.domain
  containers:
  - image: busybox:1.28
    command:
      - sleep
      - "3600"
    name: busybox
```

这仅影响 Pod 内部的主机名；不会影响集群 DNS 服务器中 Pod 的 A 或 AAAA 记录。

如果同时设置了 `hostnameOverride` 和 `hostname`、`subdomain` 字段：

* Pod 内部的主机名会被覆盖为 `hostnameOverride` 值。
* 集群 DNS 服务器中 Pod 的 A 和/或 AAAA 记录仍然基于 `hostname` 和 `subdomain` 字段生成。

注意：如果设置了 `hostnameOverride`，则你不能同时设置 `hostNetwork` 和 `setHostnameAsFQDN` 字段。
API 服务器将显式拒绝任何尝试这种组合的创建请求。

关于在 `hostnameOverride` 与其他字段（hostname、subdomain、setHostnameAsFQDN、hostNetwork）
组合使用时的行为详情，请参阅
[KEP-4762 设计细节](https://github.com/kubernetes/enhancements/blob/master/keps/sig-network/4762-allow-arbitrary-fqdn-as-pod-hostname/README.md#design-details)中的表格。
