# 卷填充器（Populator）与数据源

本文描述了 Kubernetes 中的**卷填充器**和**数据源**。
建议先了解[持久卷（Persistent Volume）](/zh-cn/docs/concepts/storage/persistent-volumes/)的相关内容。

当你创建一个 PersistentVolumeClaim 时，
Kubernetes 为它配置的卷通常是空的。利用**数据源**，你可以请求在新卷中预先填充现有数据。
**卷填充器**是根据 PersistentVolumeClaim 引用的数据源执行填充操作的控制器。

Kubernetes 内置支持数据源，可用于
[克隆现有卷](/zh-cn/docs/concepts/storage/volume-pvc-datasource/)或
[恢复卷快照](/zh-cn/docs/concepts/storage/volume-snapshots/)。自定义卷填充器扩展了这一机制。
数据源是一种自定义资源，即类型由
CustomResourceDefinition 定义的对象。
填充器控制器会监视引用此类资源的 PersistentVolumeClaim，并从中填充新卷。

## 卷填充器和数据源

Kubernetes 支持自定义卷填充器。要使用自定义卷填充器，你必须为 kube-apiserver 和
kube-controller-manager 启用 `AnyVolumeDataSource`
[特性门控](/zh-cn/docs/reference/command-line-tools-reference/feature-gates/)。

卷填充器利用 PVC 规约中的 `dataSourceRef` 字段。与 `dataSource` 字段（只能包含对另一个
PersistentVolumeClaim 或 VolumeSnapshot 的引用）不同，`dataSourceRef` 字段可以包含对同一命名空间中任何对象的引用，
除了 PVC 之外的核心对象。对于启用了特性门控的集群，建议使用 `dataSourceRef` 而不是 `dataSource`。

## 数据源引用

`dataSourceRef` 字段的行为几乎与 `dataSource` 字段相同。如果指定了其中一个而未指定另一个，
API 服务器会为两个字段赋予相同的值。两个字段在创建后都不能更改，尝试为两个字段指定不同的值将导致验证错误。
因此，这两个字段的内容始终相同。

用户应该注意 `dataSourceRef` 字段和 `dataSource` 字段之间的两个区别：

* `dataSource` 字段会忽略无效值（就像字段为空一样），而 `dataSourceRef` 字段从不忽略值，
  如果使用无效值将导致错误。无效值是除 PVC 之外的任何核心对象（没有 `apiGroup` 的对象）。
* `dataSourceRef` 字段可以包含不同类型的对象，而 `dataSource` 字段只允许 PVC 和 VolumeSnapshot。

当启用 `CrossNamespaceVolumeDataSource` 特性时，还有额外的区别：

* `dataSource` 字段只允许本地对象，而 `dataSourceRef` 字段允许任何命名空间中的对象。
* 指定命名空间时，`dataSource` 和 `dataSourceRef` 不会同步。

用户应该在启用了特性门控的集群上始终使用 `dataSourceRef`，在未启用的集群上回退到 `dataSource`。
在任何情况下都无需查看两个字段。语义略有不同的重复值仅为向后兼容而存在。
特别是，旧版和新版控制器可以互操作，因为字段相同。

### 使用卷填充器

卷填充器是可以创建非空卷的控制器，
卷的内容由自定义资源决定。用户通过使用 `dataSourceRef` 字段引用自定义资源来创建填充卷：

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: populated-pvc
spec:
  dataSourceRef:
    name: example-name
    kind: ExampleDataSource
    apiGroup: example.storage.k8s.io
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 10Gi
```

由于卷填充器是外部组件，如果未安装所有正确的组件，尝试创建使用卷填充器的 PVC 可能会失败。
外部控制器应该在 PVC 上生成事件，以提供创建状态的反馈，包括由于缺少某些组件而无法创建 PVC 时的警告。

你可以将 Alpha 版本的[卷数据源验证器](https://github.com/kubernetes-csi/volume-data-source-validator)控制器安装到你的集群中。
当没有注册填充器来处理某种类型的数据源时，该控制器会在 PVC 上生成警告事件。
当为 PVC 安装了合适的填充器时，填充器控制器负责报告与卷创建和过程中出现的问题相关的事件。

## 跨命名空间数据源

Kubernetes 支持跨命名空间卷数据源。要使用跨命名空间卷数据源，你必须为 kube-apiserver 和
kube-controller-manager 启用 `AnyVolumeDataSource` 和 `CrossNamespaceVolumeDataSource`
[特性门控](/zh-cn/docs/reference/command-line-tools-reference/feature-gates/)。
此外，你还必须为 csi-provisioner 启用 `CrossNamespaceVolumeDataSource` 特性门控。

启用 `CrossNamespaceVolumeDataSource` 特性门控允许你在 `dataSourceRef` 字段中指定命名空间。

当你为卷数据源指定命名空间时，Kubernetes 在接受引用之前会检查另一个命名空间中的
ReferenceGrant。ReferenceGrant 是 `gateway.networking.k8s.io` 扩展 API 的一部分。
有关详情，请参阅 Gateway API 文档中的 [ReferenceGrant](https://gateway-api.sigs.k8s.io/api-types/referencegrant/)。
这意味着，在使用该机制之前，你必须使用 Gateway API 中的
ReferenceGrant（至少包含此资源）来扩展你的 Kubernetes 集群。

### 使用跨命名空间卷数据源

创建 ReferenceGrant 以允许命名空间所有者接受引用。
你可以通过使用 `dataSourceRef` 字段指定跨命名空间卷数据源来定义填充卷。
你必须在源命名空间中已有有效的 ReferenceGrant：

   ```yaml
   apiVersion: gateway.networking.k8s.io/v1beta1
   kind: ReferenceGrant
   metadata:
     name: allow-ns1-pvc
     namespace: default
   spec:
     from:
     - group: ""
       kind: PersistentVolumeClaim
       namespace: ns1
     to:
     - group: snapshot.storage.k8s.io
       kind: VolumeSnapshot
       name: new-snapshot-demo
   ```

   ```yaml
   apiVersion: v1
   kind: PersistentVolumeClaim
   metadata:
     name: foo-pvc
     namespace: ns1
   spec:
     storageClassName: example
     accessModes:
     - ReadWriteOnce
     resources:
       requests:
         storage: 1Gi
     dataSourceRef:
       apiGroup: snapshot.storage.k8s.io
       kind: VolumeSnapshot
       name: new-snapshot-demo
       namespace: default
     volumeMode: Filesystem
   ```

## 

* 了解[持久卷](/zh-cn/docs/concepts/storage/persistent-volumes/)。
* 了解 [CSI 卷克隆](/zh-cn/docs/concepts/storage/volume-pvc-datasource/)。
* 了解[卷快照](/zh-cn/docs/concepts/storage/volume-snapshots/)。
* 阅读本文提到的[特性门控](/zh-cn/docs/reference/command-line-tools-reference/feature-gates/)。
