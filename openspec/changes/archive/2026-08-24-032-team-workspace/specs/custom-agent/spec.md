## ADDED Requirements

### Requirement: Bot 归属与团队可见性

Bot 列表端点 SHALL 按归属过滤:创建者本人的 Bot + 用户作为成员
的团队的 Bot(team-workspace spec 角色规则:VIEWER 可见可运行/
MEMBER 可编辑/OWNER 全权);非成员团队的 Bot MUST NOT 出现在
列表,直访/编辑按同规则 404/403 同形。Bot 定义 SHALL 纯增
team_id 归属列(NULL=个人,零回归);归属变更 SHALL 限团队
OWNER 或创建者本人并落审计。公开页与 api-key 渠道语义不变
(公开即公开,key 绑定即授权)。

#### Scenario: 团队 Bot 对成员可见可运行

- GIVEN Bot 归属团队 T
- WHEN T 的 VIEWER 查询列表并调用该 Bot
- THEN 列表可见,运行正常;编辑被拒(403)

#### Scenario: 非成员不可见

- GIVEN Bot 归属团队 T
- WHEN 非 T 成员查询列表或编辑
- THEN 列表不含该 Bot;编辑 403/404 同形

#### Scenario: 个人 Bot 零回归

- GIVEN Bot 无团队(team_id NULL)
- WHEN 查询/运行
- THEN 仅创建者可见可用,与团队域引入前一致
