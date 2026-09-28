# observability spec delta(069-chat-quality-observability)

## ADDED Requirements

### Requirement: 轮次质量观测

系统 SHALL 提供问答轮次的质量观测面。轮次终态落库(persist=true)时 SHALL 记录
四个质量信号:trace_id(与 LLM 用量/SQL 执行审计的关联键)、selfheal 重试次数、
指代消解后的改写问题、prompt 版本指纹;其中改写问题 SHALL 仅在多轮指代消解
实际发生(改写结果与原问题不同)时非空。四信号为管理面诊断字段,SHALL NOT 进入
用户响应与流式终态事件。

系统 SHALL 提供质量汇总端点:按时间范围(上限 90 天)返回轮次四态
(SUCCESS/REJECTED/NO_DATA/FAILED)占比、selfheal 率、点踩率与平均 token,
SHALL 提供按天趋势(Asia/Shanghai 日历日,逐日补零,口径与用量趋势一致),
SHALL 提供数据源切片与 prompt 版本切片。系统 SHALL 提供质量明细分页端点:
按状态、数据源、时间范围筛选,每行含时间、问题、改写问题、状态、重试次数、
用户评分、trace_id。NO_DATA 轮次(无 SQL 执行、不落执行审计)SHALL 在汇总与
明细中可见——该形态今日在任何观测面不可见。汇总与明细端点 SHALL 以
`system:chat-quality:view` 权限点保护(仅 SUPER_ADMIN 种子覆盖),无权限返回
403。观测查询 MUST NOT 影响采集通道与主链路;观测聚合 SHALL 按租户维度收敛,
SHALL NOT 提供按用户行级切分(与「观测面数据边界」既定立约一致)。

#### Scenario: 多轮轮次落库改写问题与重试次数

- GIVEN 一次多轮问答(指代消解发生且 selfheal 触发 1 次),persist=true
- WHEN 轮次终态落库
- THEN assistant 消息含 trace_id、retry_count=1、resolved_question 为改写后的自包含问题
- AND 该轮次在质量明细分页中可见

#### Scenario: 单轮不写改写问题

- GIVEN 一次无历史上下文的单轮问答,persist=true
- WHEN 轮次终态落库
- THEN resolved_question 为空(无改写发生),retry_count 如实记录(无重试为 0)

#### Scenario: NO_DATA 轮次可观

- GIVEN 某时间窗口内发生过知识未覆盖(NO_DATA)轮次——此类轮次无 SQL 执行、不落执行审计
- WHEN 查询质量汇总或明细分页
- THEN NO_DATA 计入四态占比与趋势,明细可定位到具体轮次

#### Scenario: 趋势按天补零

- GIVEN 查询窗口内某天无任何问答
- WHEN 查询按天趋势
- THEN 该天返回零值桶,桶数与窗口天数一致,日期口径为 Asia/Shanghai

#### Scenario: 数据源切片定位质量洼地

- GIVEN 两个数据源的问答成功率与 NO_DATA 率差异显著
- WHEN 查询质量汇总
- THEN 返回按数据源的切片(轮次数、四态占比、点踩率),可直接定位质量洼地

#### Scenario: prompt 版本切片归因

- GIVEN 两次 prompt 资产变更前后各产生过轮次
- WHEN 查询质量汇总
- THEN 返回按 prompt 版本的切片,支持「哪版 prompt 的线上质量更好」的对比

#### Scenario: 租户隔离

- GIVEN 租户 T1 与 T2 在同窗口均有问答轮次
- WHEN 以 T1 的数据范围查询(非超管按请求租户收敛)
- THEN 仅返回 T1 的聚合与明细,不跨租户混算

#### Scenario: 无权限拒绝

- GIVEN 登录用户角色不含 system:chat-quality:view
- WHEN 调用汇总或明细分页端点
- THEN 返回 403,且不返回任何聚合数据(与观测面既有权限拒绝形态一致)

#### Scenario: persist=false 不观测

- GIVEN persist=false 的问答调用(如评估场景)
- WHEN 轮次完成
- THEN 不落消息、无质量信号记录,汇总与明细数据不受影响

#### Scenario: 观测查询与主链路隔离

- GIVEN 采集通道(用量/SQL 审计)正常工作
- WHEN 质量汇总或明细端点因任意原因失败
- THEN 主链路与采集行为不受影响(观测面只读聚合的既有边界保持)
