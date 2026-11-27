# runtime-usage-reporting Specification

## Purpose

孔明的运行时用量与健康上报：把产品事件与健康事件可靠推送到锦官，带服务认证、缓冲、重试与幂等；LLM token/费用由 muniu 计量，kongming 只报产品事件。

## Requirements

### Requirement: 用量事件上报

kongming SHALL 上报租户/用户/Bot 维度的产品事件（会话/工具/工作流/任务/报告）；事件 SHALL 含来源、维度、幂等键与时间；控制面不可用 SHALL 本地缓冲并重试；SHALL NOT 上报 LLM token/费用（由 muniu 唯一上报）；SHALL NOT 上报提示词/结果内容或凭据；上报 SHALL 认证服务身份。

#### Scenario: 正常上报

- WHEN 问答完成
- THEN 产品事件发送，控制面聚合

#### Scenario: 不重复上报 LLM

- WHEN 产生 LLM 调用
- THEN 只记录本地引用，不向锦官上报 token/费用

#### Scenario: 断网缓冲

- GIVEN 控制面不可用
- WHEN 产生产品事件
- THEN 本地缓冲，恢复后补发

#### Scenario: 匿名拒绝

- WHEN 无服务身份上报
- THEN 401/403，不落库

### Requirement: 健康上报

kongming SHALL 周期上报健康/版本/能力；控制面 SHALL 更新服务注册表；健康上报失败 SHALL NOT 影响本地服务；上报 SHALL 幂等。

#### Scenario: 健康心跳

- WHEN 周期到达
- THEN 控制面 last_seen 更新

#### Scenario: 上报失败

- GIVEN 控制面不可用
- WHEN 心跳失败
- THEN 本地继续服务，不崩溃
