## Purpose

让工作流与 Bot 从「被动问答」升级为「主动执行」:cron 定时触发 +
webhook 入站事件驱动;触发器不自己执行逻辑——调用工作流引擎/Bot
运行入口,纵深防御全量复用,不开绕过。

## Requirements

### Requirement: cron 定时触发

系统 SHALL 支持 cron 五字段表达式(分 时 日 月 周;`*`、`*/n`、
`a-b`、`a,b,c`、单值;日/周双非 * 拒)定时触发工作流或 Bot;非法
表达式 MUST 保存期拒绝(400 文案);触发器启停即时生效;最小间隔
保护(min_interval_sec)MUST 使实际触发间隔不低于保护值;同触发器
前次未完成的再次到期 MUST 记 SKIPPED 不堆积。触发执行 MUST 调用
029/025 运行入口(复用纵深防御,不绕过)。

#### Scenario: 定时触发工作流

- GIVEN cron 触发器(每天 09:00)绑定工作流
- WHEN 到达 09:00(调度 tick 30s 精度)
- THEN 触发该工作流执行,sys_trigger_run 落 SUCCESS 行

#### Scenario: 非法表达式拒绝

- GIVEN 非法 cron 表达式
- WHEN 创建触发器
- THEN 400 拒绝,不注册调度

#### Scenario: 最小间隔保护

- GIVEN 高频 cron(*/1 分钟)且 min_interval_sec=300
- WHEN 多次到期
- THEN 实际触发间隔 ≥300s,其余记 SKIPPED

#### Scenario: 并发不堆积

- GIVEN 触发器前次执行未完成
- WHEN 再次到期
- THEN 记 SKIPPED,不并发执行

### Requirement: webhook 入站触发

系统 SHALL 提供 webhook 入站端点 POST /webhooks/{随机 UUID}(公开,
无 JWT),MUST 校验 HMAC-SHA256 签名(X-Signature=hex(hmac(secret,
body)));签名缺失/错误/路径不存在/触发器禁用 MUST 同形拒绝(401/404
不区分,防探测);body MUST ≤1MB;密钥 MUST 加密落库,任何接口
MUST NOT 回显明文(创建响应一次性展示);payload 经适配器标准化为
目标输入;接收即 202,执行异步。

#### Scenario: 签名正确触发

- GIVEN webhook 触发器(含 secret)
- WHEN 外部 POST 携带正确 X-Signature
- THEN 202 接收,异步触发绑定目标,payload 作为输入

#### Scenario: 签名错误拒绝

- GIVEN webhook 触发器
- WHEN 签名错误或缺失
- THEN 401 同形拒绝,不执行不落 run

#### Scenario: 路径防枚举

- GIVEN webhook 路径为随机 UUID
- WHEN 攻击者尝试按业务 id 枚举
- THEN 无规律可循(路径与业务标识无关联)

### Requirement: 事件源适配器

系统 SHALL 内置 custom(整 payload 透传为 inputs)与 github
(X-GitHub-Event + push/pull_request/issues 标准化摘要:{event, repo,
ref, summary})两类适配器;未知适配器 MUST 保存期拒绝。

#### Scenario: GitHub push 标准化

- GIVEN github 适配器触发器
- WHEN GitHub push 事件带正确签名入站
- THEN 标准化 {event:"push", repo, ref, summary} 作为目标输入

#### Scenario: custom 透传

- GIVEN custom 适配器触发器
- WHEN 任意 JSON payload 带正确签名入站
- THEN 整 payload 作为 inputs 透传

### Requirement: cron 预览

系统 SHALL 提供 cron 表达式预览端点(`GET /system/triggers/cron-preview?expr=`,
system:trigger:manage 权限):返回按 Asia/Shanghai 时区自当前时刻起接下来的
3 次触发时刻(ISO 8601,时间递增);表达式非法或不可计算 SHALL 400 并附
可读原因。预览与创建/更新校验及调度器 SHALL 同源(同一解析/计算实现,
不出现第二套 cron 语义)。

