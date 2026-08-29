# nl2sql-agent spec delta(044-no-data-status)

## ADDED Requirements

### Requirement: QueryOut 响应终态判别

`/query` 响应的 `verdict` SHALL 为四态:`PASS`(执行成功)/ `BLOCKED`
(权限/策略拦截)/ `ERROR`(执行失败)/ `NO_DATA`(知识未覆盖如实说明,
生成短路终态)。`NO_DATA` 态 `success=false`,SHALL NOT 记为执行失败
语义;`error` SHALL 携带用户可读说明文案;响应 SHALL 不含 SQL 与结果集
(短路发生于执行之前)。

#### Scenario: 短路终态对外判别为 NO_DATA

- GIVEN generate 输出哨兵短路(end_reason=no_data)
- WHEN agent 组装 /query 响应
- THEN verdict=NO_DATA、success=false、error 为友好说明文案
- AND 响应不含 SQL/结果集(短路前零执行)

#### Scenario: 既有三态零回归

- GIVEN PASS/BLOCKED/ERROR 三类既有终态
- WHEN agent 组装 /query 响应
- THEN 判别与字段形态与引入前逐字一致
