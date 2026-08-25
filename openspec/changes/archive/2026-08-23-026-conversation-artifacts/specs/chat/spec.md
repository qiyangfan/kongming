## ADDED Requirements

### Requirement: 消息 artifacts 字段

问答响应与历史回显的消息模型 SHALL 纯增 artifacts 数组(conversation-artifacts spec 的派生语义);同步/流式/回显三路径同源派生,字段形态逐字一致;老消息(无 artifacts 档)回显时 SHALL 按要素确定性重算兜底(与 chart 重算同款先例),显式空档(如 agent 不可达 FAILED 轮)按档原样回显。

#### Scenario: 三路径同源

- GIVEN 同一轮问答经同步响应/流式 result/历史回显
- WHEN 读取
- THEN 三处 artifacts 内容一致

#### Scenario: 老消息缺档重算

- GIVEN 026 之前落库的消息(result_json 无 artifacts 键)
- WHEN 回显
- THEN 按 sql/chart/summary 要素确定性重算 artifacts,与新消息同形态渲染

#### Scenario: 显式空档不重算

- GIVEN agent 不可达的 FAILED 消息(result_json 显式 artifacts=[])
- WHEN 回显
- THEN artifacts 为空数组,不派生 document 卡(与当轮同步响应一致)
