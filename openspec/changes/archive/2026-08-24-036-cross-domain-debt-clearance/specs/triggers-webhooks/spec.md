## MODIFIED Requirements

### Requirement: cron 定时触发

系统 SHALL 支持 cron 五字段表达式(分 时 日 月 周;`*`、`*/n`、
`a-b`、`a,b,c`、单值,以及 Vixie `a/b` 形态[从 a 起步进 b,
`*/n` 为 a=0 特例];日/周双非 * 拒)定时触发工作流或 Bot;非法
表达式 MUST 保存期拒绝(400 文案);触发器启停即时生效;最小间隔
保护(min_interval_sec)MUST 使实际触发间隔不低于保护值;同触发器
前次未完成的再次到期 MUST 记 SKIPPED 不堆积。触发执行 MUST 调用
029/025 运行入口(复用纵深防御,不绕过)。**next_fire 计算 MUST
为日历字段跳跃式**(月/日/时/分逐级推进),对任意合法表达式的
计算耗时 MUST 与表达式跨度无关(远未来表达式[如每年一次]保存期
不得出现逐分钟级迭代的秒级阻塞)。

#### Scenario: 定时触发工作流

- GIVEN cron 触发器(每天 09:00)绑定工作流
- WHEN 到达 09:00(调度 tick 30s 精度)
- THEN 触发该工作流执行,sys_trigger_run 落 SUCCESS 行

#### Scenario: 非法表达式拒绝

- GIVEN 非法 cron 表达式
- WHEN 创建触发器
- THEN 400 拒绝,不注册调度

#### Scenario: a/b 形态兼容

- GIVEN cron 表达式「5/10 * * * *」(从 5 分起步进 10:5,15,25,35,45,55)
- WHEN 计算下次触发
- THEN 按该分钟序列推进(与 Vixie 语义一致),不拒不错解

#### Scenario: 远未来表达式即时计算

- GIVEN cron 表达式「0 0 1 1 *」(每年 1 月 1 日)
- WHEN 保存触发器或计算 next_fire
- THEN 毫秒级返回正确的下个 1 月 1 日(无逐 tick 迭代阻塞)

#### Scenario: 最小间隔保护

- GIVEN 高频 cron(*/1 分钟)且 min_interval_sec=300
- WHEN 多次到期
- THEN 实际触发间隔 ≥300s,其余记 SKIPPED

#### Scenario: 并发不堆积

- GIVEN 触发器前次执行未完成
- WHEN 再次到期
- THEN 记 SKIPPED,不并发执行