#### Scenario: 预览返回递增的三次触发时刻

- GIVEN 合法表达式 `0 9 * * *`
- WHEN 请求预览
- THEN 返回 3 个按时间递增的 ISO 时刻,首值为次日 09:00(Asia/Shanghai)

#### Scenario: 非法表达式被拒

- GIVEN 表达式 `61 * * * *`
- WHEN 请求预览
- THEN 返回 400 并附可读原因(与创建期校验同文案口径)

### Requirement: 触发器管理与执行历史

系统 SHALL 提供触发器 CRUD+启停+执行历史分页(system:trigger:manage,
租户隔离,跨租户 404 同形);sys_trigger_run SHALL 记录每次触发
(SUCCESS/FAILED/SKIPPED+耗时+错误+目标 run 关联);目标执行
MUST 以定义创建者身份经既有防御层(无权限放大);失败重试策略见
「失败重试策略」requirement(缺省不重试)。执行历史 SHALL 呈现
重试链:每行含尝试序号(attempt),重试行 SHALL 可追溯其首跑(重试来源关联)。

#### Scenario: 管理与历史

- GIVEN 持 system:trigger:manage 的用户
- WHEN 创建/启停/删除触发器并查询历史
- THEN 落库并调度同步;历史分页含状态/耗时/错误

目标已被删除的存量触发器 SHALL 仍可停用/删除/修改非目标字段(止损路径不被堵)。

#### Scenario: 目标已删的存量触发器可止损

- GIVEN 存量触发器目标 workflow 已被删除
- WHEN 停用该触发器或修改非目标字段(名称/cron 等)
- THEN 正常执行(止损不被堵;派发失败由执行历史可见)

#### Scenario: 触发执行不绕过防御

- GIVEN 触发的工作流含 SQL 节点
- WHEN 触发执行
- THEN SQL 仍走六层防御(创建者身份行级权限适用)

#### Scenario: 历史呈现重试链

- GIVEN 某触发器首跑 FAILED 后经 2 次重试成功
- WHEN 查询执行历史
- THEN 可见 3 行(attempt=1/2/3),重试行可追溯到首跑,末行 SUCCESS

### Requirement: 触发器创建期目标校验

创建 SHALL 校验 target_key 在当前租户对应目标域(workflow/bot)中真实存在,更新
仅在 target_key 变更时重走同校验,不存在即 400 `TARGET_NOT_FOUND`——写边界
fail-fast,防拼错目标静默挂到运行期才暴露;校验与派发期目标解析同口径(同源,不
出现第二套存在性判定)。target_type=task 无 target_key 引用,见「task 立项目标」
requirement 的创建期模板校验。

#### Scenario: 目标不存在创建被拒

- GIVEN 目标域中不存在 key 为 `wf_report` 的工作流
- WHEN 创建 target_type=workflow、target_key=wf_report 的触发器
- THEN 返回 400 TARGET_NOT_FOUND,不落库、不进调度

#### Scenario: 更新改指不存在目标被拒

- GIVEN 存量触发器原目标存在
- WHEN 更新请求把 target_key 改为不存在的工作流 key
- THEN 返回 400 TARGET_NOT_FOUND,原值不变

#### Scenario: 目标存在正常创建

- GIVEN 目标域存在启用中的 bot `after_sale`
- WHEN 创建 target_type=bot、target_key=after_sale 的触发器
- THEN 落库并调度同步

### Requirement: 出站通知

触发器可配 notify_url;目标执行完成后系统 SHALL POST 结果摘要
(带 X-Kongming-Signature=HMAC 签名);notify_url MUST 先经 SSRF
判定(029 拒绝段),落入拒绝段 MUST NOT 发送(记 run 错误段);
超时 5s。

#### Scenario: 结果出站推送

- GIVEN 触发器配置了公网 notify_url
- WHEN 目标执行完成
- THEN 结果摘要带签名 POST 到该地址

#### Scenario: 通知 SSRF 拒绝

