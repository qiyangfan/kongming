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

### Requirement: 触发器管理与执行历史

系统 SHALL 提供触发器 CRUD+启停+执行历史分页(system:trigger:manage,
租户隔离,跨租户 404 同形);sys_trigger_run SHALL 记录每次触发
(SUCCESS/FAILED/SKIPPED+耗时+错误+目标 run 关联);目标执行
MUST 以定义创建者身份经既有防御层(无权限放大);失败 v1 不自动
重试。

#### Scenario: 管理与历史

- GIVEN 持 system:trigger:manage 的用户
- WHEN 创建/启停/删除触发器并查询历史
- THEN 落库并调度同步;历史分页含状态/耗时/错误

#### Scenario: 触发执行不绕过防御

- GIVEN 触发的工作流含 SQL 节点
- WHEN 触发执行
- THEN SQL 仍走六层防御(创建者身份行级权限适用)

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
