## ADDED Requirements

### Requirement: 派生量接地与数值白名单

结果智能分析输出的 summary 与 insights 文本中的数值 MUST 来自以下白名单
之一:①本次查询结果集的数值单元格;②服务端确定性统计(stats)字段;
③计算工具本轮返回值;④结构性常量(结果行数、预览行数等系统自身给出的
计数)。不在白名单的数值 SHALL NOT 出现。增幅、占比、倍数、差值、复合
变化率等**派生量**若不在白名单,MUST NOT 由语言模型心算写入:要么经计算
工具/服务端预计算得到并入白名单,要么省略该表述(宁省略,不编造)。

#### Scenario: 增长率不得心算

- GIVEN 结果集两行数值 100 与 80,stats 无增长率字段,本轮无计算工具返回
- WHEN 生成 summary 与 insights
- THEN 输出不出现「增长 25%」或任何比率数值
- AND 允许「由 80 变为 100」这类仅含白名单数字的表述

#### Scenario: 计算工具结果可写

- GIVEN 模型调用计算工具得到 delta_pct=25.0
- WHEN 生成 summary
- THEN 允许写「增长 25%」(工具返回值在白名单)

#### Scenario: 结构性常量不误伤

- GIVEN 结果共 10 行,统计给出 avg=3.5
- WHEN 生成 summary
- THEN 允许出现 10 与 3.5,不被审计剥离

### Requirement: 确定性数值审计

总结收口 SHALL 对 summary 与 insights 文本做确定性数值抽取与白名单比对
(数值等价比较,百分数/千分位先归一化)。含非白名单数值的句子 SHALL 被
剥离或降级为不含该数值的表述;审计 SHALL NOT 触发语言模型重试、SHALL NOT
阻断 SQL 与结果字段。审计对结构性常量零误伤;零违规时输出与审计前逐字
一致(零回归)。

#### Scenario: 违规句剥离

- GIVEN summary 原文含一句「环比增长 15%」而 15 不在白名单
- WHEN 审计收口
- THEN 该句不出现在最终 summary
- AND 其余白名单合法句保留,columns/rows/stats 不变

#### Scenario: 零违规零回归

- GIVEN summary 全部数值均在白名单
- WHEN 审计收口
- THEN 输出与审计前逐字一致

### Requirement: 计算工具

系统 SHALL 提供确定性计算工具(至少增幅与占比两类),入参为显式数值,
输出确定性结果并纳入本轮数值白名单。入参 SHALL 校验属于本轮白名单
(结果集/stats/既有工具值),不属于则拒绝并返回可纠正错误。计算工具
SHALL 为只读无副作用,不写任何业务数据。

#### Scenario: 入参白名单校验

- GIVEN 模型以编造基数 50 调用占比计算
- WHEN 工具执行
- THEN 拒绝执行并返回纠正指引(入参不在本轮数值白名单)
- AND 不产生输出值

### Requirement: 趋势与异常类洞察收紧

insights 类型为 trend(趋势)时,SHALL 要求存在不少于 3 期可排序序列且
结论数字在白名单;类型为 anomaly(异常)时,定量结论 SHALL 以白名单值与
确定性统计为据。不满足时 SHALL 不产出该条定量洞察(可保留非定量的数据
质量提示)。

#### Scenario: 两期不写趋势

- GIVEN 结果仅 2 个时间点
- WHEN 生成 insights
- THEN 不出现 trend 类「逐月上升」定量断言
