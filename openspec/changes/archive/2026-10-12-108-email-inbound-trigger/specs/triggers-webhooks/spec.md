# Spec Delta

## ADDED Requirements

### Requirement: 邮件入站触发

系统 SHALL 支持邮件入站触发器:配置含 IMAP 主机/端口/TLS 开关/账号/凭证/
文件夹/轮询间隔;凭证 SHALL 加密落库(与既有 webhook 密钥同一加密机制),
任何接口 MUST NOT 回显明文(创建/编辑响应与列表均掩码;编辑空框=保持
现值)。目标(workflow/bot)校验、TARGET_NOT_FOUND 写边界、租户隔离
(跨租户 404 同形)、执行历史、创建者身份执行与全量防御层复用既有触发器
口径。轮询 SHALL 复用既有调度 tick;**每触发器按文件夹维护已处理 UID
游标并持久化**:游标后的新邮件逐一触发,游标推进与触发记录同事务,
重启/断线后 SHALL 续传(不重复处理已触发邮件,MUST NOT 跳过未处理邮件)。
新邮件 SHALL 经适配器标准化为 `{from, subject, date, text_preview}`
摘要(正文预览截断受上限约束)作为目标输入,标准化文本进目标执行前过
既有输入护栏;邮件原文与完整正文 MUST NOT 落库(run 记录只含标准化
摘要,隐私最小化)。轮询连接/认证/协议失败 SHALL 记 FAILED run(错误
分类),MUST NOT 堆积并发、MUST NOT 影响其他触发器与调度器(重试策略
由失败重试策略统一承接);触发器停用/删除后轮询停止且游标保留
(重新启用从游标续传)。

#### Scenario: 新邮件触发目标执行

- GIVEN 已启用邮件触发器(目标为工作流),邮箱收到新邮件
- WHEN 轮询 tick 到期
- THEN 新邮件经标准化摘要触发工作流执行,sys_trigger_run 落 SUCCESS
  且记录标准化摘要

#### Scenario: UID 游标续传不重复

- GIVEN 某文件夹 3 封邮件已触发(游标=UID 100)
- WHEN 调度器重启后再轮询,文件夹新到第 4 封(UID 101)
- THEN 仅第 4 封触发,前 3 封不重复(游标续传)

#### Scenario: 凭证不回显

- GIVEN 已配置 IMAP 凭证的邮件触发器
- WHEN 查询触发器详情/列表
- THEN 凭证掩码返回,明文不出现在任何响应/日志/审计中

#### Scenario: 连接失败落历史不堆积

- GIVEN 目标 IMAP 主机不可达
- WHEN 轮询 tick 到期
- THEN 该触发器落 FAILED run(含错误分类),不并发重试堆积,其他
  触发器与调度器不受影响

#### Scenario: 停用后游标保留

- GIVEN 游标=UID 100 的邮件触发器被停用,期间新到 UID 101/102 两封
- WHEN 重新启用后首次轮询
- THEN 从游标续传:UID 101/102 触发,停用期间的邮件不丢失
