# observability spec delta(061-unify-backend-logging)

## ADDED Requirements

### Requirement: 运行日志可观测

系统 SHALL 以单一日志 API 与单一输出流记录运行事件,SHALL NOT 出现同一事件
按模块分流至不同流或不同格式。日志级别 SHALL 可经环境变量配置,缺省 INFO。
INFO 及以上运行事件 SHALL NOT 因缺少日志配置而被静默丢弃。运行日志 SHALL
可关联 trace_id:控制面响应 SHALL 回落 `X-Trace-Id` 头,且日志记录 SHALL
携带同一 trace_id,使用户侧报障可定位到用量明细、审计记录与 trace 时间线
同一轮问答。

#### Scenario: 定时任务执行结果可见

- GIVEN 每日审计归档任务执行完成
- WHEN 查看进程运行日志
- THEN 可见该次执行的结果摘要(INFO 级),而非无任何输出

#### Scenario: 日志级别可配置

- GIVEN 将日志级别配置为 WARNING 并重启进程
- WHEN 发生一次 INFO 级运行事件与一次 WARNING 级运行事件
- THEN INFO 级不输出,WARNING 级仍输出

#### Scenario: 报障可定位到同一轮

- GIVEN 用户提供一次报障与响应头中的 X-Trace-Id 值
- WHEN 运维以该 trace_id 查询用量明细与 trace 时间线
- THEN 命中同一轮问答的事件集合,且该轮运行日志可被关联检索

#### Scenario: 单一输出流与格式

- GIVEN 任意模块(含调度、清理、出站客户端)记录一条运行事件
- WHEN 查看进程输出
- THEN 事件落在同一流、同一字段格式,不存在部分模块走 stdout、
  部分走 stderr 的两种形态

#### Scenario: 缺配置不静默丢弃

- GIVEN 进程以缺省配置启动(未显式配置日志)
- WHEN 发生一次 INFO 级运行事件
- THEN 该事件被输出,而非被静默丢弃