- GIVEN notify_url 指向 169.254.169.254
- WHEN 执行完成
- THEN 不发送,run 记录通知拒绝原因

### Requirement: 失败重试策略

触发器 SHALL 支持失败重试次数上限配置(0–5,缺省 0=不重试,与既有行为
零回归)。FAILED 终态且剩余重试次数 >0 时,调度器 SHALL 按固定退避间隔
(部署级可配,缺省 60s)安排一次重试;重试为**独立执行历史行**(尝试序号
递增并关联首跑),MUST NOT 覆盖或改写先前失败行。重试执行 SHALL 与首跑
同身份(定义创建者)、同防御层(无权限放大)、同目标解析口径。重试链内
任一次 SUCCESS SHALL 终止重试(链终态成功);次数耗尽仍失败 SHALL 终态
FAILED。SKIPPED 行 SHALL NOT 触发重试(并发保护语义,不是执行失败);
「前次未完成记 SKIPPED」既有语义对重试行同样生效(重试执行期间到期的
cron 触发记 SKIPPED,不并发堆积)。触发器被停用或删除时,挂起未执行的重试
SHALL 取消(不再派发)。出站通知 SHALL 只在重试链终态发送一次(中间失败
不发送),避免通知风暴。

#### Scenario: 失败后自动重试成功

- GIVEN 触发器 max_retries=3,目标工作流首跑因外部系统抖动 FAILED
- WHEN 退避间隔到期
- THEN 调度器派发重试(attempt=2,关联首跑),执行成功则链终态 SUCCESS,
  不再继续重试

#### Scenario: 重试耗尽终态失败

- GIVEN 触发器 max_retries=2,连续 3 次(首跑+2 重试)均 FAILED
- WHEN 第 3 次失败落定
- THEN 链终态 FAILED,不再安排重试,历史保留全部 3 行

#### Scenario: 缺省不重试零回归

- GIVEN 触发器未配置重试(缺省 max_retries=0)
- WHEN 执行 FAILED
- THEN 行为与引入前一致(单行终态,无重试派发)

#### Scenario: SKIPPED 不触发重试

- GIVEN 触发器 max_retries=3,执行因「前次未完成」被记 SKIPPED
- WHEN 记录落定
- THEN 不安排重试(SKIPPED 非执行失败)

#### Scenario: 停用取消挂起重试

- GIVEN 某触发器存在挂起中的重试(退避未到期)
- WHEN 管理员停用该触发器
- THEN 挂起重试取消,退避到期后不再派发;重新启用从下个 cron 周期正常触发

#### Scenario: 通知只在链终态发一次

- GIVEN 触发器配置 notify_url 且 max_retries=2
- WHEN 首跑 FAILED、重试 SUCCESS
- THEN 出站通知恰发一次(终态成功结果),首跑失败不发送通知

### Requirement: 邮件入站触发

系统 SHALL 支持邮件入站触发器:配置含 IMAP 主机/端口/TLS 开关/账号/凭证/
文件夹/轮询间隔;凭证 SHALL 加密落库(与既有 webhook 密钥同一加密机制),
任何接口 MUST NOT 回显明文(创建/编辑响应与列表均掩码;编辑空框=保持
现值)。目标(workflow/bot)校验、TARGET_NOT_FOUND 写边界、租户隔离
(跨租户 404 同形)、执行历史、创建者身份执行与全量防御层复用既有触发器
口径。轮询 SHALL 复用既有调度 tick;UID 游标、标准化摘要、失败处理与停用
语义见「邮件轮询的游标续传与隐私最小化」requirement。

#### Scenario: 新邮件触发目标执行

- GIVEN 已启用邮件触发器(目标为工作流),邮箱收到新邮件
- WHEN 轮询 tick 到期
- THEN 新邮件经标准化摘要触发工作流执行,sys_trigger_run 落 SUCCESS
  且记录标准化摘要

#### Scenario: 凭证不回显

