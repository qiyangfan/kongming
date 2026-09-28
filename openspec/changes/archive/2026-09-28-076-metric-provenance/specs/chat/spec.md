# chat spec delta(076-metric-provenance)

## ADDED Requirements

### Requirement: 口径依据持久化与回显

问答响应与历史回显的消息模型 SHALL 纯增本轮实际使用的指标口径命中列表(每项含指标标识、名称、口径表达式、修订时间)。同步、流式、回显三路径 SHALL 同源:落库值与当轮响应一致。老消息(无该键)回显时 SHALL 按空列表呈现,SHALL NOT 反查指标表重算——口径是链路输入而非终态输出的函数,重算等于伪造依据。

#### Scenario: 三路径同源

- GIVEN 一轮命中了指标口径的问答
- WHEN 分别经同步响应、流式 result 事件、历史回显读取
- THEN 三处口径命中列表内容一致

#### Scenario: 老消息按空呈现且不重算

- GIVEN 一条在本变更之前落库的消息(档中无该键)
- WHEN 回显
- THEN 口径命中列表为空,不回显任何条目
- AND 不依据当轮 sql/summary 反查指标表

#### Scenario: 无命中轮次零回归

- GIVEN 一轮未命中任何指标的问答
- WHEN 响应与回显
- THEN 该列表为空数组,呈现形态与引入前逐字一致
