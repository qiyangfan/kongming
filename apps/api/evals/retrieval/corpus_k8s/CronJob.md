# CronJob

**CronJob** 创建基于时隔重复调度的 。

CronJob 用于执行排期操作，例如备份、生成报告等。
一个 CronJob 对象就像 Unix 系统上的 **crontab**（cron table）文件中的一行。
它用 [Cron](https://zh.wikipedia.org/wiki/Cron) 格式进行编写，
并周期性地在给定的调度时间执行 Job。

CronJob 有所限制，也比较特殊。
例如在某些情况下，单个 CronJob 可以创建多个并发任务。
请参阅下面的[限制](#cron-job-limitations)。

当控制平面为 CronJob 创建新的 Job 和（间接）Pod 时，CronJob
的 `.metadata.name` 是命名这些 Pod 的部分基础。
CronJob 的名称必须是一个合法的
[DNS 子域](/zh-cn/docs/concepts/overview/working-with-objects/names/#dns-subdomain-names)值，
但这会对 Pod 的主机名产生意外的结果。为获得最佳兼容性，名称应遵循更严格的
[DNS 标签](/zh-cn/docs/concepts/overview/working-with-objects/names#dns-label-names)规则。
即使名称是一个 DNS 子域，它也不能超过 52 个字符。这是因为 CronJob 控制器将自动在你所提供的 Job 名称后附加
11 个字符，并且存在 Job 名称的最大长度不能超过 63 个字符的限制。

## 示例    {#example}

下面的 CronJob 示例清单会在每分钟打印出当前时间和问候消息：

[使用 CronJob 运行自动化任务](/zh-cn/docs/tasks/job/automated-tasks-with-cron-jobs/)一文会为你详细讲解此例。

## 编写 CronJob 声明信息   {#writing-a-cronjob-spec}

### Cron 时间表语法    {#cron-schedule-syntax}

`.spec.schedule` 字段是必需的。该字段的值遵循 [Cron](https://zh.wikipedia.org/wiki/Cron) 语法：

```
# ┌───────────── 分钟 (0 - 59)
# │ ┌───────────── 小时 (0 - 23)
# │ │ ┌───────────── 月的某天 (1 - 31)
# │ │ │ ┌───────────── 月份 (1 - 12)
# │ │ │ │ ┌───────────── 周的某天 (0 - 6)（周日到周六）
# │ │ │ │ │                          或者是 sun、mon、tue、web、thu、fri、sat
# │ │ │ │ │
# │ │ │ │ │
# * * * * *
```

例如 `0 3 * * 1` 表示此任务计划于每周一凌晨 3 点运行。

该格式也包含了扩展的 “Vixie cron” 步长值。
[FreeBSD 手册](https://www.freebsd.org/cgi/man.cgi?crontab%285%29)中解释如下:

> 步长可被用于范围组合。范围后面带有 `/<数字>` 可以声明范围内的步幅数值。
> 例如，`0-23/2` 可被用在小时字段来声明命令在其他数值的小时数执行
> （V7 标准中对应的方法是 `0,2,4,6,8,10,12,14,16,18,20,22`）。
> 步长也可以放在通配符后面，因此如果你想表达 “每两小时”，就用 `*/2` 。

时间表中的问号 (`?`) 和星号 `*` 含义相同，它们用来表示给定字段的任何可用值。

除了标准语法，还可以使用一些类似 `@monthly` 的宏：

| 输入                   | 描述                      | 相当于        |
| ---------------------- | ------------------------ | ------------ |
| @yearly (或 @annually) | 每年 1 月 1 日的午夜运行一次 | 0 0 1 1 *    |
| @monthly               | 每月第一天的午夜运行一次     | 0 0 1 * *    |
| @weekly                | 每周的周日午夜运行一次       | 0 0 * * 0    |
| @daily (或 @midnight)  | 每天午夜运行一次            | 0 0 * * *    |
| @hourly                | 每小时的开始一次            | 0 * * * *    |

为了生成 CronJob 时间表的表达式，你还可以使用 [crontab.guru](https://crontab.guru/)
这类 Web 工具。

### 任务模板   {#job-template}

`.spec.jobTemplate`为 CronJob 创建的 Job 定义模板，它是必需的。它和
[Job](/zh-cn/docs/concepts/workloads/controllers/job/) 的语法完全一样，
只不过它是嵌套的，没有 `apiVersion` 和 `kind`。
你可以为模板化的 Job 指定通用的元数据，
例如标签或注解。
有关如何编写一个 Job 的 `.spec`，
请参考[编写 Job 规约](/zh-cn/docs/concepts/workloads/controllers/job/#writing-a-job-spec)。

### Job 延迟开始的最后期限   {#starting-deadline}

`.spec.startingDeadlineSeconds` 字段是可选的。
它表示 Job 如果由于某种原因错过了调度时间，开始该 Job 的截止时间的秒数。

过了截止时间，CronJob 就不会开始该 Job 的实例（未来的 Job 仍在调度之中）。
例如，如果你有一个每天运行两次的备份 Job，你可能会允许它最多延迟 8 小时开始，但不能更晚，
因为更晚进行的备份将变得没有意义：你宁愿等待下一次计划的运行。

对于错过已配置的最后期限的 Job，Kubernetes 将其视为失败的 Job。
如果你没有为 CronJob 指定 `startingDeadlineSeconds`，那 Job 就没有最后期限。

如果 `.spec.startingDeadlineSeconds` 字段被设置（非空），
CronJob 控制器将会计算从预期创建 Job 到当前时间的时间差。
如果时间差大于该限制，则跳过此次执行。

例如，如果将其设置为 `200`，则 Job 控制器允许在实际调度之后最多 200 秒内创建 Job。

### 并发性规则   {#concurrency-policy}

`.spec.concurrencyPolicy` 字段也是可选的。它声明了 CronJob 创建的 Job 执行时发生重叠如何处理。
`spec` 仅能声明下列规则中的一种：

* `Allow`（默认）：CronJob 允许并发 Job 执行。
* `Forbid`：CronJob 不允许并发执行；如果新 Job 的执行时间到了而老 Job 没有执行完，CronJob 会忽略新 Job 的执行。
  另请注意，当老 Job 执行完成时，仍然会考虑 `.spec.startingDeadlineSeconds`，可能会导致新的 Job 执行。
* `Replace`：如果新 Job 的执行时间到了而老 Job 没有执行完，CronJob 会用新 Job 替换当前正在运行的 Job。

请注意，并发性规则仅适用于相同 CronJob 创建的 Job。
如果有多个 CronJob，它们相应的 Job 总是允许并发执行的。

### 调度挂起   {#schedule-suspension}

通过将可选的 `.spec.suspend` 字段设置为 `true`，可以挂起针对 CronJob 执行的任务。

这个设置**不**会影响 CronJob 已经开始的任务。

如果你将此字段设置为 `true`，后续发生的执行都会被挂起
（这些任务仍然在调度中，但 CronJob 控制器不会启动这些 Job 来运行任务），
直到你取消挂起 CronJob 为止。

在调度时间内挂起的执行都会被统计为错过的 Job。
当现有的 CronJob 将 `.spec.suspend` 从 `true` 改为 `false` 时，
且没有[开始的最后期限](#starting-deadline)，错过的 Job 会被立即调度。

### 任务历史限制   {#jobs-history-limits}

`.spec.successfulJobsHistoryLimit` 和 `.spec.failedJobsHistoryLimit`
字段指定应保留多少已完成和失败的 Job。这两个字段都是可选的。

* `.spec.successfulJobsHistoryLimit`：此字段指定要保留多少成功完成的 Job。默认值为 `3`。
 将此字段设置为 `0` 意味着不会保留任何成功的 Job。

* `.spec.failedJobsHistoryLimit`：此字段指定要保留多少失败完成的 Job。默认值为 `1`。
 将此字段设置为 `0` 意味着不会保留任何失败的 Job。

有关自动清理 Job 的其他方式，
请参见[自动清理完成的 Job](/zh-cn/docs/concepts/workloads/controllers/job/#clean-up-finished-jobs-automatically)。

## 时区    {#time-zones}

对于没有指定时区的 CronJob，

基于本地时区解释排期表（Schedule）。

你可以通过将 `.spec.timeZone`
设置为一个有效[时区](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)的名称，
为 CronJob 指定一个时区。例如设置 `.spec.timeZone: "Etc/UTC"` 将告诉
Kubernetes 基于世界标准时间解读排期表。

Go 标准库中的时区数据库包含在二进制文件中，并用作备用数据库，以防系统上没有可用的外部数据库。

## CronJob 的限制    {#cron-job-limitations}

### 不支持的时区规范   {#unsupported-timezone-spec}

在 `.spec.schedule` 中通过 `CRON_TZ` 或 `TZ` 变量来指定时区**并未得到官方支持**（而且从未支持过）。
如果你尝试设置一个包含 `TZ` 或 `CRON_TZ` 时区规范的计划，Kubernetes
将因验证错误无法创建或更新资源。
你应该使用[时区字段](#time-zones)指定时区。

### 修改 CronJob   {#modifying-a-cronjob}

按照设计，CronJob 包含一个用于**新** Job 的模板。
如果你修改现有的 CronJob，你所做的更改将应用于修改完成后开始运行的新任务。
已经开始的任务（及其 Pod）将继续运行而不会发生任何变化。
也就是说，CronJob **不**会更新现有任务，即使这些任务仍在运行。

### Job 创建  {#job-creation}

CronJob 根据其计划编排，在每次该执行任务的时候大约会创建一个 Job。
我们之所以说 "大约"，是因为在某些情况下，可能会创建两个 Job，或者不会创建任何 Job。
我们试图使这些情况尽量少发生，但不能完全杜绝。因此，Job 应该是**幂等的**。

从 Kubernetes v1.32 开始，CronJob 为其创建的 Job 添加一个注解 `batch.kubernetes.io/cronjob-scheduled-timestamp`。
此注解表示 Job 最初计划的创建时间，采用 RFC3339 格式。

如果 `startingDeadlineSeconds` 设置为很大的数值或未设置（默认），并且
`concurrencyPolicy` 设置为 `Allow`，则 Job 将始终至少运行一次。

如果 `startingDeadlineSeconds` 的设置值低于 10 秒钟，CronJob 可能无法被调度。
这是因为 CronJob 控制器每 10 秒钟执行一次检查。

对于每个 CronJob，CronJob 
检查从上一次调度的时间点到现在所错过了调度次数。如果错过的调度次数超过 100 次，
那么它就不会启动这个 Job，并记录这个错误:

```
too many missed start times. Set or decrease .spec.startingDeadlineSeconds or check clock skew
```

这种行为适用于补做定时计划中任务的场景，并不表示 CronJob 会停止运行。

例如，如果使用的是 `concurrencyPolicy: Forbid`，则需要长期运行的 Job 可能会错过
事先计划的运行时间，不过一旦前一个 Job 完成，就可以创建新的 Job 了。

需要注意的是，如果 `startingDeadlineSeconds` 字段非空，则控制器会统计从
`startingDeadlineSeconds` 设置的值到现在而不是从上一个计划时间到现在错过了多少次 Job。
例如，如果 `startingDeadlineSeconds` 是 `200`，则控制器会统计在过去 200 秒中错过了多少次 Job。

如果未能在调度时间内创建 CronJob，则计为错过。
例如，如果 `concurrencyPolicy` 被设置为 `Forbid`，并且当前有一个调度仍在运行的情况下，
试图调度的 CronJob 将被计算为错过。

例如，假设一个 CronJob 被设置为从 `08:30:00` 开始每隔一分钟创建一个新的 Job，
并且它的 `startingDeadlineSeconds` 字段未被设置。如果 CronJob 控制器从
`08:29:00` 到 `10:21:00` 终止运行，则该 Job 将不会启动，
因为其错过的调度次数超过了 100。

为了进一步阐述这个概念，假设将 CronJob 设置为从 `08:30:00` 开始每隔一分钟创建一个新的 Job，
并将其 `startingDeadlineSeconds` 字段设置为 200 秒。
如果 CronJob 控制器恰好在与上一个示例相同的时间段（`08:29:00` 到 `10:21:00`）终止运行，
则 Job 仍将从 `10:22:00` 开始。
造成这种情况的原因是控制器现在检查在最近 200 秒（即 3 个错过的调度）中发生了多少次错过的
Job 调度，而不是从现在为止的最后一个调度时间开始。

CronJob 仅负责创建与其调度时间相匹配的 Job，而 Job 又负责管理其代表的 Pod。

## 

* 了解 CronJob 所依赖的 [Pod](/zh-cn/docs/concepts/workloads/pods/) 与
  [Job](/zh-cn/docs/concepts/workloads/controllers/job/) 的概念。
* 阅读 CronJob `.spec.schedule`
  字段的详细[格式](https://pkg.go.dev/github.com/robfig/cron/v3#hdr-CRON_Expression_Format)。
* 有关创建和使用 CronJob 的说明及 CronJob 清单的示例，
  请参见[使用 CronJob 运行自动化任务](/zh-cn/docs/tasks/job/automated-tasks-with-cron-jobs/)。
* `CronJob` 是 Kubernetes REST API 的一部分，
  阅读  API 参考了解更多细节。