- GIVEN 已配置 IMAP 凭证的邮件触发器
- WHEN 查询触发器详情/列表
- THEN 凭证掩码返回,明文不出现在任何响应/日志/审计中

#### Scenario: 连接失败落历史不堆积

- GIVEN 目标 IMAP 主机不可达
- WHEN 轮询 tick 到期
- THEN 该触发器落 FAILED run(含错误分类),不并发重试堆积,其他
  触发器与调度器不受影响

### Requirement: 邮件轮询的游标续传与隐私最小化

轮询 SHALL 复用既有调度 tick;**每触发器按文件夹维护已处理 UID
游标并持久化**:游标后的新邮件逐一触发,游标推进与触发记录同事务,
重启/断线后 SHALL 续传(不重复处理已触发邮件,MUST NOT 跳过未处理邮件)。
新邮件 SHALL 经适配器标准化为 `{from, subject, date, text_preview}`
摘要(正文预览截断受上限约束)作为目标输入,标准化文本进目标执行前过
既有输入护栏;邮件原文与完整正文 MUST NOT 落库(run 记录只含标准化
摘要,隐私最小化)。轮询连接/认证/协议失败 SHALL 记 FAILED run(错误
分类),MUST NOT 堆积并发、MUST NOT 影响其他触发器与调度器(重试策略
由失败重试策略统一承接);触发器停用/删除后轮询停止且游标保留
(重新启用从游标续传)。

#### Scenario: UID 游标续传不重复

- GIVEN 某文件夹 3 封邮件已触发(游标=UID 100)
- WHEN 调度器重启后再轮询,文件夹新到第 4 封(UID 101)
- THEN 仅第 4 封触发,前 3 封不重复(游标续传)

#### Scenario: 停用后游标保留

- GIVEN 游标=UID 100 的邮件触发器被停用,期间新到 UID 101/102 两封
- WHEN 重新启用后首次轮询
- THEN 从游标续传:UID 101/102 触发,停用期间的邮件不丢失

### Requirement: task 立项目标

系统 SHALL 支持 target_type=task 的触发器:配置含任务模板(目标文本模板/
数据源 key/预算/可选协作拓扑/确认模式)。事件到达时系统 SHALL 以模板+
事件载荷渲染目标文本,以**触发器定义创建者身份**创建任务(创建者账号态闸
复用,无权限放大);预算 SHALL 经既有部署级上限钳制;数据源可见性、SQL
执行防御、步骤审批门与任务事件账本 SHALL 与手工创建任务完全同口径。
触发 run 的 SUCCESS 语义为**立项成功**(任务后续生命周期归任务域账本,
不构成触发成败)。可选协作拓扑 SHALL 在计划生成后经既有拓扑声明路径应用,
应用失败 SHALL 落任务事件并以串行形态继续,SHALL NOT 静默丢弃声明。

target_type=task 无 target_key 引用,SHALL 以模板校验替代存在性校验:目标文本
模板/数据源 key(须对创建者可见)/预算/可选协作拓扑在创建期 fail-fast,非法即
400;校验口径与派发期立项参数同源。

#### Scenario: task 目标模板非法创建被拒

- GIVEN 触发器创建者对数据源 `ds_x` 无可见权限
- WHEN 创建 target_type=task、模板引用 `ds_x` 的触发器
- THEN 返回 400(模板校验失败),不落库、不进调度

#### Scenario: webhook 事件立项

- GIVEN task 目标触发器绑定 GitHub issue 适配器
- WHEN 合法 issue 事件通过签名校验与过滤闸
- THEN 以触发器创建者身份创建任务,run 记 SUCCESS 且目标 run 关联为任务 id

#### Scenario: 立项不放大权限

- GIVEN 触发器创建者仅对数据源 A 有权限,模板引用数据源 A
- WHEN 事件触发立项
- THEN 任务以创建者身份执行,SQL 走六层防御;任务可见性遵循任务域既有归属规则

#### Scenario: 模板拓扑应用失败可见

