# Proposal

## Why

触发器入站形态现有 cron(定时)与 webhook(HTTP 推送)两类,但最常见的
被动事件源——邮件——没有接入路径:审批通知、告警邮件、客户来信等「以
邮件到达为起点」的自动化(解析→建工单→触发工作流)只能人工搬运。产品
待办已登记「更多事件源(邮件/Slack)」;Slack 需外部 App 审批(卡外部
条件),邮件经 IMAP 轮询完全自持,是事件源广度的可落地增量。Python 标准库
`imaplib` 即够,零新依赖。

## What Changes

- 触发器新增邮件入站形态:配置(IMAP 主机/端口/TLS/账号/凭证/文件夹/
  轮询间隔),凭证加密落库(复用 webhook 密钥既有机制),任何接口不回显
  明文;触发器管理/启停/历史/租户隔离/目标校验(TARGET_NOT_FOUND)全部
  复用既有口径。
- 轮询语义:复用既有调度 tick;**UID 游标持久化**(每邮箱文件夹记录已
  处理 UID,断线/重启续传不重复不漏);新邮件经适配器标准化为
  `{from, subject, date, text_preview}` 摘要作为目标输入。
- 失败语义:轮询连接/认证失败按 FAILED 记 run 历史(错误分类),不堆积
  不重试(重试策略由 101 统一承接);单触发器轮询失败 MUST NOT 影响
  其他触发器与调度器。
- 摘要文本进目标执行前过既有输入护栏;邮件原文不落库(只留标准化摘要
  于 run 记录,隐私最小化)。
- e2e 依赖真实 IMAP 邮箱(演示待邮箱账号);测试以内存 IMAP 桩承载。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `triggers-webhooks`:新增「邮件入站触发」requirement——配置与凭证
  治理、UID 游标幂等轮询、标准化摘要、失败分类与隔离。

## Impact

- `apps/api`:triggers 域(邮件触发器配置/游标存储/轮询器/适配器);
  新迁移(触发器邮件配置列/游标表,UUID v7+timestamptz 口径)。
- `apps/web`:触发器表单新增邮件形态分支(配置字段+凭证空框语义)。
- `apps/agent`:零改动;contracts 经管线重生成。
- 外部依赖:零新包(imaplib 标准库);演示前置=真实 IMAP 邮箱
  (HANDOFF 记配方)。
