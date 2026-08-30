# feedback spec delta(050-id-exposure-governance)

## ADDED Requirements

### Requirement: 操作者身份可读性

管理面反馈列表的操作者列 SHALL 以可读身份呈现:昵称缺失时 SHALL 回退登录
用户名(username,随列表查询一并返回,契约纯增),两者皆缺 SHALL 呈现
「已注销」;SHALL NOT 渲染用户 id 或其截断片段(如 UUID 前 8 位)。悬停
提示 SHALL 与主文案同源。

#### Scenario: 昵称缺失回退用户名
- GIVEN 反馈用户未设置昵称且有登录名 op_x
- WHEN 管理面反馈列表渲染
- THEN 操作者列呈现 op_x,SHALL NOT 出现 id 截断片段

#### Scenario: 双名称皆缺
- GIVEN 反馈用户昵称与用户名均不可用
- WHEN 列表渲染
- THEN 操作者列呈现「已注销」
