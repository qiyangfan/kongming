## ADDED Requirements

### Requirement: 工作流归属(团队可见性)

工作流定义 SHALL 纯增 team_id 归属列(NULL=个人,零回归);
列表/详情/运行/编辑(新版本/启停/删除)SHALL 按 team-workspace
spec 角色规则过滤(VIEWER 可见可运行/MEMBER 可编辑/OWNER 全权);
非成员 fail-closed(列表过滤+直访 404 同形);归属变更限团队
OWNER 或创建者本人并落审计。

#### Scenario: 团队工作流对成员可见

- GIVEN 工作流归属团队 T
- WHEN T 的 MEMBER 查询并触发运行
- THEN 可见可运行可编辑(新版本)

#### Scenario: 非成员不可见

- GIVEN 工作流归属团队 T
- WHEN 非 T 成员查询或触发
- THEN 列表不含该项;触发 404 同形