- GIVEN 模板声明的协作拓扑引用的 bot 已被删除
- WHEN 事件触发立项且计划生成
- THEN 任务事件记录拓扑应用失败,任务以串行形态继续,不静默丢弃

### Requirement: 两级事件过滤

task 目标的 WEBHOOK/EMAIL 事件 SHALL 经两级过滤后才立项:**程序层**
按触发器配置的确定性条件(动作类型/字段匹配)在调用语义判读前筛除噪音;
**语义层**对通过程序层的候选事件做单次判读(是否值得立项),判读结论与
理由 SHALL 落触发 run 历史。判读不可用(端点不可达/超时/报文畸形)SHALL
按 SKIPPED 处理并留原因,SHALL NOT 默认立项;SKIPPED SHALL NOT 触发重试。
CRON 触发 SHALL NOT 经过滤闸(定时触发无事件载荷,立项意图在创建期
已由模板确定)。判读内容 SHALL 只含事件标准化摘要,SHALL NOT 含凭据或
完整原始载荷。

#### Scenario: 程序层筛除噪音

- GIVEN 触发器配置仅接受 action=opened 的 GitHub 事件
- WHEN 一个 action=labeled 的事件到达
- THEN 程序层直接筛除,run 记 SKIPPED 与原因,不调用语义判读

#### Scenario: 语义判读否决立项

- GIVEN 候选事件通过程序层
- WHEN 语义判读结论为「不值得立项」
- THEN 不创建任务,run 记 SKIPPED 并含判读理由

#### Scenario: 判读不可用不默认立项

- GIVEN 候选事件到达但判读端点不可达
- WHEN 过滤闸执行
- THEN 记 SKIPPED(原因注明判读不可用),不立项、不重试

#### Scenario: CRON 免语义判读

- GIVEN task 目标的 CRON 触发器到期
- WHEN 调度器派发
- THEN 直接立项,不调用语义判读

### Requirement: 线程去重与事件归属

task 目标触发器 SHALL 支持线程键去重:线程键由适配器按事件推导
(GitHub 适配器 SHALL 内建 repo+issue/PR 推导;custom/email SHALL 支持
配置声明字段,未配置时视为无线程键)。同线程键存在未终态触发任务时,
后续事件 SHALL NOT 重复立项,SHALL 追加为该任务的任务事件(含事件来源
与摘要);既有任务终态后同线程键新事件 SHALL 立项新任务。

#### Scenario: 同线程不重复立项

- GIVEN 同一 GitHub issue 已存在未终态触发任务
- WHEN 该 issue 的新评论事件到达且通过过滤闸
- THEN 不创建新任务,事件追加为既有任务的账本事件

#### Scenario: 终态后重新立项

- GIVEN 同线程键的既有触发任务已 DONE
- WHEN 同线程新事件到达且通过过滤闸
- THEN 立项新任务,与旧任务无继承关系

### Requirement: 触发任务确认门

触发创建的任务 SHALL 默认进入**待确认**形态:落库为 PAUSED 并附
「触发立项待确认」任务事件,SHALL NOT 自动派发;归属人显式恢复后按
既有恢复语义派发。触发器 SHALL 可配置自动派发模式:确认门关闭时,
立项即按既有创建路径派发。两种模式下,预算钳制、步骤审批门、账本留痕
SHALL 完全一致。

#### Scenario: 默认待确认

- GIVEN 触发器为缺省确认模式
- WHEN 事件通过过滤闸立项
- THEN 任务落 PAUSED 且事件注明「触发立项待确认」,无编排派发

#### Scenario: 归属人确认后派发

- GIVEN 存在待确认的触发任务
- WHEN 归属人(task:manage)恢复该任务
- THEN 按既有恢复语义进入编排,与手工创建任务的恢复同口径

#### Scenario: 自动派发模式

- GIVEN 触发器显式配置自动派发
- WHEN 事件通过过滤闸立项
- THEN 任务创建后立即按既有创建路径派发,审批门与账本照常
