# 存储类（StorageClass）

本文描述了 Kubernetes 中 StorageClass 的概念。
建议先熟悉[卷](/zh-cn/docs/concepts/storage/volumes/)和[持久卷](/zh-cn/docs/concepts/storage/persistent-volumes)的概念。

StorageClass 为管理员提供了描述存储**类**的方法。
不同的类型可能会映射到不同的服务质量等级或备份策略，或是由集群管理员制定的任意策略。
Kubernetes 本身并不清楚各种类代表的什么。

Kubernetes 存储类的概念类似于一些其他存储系统设计中的"配置文件"。

## StorageClass 对象   {#storageclass-objects}

每个 StorageClass 都包含 `provisioner`、`parameters` 和 `reclaimPolicy` 字段，
这些字段会在 StorageClass 需要动态制备 PersistentVolume 以满足 PersistentVolumeClaim（PVC）时使用到。

StorageClass 对象的命名很重要，用户使用这个命名来请求生成一个特定的类。
当创建 StorageClass 对象时，管理员设置 StorageClass 对象的命名和其他参数。

作为管理员，你可以为没有申请绑定到特定 StorageClass 的 PVC 指定一个默认的存储类：
更多详情请参阅
[PersistentVolumeClaim 概念](/zh-cn/docs/concepts/storage/persistent-volumes/#persistentvolumeclaims)。

## 默认 StorageClass  {#default-storageclass}

你可以将某个 StorageClass 标记为集群的默认存储类。
关于如何设置默认的 StorageClass，
请参见[更改默认 StorageClass](/zh-cn/docs/tasks/administer-cluster/change-default-storage-class/)。

当一个 PVC 没有指定 `storageClassName` 时，会使用默认的 StorageClass。

如果你在集群中的多个 StorageClass 上将
[`storageclass.kubernetes.io/is-default-class`](/zh-cn/docs/reference/labels-annotations-taints/#storageclass-kubernetes-io-is-default-class)
注解设置为 true，然后创建一个未设置 `storageClassName` 的 PersistentVolumeClaim（PVC），
Kubernetes 将使用最近创建的默认 StorageClass。

你应该尝试在集群中只将一个 StorageClass 标记为默认的存储类。
Kubernetes 允许你拥有多个默认 StorageClass 的原因是为了无缝迁移。

你可以在创建新的 PVC 时不指定 `storageClassName`，即使在集群中没有默认
StorageClass 的情况下也可以这样做。在这种情况下，新的 PVC
会按照你定义的方式进行创建，并且该 PVC 的 `storageClassName` 将保持不设置，
直到有可用的默认 StorageClass 为止。

你可以拥有一个没有任何默认 StorageClass 的集群。
如果你没有将任何 StorageClass 标记为默认（例如，云服务提供商还没有为你设置默认值），那么
Kubernetes 将无法为需要 StorageClass 的 PersistentVolumeClaim 应用默认值。

当默认 StorageClass 变得可用时，控制平面会查找所有未设置 `storageClassName` 的现有 PVC。
对于那些 `storageClassName` 值为空或没有此键的 PVC，控制平面将更新它们，
将 `storageClassName` 设置为匹配新的默认 StorageClass。
如果你有一个现成的 PVC，其 `storageClassName` 为 `""`，
而你配置了默认的 StorageClass，那么该 PVC 将不会被更新。

（当默认的 StorageClass 存在时）为了继续绑定到 `storageClassName` 为 `""` 的 PV，
你需要将关联 PVC 的 `storageClassName` 设置为 `""`。

## 存储制备器  {#provisioner}

每个 StorageClass 都有一个制备器（Provisioner），用来决定使用哪个卷插件制备 PV。
该字段必须指定。

| 卷插件               | 内置制备器 |               配置示例                |
| :------------------- | :--------: | :-----------------------------------: |
| AzureFile            |  &#x2713;  |       [Azure File](#azure-file)       |
| CephFS               |     -      |                   -                   |
| FC                   |     -      |                   -                   |
| FlexVolume           |     -      |                   -                   |
| iSCSI                |     -      |                   -                   |
| Local                |     -      |            [Local](#local)            |
| NFS                  |     -      |              [NFS](#nfs)              |
| PortworxVolume       |  &#x2713;  |  [Portworx Volume](#portworx-volume)  |
| RBD                  |  &#x2713;  |         [Ceph RBD](#ceph-rbd)         |
| VsphereVolume        |  &#x2713;  |          [vSphere](#vsphere)          |

你不限于指定此处列出的"内置"制备器（其名称前缀为 "kubernetes.io" 并打包在 Kubernetes 中）。
你还可以运行和指定外部制备器，这些独立的程序遵循由 Kubernetes
定义的[规范](https://github.com/kubernetes/design-proposals-archive/blob/main/storage/volume-provisioning.md)。
外部供应商的作者完全可以自由决定他们的代码保存于何处、打包方式、运行方式、使用的插件（包括 Flex）等。
代码仓库 [kubernetes-sigs/sig-storage-lib-external-provisioner](https://github.com/kubernetes-sigs/sig-storage-lib-external-provisioner)
包含一个用于为外部制备器编写功能实现的类库。你可以访问代码仓库
[kubernetes-sigs/sig-storage-lib-external-provisioner](https://github.com/kubernetes-sigs/sig-storage-lib-external-provisioner)
了解外部驱动列表。

例如，NFS 没有内部制备器，但可以使用外部制备器。
也有第三方存储供应商提供自己的外部制备器。

## 回收策略 {#reclaim-policy}

由 StorageClass 动态创建的 PersistentVolume 会在类的
[`reclaimPolicy`](/zh-cn/docs/concepts/storage/persistent-volumes/#reclaiming)
字段中指定回收策略，可以是 `Delete` 或者 `Retain`。
如果 StorageClass 对象被创建时没有指定 `reclaimPolicy`，它将默认为 `Delete`。

通过 StorageClass 手动创建并管理的 PersistentVolume 会使用它们被创建时指定的回收策略。

## 卷扩展   {#allow-volume-expansion}

PersistentVolume 可以配置为可扩展。
这允许你通过编辑相应的 PVC 对象来调整卷大小，申请一个新的、更大的存储容量。

当下层 StorageClass 的 `allowVolumeExpansion` 字段设置为 `true` 时，以下类型的卷支持卷扩展。

| 卷类型               | 卷扩展的 Kubernetes 版本要求  |
| :------------------- | :------------------------ |
| Azure File           | 1.11                      |
| CSI                  | 1.24                      |
| FlexVolume           | 1.13                      |
| Portworx             | 1.11                      |
| rbd                  | 1.11                      |

此功能仅可用于扩容卷，不能用于缩小卷。

## 挂载选项   {#mount-options}

由 StorageClass 动态创建的 PersistentVolume 将使用类中 `mountOptions` 字段指定的挂载选项。

如果卷插件不支持挂载选项，却指定了挂载选项，则制备操作会失败。
挂载选项在 StorageClass 和 PV 上都**不**会做验证。如果其中一个挂载选项无效，
那么这个 PV 挂载操作就会失败。

## 卷绑定模式 {#volume-binding-mode}

`volumeBindingMode`
字段控制了[卷绑定和动态制备](/zh-cn/docs/concepts/storage/persistent-volumes/#provisioning)应该发生在什么时候。
当未设置时，默认使用 `Immediate` 模式。

`Immediate` 模式表示一旦创建了 PersistentVolumeClaim 也就完成了卷绑定和动态制备。
对于由于拓扑限制而非集群所有节点可达的存储后端，PersistentVolume会在不知道
Pod 调度要求的情况下绑定或者制备。

集群管理员可以通过指定 `WaitForFirstConsumer` 模式来解决此问题。
该模式将延迟 PersistentVolume 的绑定和制备，直到使用该 PersistentVolumeClaim
的 Pod 被创建。PersistentVolume 会根据 Pod 调度约束指定的拓扑来选择或制备。
这些包括但不限于[资源需求](/zh-cn/docs/concepts/configuration/manage-resources-containers/)、
[节点筛选器](/zh-cn/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector)、
[Pod 亲和性和互斥性](/zh-cn/docs/concepts/scheduling-eviction/assign-pod-node/#affinity-and-anti-affinity/)、
以及[污点和容忍度](/zh-cn/docs/concepts/scheduling-eviction/taint-and-toleration)。

以下插件支持使用动态制备的 `WaitForFirstConsumer`：

- CSI 卷，前提是特定的 CSI 驱动程序支持此卷

以下插件支持预创建绑定 PersistentVolume 的 `WaitForFirstConsumer` 模式：

- CSI 卷，前提是特定的 CSI 驱动程序支持此卷
- [`local`](#local)

如果你选择使用 `WaitForFirstConsumer`，请不要在 Pod 规约中使用 `nodeName` 来指定节点亲和性。
如果在这种情况下使用 `nodeName`，Pod 将会绕过调度程序，PVC 将停留在 `pending` 状态。

相反，你可以为 `kubernetes.io/hostname` 使用节点选择器：

## 允许的拓扑结构  {#allowed-topologies}

当集群操作人员使用了 `WaitForFirstConsumer` 的卷绑定模式，
在大部分情况下就没有必要将制备限制为特定的拓扑结构。
然而，如果还有需要的话，可以使用 `allowedTopologies`。

这个例子描述了如何将制备卷的拓扑限制在特定的区域，
在使用时应该根据插件支持情况替换 `zone` 和 `zones` 参数。

`tagSpecification`：具有此前缀的标签适用于动态配置的 EBS 卷。

## 参数   {#parameters}

StorageClass 的参数描述了存储类的卷。取决于制备器，可以接受不同的参数。
当参数被省略时，会使用默认值。

一个 StorageClass 最多可以定义 512 个参数。这些参数对象的总长度不能超过
256 KiB，包括参数的键和值。

### AWS EBS

Kubernetes  不包含 `awsElasticBlockStore` 卷类型。

AWSElasticBlockStore 树内存储驱动程序在 Kubernetes v1.19 版本中被弃用，
并在 v1.27 版本中被完全移除。

Kubernetes 项目建议你转为使用 [AWS EBS](https://github.com/kubernetes-sigs/aws-ebs-csi-driver)
树外存储驱动程序。

以下是一个针对 AWS EBS CSI 驱动程序的 StorageClass 示例：

### AWS EFS

要配置 AWS EFS 存储，你可以使用树外
[AWS_EFS_CSI_DRIVER](https://github.com/kubernetes-sigs/aws-efs-csi-driver)。

- `provisioningMode`：由 Amazon EFS 制备的卷类型。目前，仅支持基于访问点的制备（`efs-ap`）。
- `fileSystemId`：在此文件系统下创建访问点。
- `directoryPerms`：由访问点所创建的根目录的目录权限。

有关细节参阅
[AWS_EFS_CSI_Driver 动态制备](https://github.com/kubernetes-sigs/aws-efs-csi-driver/blob/master/examples/kubernetes/dynamic_provisioning/README.md)文档。

### NFS

要配置 NFS 存储，
你可以使用树内驱动程序或[针对 Kubernetes 的 NFS CSI 驱动程序](https://github.com/kubernetes-csi/csi-driver-nfs#readme)（推荐）。

- `server`：NFS 服务器的主机名或 IP 地址。
- `path`：NFS 服务器导出的路径。
- `readOnly`：是否将存储挂载为只读的标志（默认为 `false`）。

Kubernetes 不包含内部 NFS 驱动。你需要使用外部驱动为 NFS 创建 StorageClass。
这里有些例子：

- [NFS Ganesha 服务器和外部驱动](https://github.com/kubernetes-sigs/nfs-ganesha-server-and-external-provisioner)
- [NFS subdir 外部驱动](https://github.com/kubernetes-sigs/nfs-subdir-external-provisioner)

### vSphere

vSphere 存储类有两种制备器：

- [CSI 制备器](#vsphere-provisioner-csi)：`csi.vsphere.vmware.com`
- [vCP 制备器](#vcp-provisioner)：`kubernetes.io/vsphere-volume`

树内制备器已经被
[弃用](/blog/2019/12/09/kubernetes-1-17-feature-csi-migration-beta/#why-are-we-migrating-in-tree-plugins-to-csi)。
更多关于 CSI 制备器的详情，请参阅
[Kubernetes vSphere CSI 驱动](https://vsphere-csi-driver.sigs.k8s.io/)和
[vSphereVolume CSI 迁移](/zh-cn/docs/concepts/storage/volumes/#vsphere-csi-migration)。

#### CSI 制备器 {#vsphere-provisioner-csi}

vSphere CSI StorageClass 制备器在 Tanzu Kubernetes 集群下运行。示例请参阅
[vSphere CSI 仓库](https://github.com/kubernetes-sigs/vsphere-csi-driver/blob/master/example/vanilla-k8s-RWM-filesystem-volumes/example-sc.yaml)。

#### vCP 制备器   {#vcp-provisioner}

以下示例使用 VMware Cloud Provider（vCP）StorageClass 制备器。

1. 使用用户指定的磁盘格式创建一个 StorageClass。

   ```yaml
   apiVersion: storage.k8s.io/v1
   kind: StorageClass
   metadata:
     name: fast
   provisioner: kubernetes.io/vsphere-volume
   parameters:
     diskformat: zeroedthick
   ```

   

   `diskformat`：`thin`、`zeroedthick` 和 `eagerzeroedthick`。
   默认值：`"thin"`。

2. 在用户指定的数据存储上创建磁盘格式的 StorageClass。

   ```yaml
   apiVersion: storage.k8s.io/v1
   kind: StorageClass
   metadata:
     name: fast
   provisioner: kubernetes.io/vsphere-volume
   parameters:
     diskformat: zeroedthick
     datastore: VSANDatastore
   ```

   

   `datastore`：用户也可以在 StorageClass 中指定数据存储。
   卷将在 StorageClass 中指定的数据存储上创建，在这种情况下是 `VSANDatastore`。
   该字段是可选的。
   如果未指定数据存储，则将在用于初始化 vSphere Cloud Provider 的 vSphere
   配置文件中指定的数据存储上创建该卷。

3. Kubernetes 中的存储策略管理

   

   - 使用现有的 vCenter SPBM 策略

     vSphere 用于存储管理的最重要特性之一是基于策略的管理。
     基于存储策略的管理（SPBM）是一个存储策略框架，提供单一的统一控制平面的跨越广泛的数据服务和存储解决方案。
     SPBM 使得 vSphere 管理员能够克服先期的存储配置挑战，如容量规划、差异化服务等级和管理容量空间。

     SPBM 策略可以在 StorageClass 中使用 `storagePolicyName` 参数声明。

    

   - Kubernetes 内的 Virtual SAN 策略支持

     Vsphere Infrastructure（VI）管理员将能够在动态卷配置期间指定自定义 Virtual SAN
     存储功能。你现在可以在动态制备卷期间以存储能力的形式定义存储需求，例如性能和可用性。
     存储能力需求会转换为 Virtual SAN 策略，之后当持久卷（虚拟磁盘）被创建时，
     会将其推送到 Virtual SAN 层。虚拟磁盘分布在 Virtual SAN 数据存储中以满足要求。

     

     你可以参考[基于存储策略的动态制备卷管理](https://github.com/vmware-archive/vsphere-storage-for-kubernetes/blob/fa4c8b8ad46a85b6555d715dd9d27ff69839df53/documentation/policy-based-mgmt.md)，
     进一步了解有关持久卷管理的存储策略的详细信息。

### Ceph RBD（已弃用）  {#ceph-rbd}

Ceph RBD 的内部驱动程序已被弃用。请使用
[CephFS RBD CSI 驱动程序](https://github.com/ceph/ceph-csi)。

- `monitors`：Ceph monitor，逗号分隔。该参数是必需的。
- `adminId`：Ceph 客户端 ID，用于在池 Ceph 池中创建映像。默认是 "admin"。
- `adminSecret`：`adminId` 的 Secret 名称。该参数是必需的。
  提供的 Secret 必须有值为 "kubernetes.io/rbd" 的 type 参数。
- `adminSecretNamespace`：`adminSecret` 的命名空间。默认是 "default"。
- `pool`：Ceph RBD 池。默认是 "rbd"。
- `userId`：Ceph 客户端 ID，用于映射 RBD 镜像。默认与 `adminId` 相同。

- `userSecretName`：用于映射 RBD 镜像的 `userId` 的 Ceph Secret 的名字。
  它必须与 PVC 存在于相同的 namespace 中。该参数是必需的。
  提供的 Secret 必须具有值为 "kubernetes.io/rbd" 的 type 参数，例如以这样的方式创建：

  ```shell
  kubectl create secret generic ceph-secret --type="kubernetes.io/rbd" \
    --from-literal=key='QVFEQ1pMdFhPUnQrSmhBQUFYaERWNHJsZ3BsMmNjcDR6RFZST0E9PQ==' \
    --namespace=kube-system
  ```

- `userSecretNamespace`：`userSecretName` 的命名空间。
- `fsType`：Kubernetes 支持的 fsType。默认：`"ext4"`。
- `imageFormat`：Ceph RBD 镜像格式，"1" 或者 "2"。默认值是 "1"。
- `imageFeatures`：这个参数是可选的，只能在你将 `imageFormat` 设置为 "2" 才使用。
  目前支持的功能只是 `layering`。默认是 ""，没有功能打开。

### Azure 磁盘   {#azure-disk}

Kubernetes  不包含 `azureDisk` 卷类型。

`azureDisk` 树内存储驱动程序在 Kubernetes v1.19 版本中被弃用，并在
v1.27 版本中被完全移除。

Kubernetes 项目建议你转为使用
[Azure Disk](https://github.com/kubernetes-sigs/azuredisk-csi-driver)
第三方存储驱动程序。

### Azure 文件（已弃用）  {#azure-file}

- `skuName`：Azure 存储帐户 SKU 层。默认为空。
- `location`：Azure 存储帐户位置。默认为空。
- `storageAccount`：Azure 存储帐户名称。默认为空。
  如果不提供存储帐户，会搜索所有与资源相关的存储帐户，以找到一个匹配
  `skuName` 和 `location` 的账号。
  如果提供存储帐户，它必须存在于与集群相同的资源组中，`skuName` 和 `location` 会被忽略。
- `secretNamespace`：包含 Azure 存储帐户名称和密钥的密钥的名字空间。
  默认值与 Pod 相同。
- `secretName`：包含 Azure 存储帐户名称和密钥的密钥的名称。
  默认值为 `azure-storage-account-<accountName>-secret`
- `readOnly`：指示是否将存储安装为只读的标志。默认为 `false`，表示"读/写"挂载。
  该设置也会影响 `VolumeMounts` 中的 `ReadOnly` 设置。

在存储制备期间，为挂载凭证创建一个名为 `secretName` 的 Secret。如果集群同时启用了
[RBAC](/zh-cn/docs/reference/access-authn-authz/rbac/)
和[控制器角色](/zh-cn/docs/reference/access-authn-authz/rbac/#controller-roles)，
为 `system:controller:persistent-volume-binder` 的 clusterrole 添加
`secret` 资源的 `create` 权限。

在多租户上下文中，强烈建议显式设置 `secretNamespace` 的值，
否则其他用户可能会读取存储帐户凭据。

### Portworx 卷（已弃用）    {#portworx-volume}

- `fs`：选择的文件系统：`none/xfs/ext4`（默认：`ext4`）。
- `block_size`：以 Kbytes 为单位的块大小（默认值：`32`）。
- `repl`：同步副本数量，以复制因子 `1..3`（默认值：`1`）的形式提供。
  这里需要填写字符串，即，`"1"` 而不是 `1`。
- `io_priority`：决定是否从更高性能或者较低优先级存储创建卷
  `high/medium/low`（默认值：`low`）。
- `snap_interval`：触发快照的时钟/时间间隔（分钟）。
  快照是基于与先前快照的增量变化，0 是禁用快照（默认：`0`）。
  这里需要填写字符串，即，是 `"70"` 而不是 `70`。
- `aggregation_level`：指定卷分配到的块数量，0 表示一个非聚合卷（默认：`0`）。
  这里需要填写字符串，即，是 `"0"` 而不是 `0`。
- `ephemeral`：指定卷在卸载后进行清理还是持久化。
  `emptyDir` 的使用场景可以将这个值设置为 `true`，
  `persistent volumes` 的使用场景可以将这个值设置为 `false`
  （例如 Cassandra 这样的数据库）
  `true/false`（默认为 `false`）。这里需要填写字符串，即，
  是 `"true"` 而不是 `true`。

### 本地   {#local}

在 Kubernetes  中，本地卷还不支持动态制备；
然而还是需要创建 StorageClass 以延迟卷绑定，直到 Pod 被实际调度到合适的节点。
这是由 `WaitForFirstConsumer` 卷绑定模式指定的。

延迟卷绑定使得调度器在为 PersistentVolumeClaim 选择一个合适的
PersistentVolume 时能考虑到所有 Pod 的调度限制。
