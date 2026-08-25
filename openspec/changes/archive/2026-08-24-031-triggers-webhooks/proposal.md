# 031 triggers-webhooks:触发器与入站事件

## Why

029 工作流解决了「做什么」,但执行仍需人点「运行」。本变更补「何时做」:
cron 定时触发 + webhook 入站事件驱动,与工作流/Bot 配对形成完整自动化
(上游 triggers-webhooks 域的 Kongming 适配)。触发器本身不执行逻辑——
调用 029 工作流引擎/025 Bot 运行入口,纵深防御全量复用,不开绕过。

## What Changes

- **触发器定义**(api 新 `trigger` 域包 + 0019 迁移
  sys_trigger_definition/sys_trigger_run):类型 CRON|WEBHOOK;
  目标 workflow(029 wf_key)或 bot(025 bot_key);cron 五字段表达式
  校验(手写解析器,零新依赖);webhook 随机 UUID 路径(防枚举)+
  HMAC-SHA256 签名(密钥 AES-GCM 落库不回显);启停;租户隔离;
  system:trigger:manage 权限点(排序 24)。
- **cron 调度器**:lifespan 薄壳(015 归档调度先例)——30s .tick 扫描
  到期触发器(next_fire_at 预计算列),最小间隔保护(min_interval_sec
  防 */1 风暴),同触发器并发不堆积(进行中再到期记 SKIPPED)。
- **webhook 入站**:POST /webhooks/{uuid}(公开端点,无 JWT)——
  HMAC 签名校验(X-Signature=hex(hmac_sha256(secret, body)),
  缺失/错误 401 同形不执行);事件源适配器:custom(整 payload 透传)
  与 github(X-GitHub-Event+标准化 push/pull_request/issues 摘要);
  payload 经适配器标准化为目标输入。
- **执行与历史**:触发即调 029 service.run(workflow)或 025
  service.run(bot,创建者身份,channel="cron"/"webhook" 审计溯源);
  sys_trigger_run 全量落(status/耗时/错误/目标 run 关联);分页查询;
  **失败 v1 不自动重试**(Non-goal 声明,挂账)。
- **出站通知**:触发器可配 notify_url——目标执行完成后 POST 结果
  摘要(带 X-Kongming-Signature HMAC 签名);SSRF 防护复用 029
  http_runner 判定(deny 段拒绝即记失败不发送)。
- **web**:/admin/triggers 第十三子页(列表/新建编辑[cron 校验反馈/
  webhook URL 与密钥一次性展示]/启停/删除受控确认/历史分页)。

## Capabilities

### New Capabilities

- `triggers-webhooks`:cron 触发、webhook 入站、事件源适配、执行
  历史与并发控制、出站通知的行为契约。

### Modified Capabilities

- 无(调用经 029/025 既有入口,不改被调域)。

## Non-goals

- 不做失败自动重试(v1 记录 FAILED,重试策略随后续);不做多实例
  调度互斥(单进程部署形态;多副本经 leader 选举/分布式锁随后续);
  不做 cron 秒级字段/时区表达式(固定 Asia/Shanghai 五字段);
  不做更多事件源(邮件/Slack 逐个接入,长期)。
- 不做 webhook 响应自定义(恒 202 快速应答,执行异步);不做触发
  器级速率配额(最小间隔保护已覆盖风暴面);不做 payload 大小超过
  1MB 的接收(传输层闸)。
- 不改工作流/Bot 定义;不引入 APScheduler 等调度库(薄壳扫描器
  足够:30s 精度对日报类场景绰绰有余)。

## Impact

- **代码**:api 新 trigger 域包(models[cron 解析纯函数]/ports/
  repository/scheduler/service/router/adapters)+ lifespan 调度接线 +
  web TriggersPage。
- **Schema**:0019 迁移两表(ADR-004 约定)。
- **契约**:/system/triggers/* 管理端点 + /webhooks/{uuid} 入站;
  make contracts。
- **ADR 引用**:ADR-002(触发执行零新凭证面)/ADR-004/ADR-005。
- **安全**:HMAC 签名/随机 UUID 路径/密钥加密不回显/最小间隔/
  并发不堆积/SSRF(通知出站)/目标执行全量防御;测试先行(签名
  错误/枚举防护/并发跳过/ cron 校验/隔离)。
- **依赖**:零新依赖(cron 解析手写;HMAC=hmac/hashlib 标准库)。
