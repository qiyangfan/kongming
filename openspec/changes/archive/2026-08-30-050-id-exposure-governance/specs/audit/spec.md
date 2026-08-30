# audit spec delta(050-id-exposure-governance)

## ADDED Requirements

### Requirement: 操作者身份可读性

审计列表(SQL 审计与操作日志)的操作者列 SHALL 以可读身份呈现:昵称缺失时
SHALL 回退登录用户名(username,随查询一并返回,契约纯增);操作者 id 为空
(系统行为)SHALL 呈现「系统」;id 存在但昵称与用户名皆缺 SHALL 呈现
「已注销」;SHALL NOT 渲染用户 id 或其截断片段。悬停提示 SHALL 与主文案同源。

#### Scenario: 昵称缺失回退用户名
- GIVEN 审计操作者未设置昵称且有登录名 op_x
- WHEN 审计列表渲染
- THEN 操作者列呈现 op_x,SHALL NOT 出现 id 截断片段

#### Scenario: 系统行为与已注销
- GIVEN 某审计行操作者 id 为空;另一行操作者昵称与用户名皆缺
- WHEN 列表渲染
- THEN 前者呈现「系统」,后者呈现「已注销」
