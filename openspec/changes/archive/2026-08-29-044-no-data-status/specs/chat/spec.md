# chat spec delta(044-no-data-status)

## ADDED Requirements

### Requirement: 消息状态第四态(NO_DATA)

chat_message.status SHALL 支持第四态 `NO_DATA`:agent verdict=NO_DATA
的回答以该状态持久化——`summary` 承载中性说明文案、`error` 为空、产物
卡为空。呈现 SHALL 为中性说明形态(非错误横幅、非成功结果卡),反馈
操作 SHALL 照常可用;web 实时映射(verdict→status)与历史回显 SHALL
与该语义一致。既有三态(SUCCESS/FAILED/REJECTED)的映射与呈现 SHALL
逐字不变。

#### Scenario: NO_DATA 消息持久化与中性呈现

- GIVEN agent 返回 verdict=NO_DATA、error=「当前数据源未找到与问题相关的数据表…」
- WHEN chat 终态组装与落库
- THEN 消息 status=NO_DATA、summary=说明文案、result_json.error 为空、产物卡为空
- AND 响应与历史回显以中性说明形态渲染(非错误横幅),反馈区可用

#### Scenario: 既有三态零回归

- GIVEN PASS/BLOCKED/ERROR 三类既有终态
- WHEN 终态组装与落库
- THEN 映射与呈现行为与引入前逐字一致
