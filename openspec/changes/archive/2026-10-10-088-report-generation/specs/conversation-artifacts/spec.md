## MODIFIED Requirements

### Requirement: artifact 类型化抽象

assistant 消息的 result_json SHALL 纯增 artifacts 数组:每项含 type(∈ sql/chart/document/report)、content(主内容)、metadata(辅助信息,如图表配置/SQL 数据源)。报告生成轮次的 assistant 消息 SHALL 派生 report 卡:content 为报告标题与终态摘要,metadata 携带报告标识、版本与状态(ready/partial/failed);部分完成(partial)与失败(failed)的摘要 SHALL 如实反映终态,SHALL NOT 表述为全部完成。控制面 SHALL 从既有要素确定性派生:sql 非空→sql 卡;chart.type≠none→chart 卡(含完整 chart 配置);summary 非空→document 卡;报告轮次→report 卡(互斥于前三者的问答轮次,不叠加派生)。无产物时 SHALL 输出空数组(向后兼容,老前端不受影响);派生逻辑对同步/流式/历史回显 SHALL 同源(读档重算兜底与既有形态一致)。

#### Scenario: 全要素派生

- **GIVEN** 一轮成功问答(SQL+图表+摘要)
- **WHEN** 响应组装
- **THEN** artifacts 含 sql/chart/document 三卡,内容逐字段对应既有要素

#### Scenario: 报告轮次派生报告卡

- **GIVEN** 一轮报告生成以 partial 终态结束(三章成两章缺)
- **WHEN** 响应组装
- **THEN** artifacts 含 report 卡,摘要如实表述部分完成,metadata 携带报告标识与版本

#### Scenario: 无产物零回归

- **GIVEN** REJECTED/FAILED 轮(无 SQL 无结果)
- **WHEN** 组装
- **THEN** artifacts 为空数组,既有分态文案不变

#### Scenario: 老前端兼容

- **GIVEN** 未升级的前端客户端收到 report 卡
- **WHEN** 渲染该消息
- **THEN** 忽略未知类型不崩溃,不影响其余卡片与终态处理
