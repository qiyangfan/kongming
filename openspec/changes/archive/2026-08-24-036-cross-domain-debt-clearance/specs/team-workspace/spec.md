## MODIFIED Requirements

### Requirement: 团队与成员管理

系统 SHALL 支持租户内创建团队并管理成员(角色 OWNER/MEMBER/
VIEWER),创建者自动为 OWNER;团队 MUST 严格租户内(跨租户 404
同形);管理操作(创建/编辑/删除/成员增删改)MUST 要求
system:team:manage 权限点;删除末位 OWNER MUST 拒绝。**添加成员
对话框的候选排除(已在团队中的用户不再出现在候选)MUST 按
user_id 语义键判定**(username 可变更,按名排除会失效致重复添加)。

#### Scenario: 创建团队与加成员

- GIVEN 持 system:team:manage 的用户
- WHEN 创建团队并添加成员(指定角色)
- THEN 落库成功,创建者为 OWNER,成员按角色获得资源访问权

#### Scenario: 成员角色边界

- GIVEN 团队成员角色
- WHEN 操作团队资源
- THEN VIEWER 可见可运行不可编辑/MEMBER 可编辑不可管理成员/
  OWNER 全权;越权拒绝(403)

#### Scenario: 团队不跨租户

- GIVEN 租户 A 的团队
- WHEN 租户 B 用户访问
- THEN 404 同形(fail-closed)

#### Scenario: 末位 OWNER 保护

- GIVEN 团队仅剩一名 OWNER
- WHEN 移除该成员
- THEN 拒绝(团队至少保留一名 OWNER)

#### Scenario: 候选排除按 user_id

- GIVEN 用户 U 已是团队成员(且 U 此后变更过 username)
- WHEN 打开添加成员对话框
- THEN U 不出现在候选列表(按 user_id 排除,不受改名影响)

### Requirement: 资源归属与可见性

Bot 与工作流 SHALL 支持归属团队或个人(team_id NULL=个人)。
个人资源 MUST 仅创建者可见(零回归);团队资源 MUST 按成员角色
可见(VIEWER 可见可运行/MEMBER 可编辑/OWNER 全权),对非成员
MUST fail-closed 不可见(列表过滤+直访 404 同形);归属变更 MUST
落操作审计,且仅团队 OWNER 或资源创建者本人(目标团队须其为
OWNER)可执行;运行路径(Bot 三渠道创建者面/工作流触发)MUST
按同规则校验。归属团队的资源执行 MUST NOT 绕过既有防御层。
**资源与团队记忆的列表/详情响应 SHALL 回传 team_id**(契约纯增),
**管理/门户页面 SHALL 以徽标展示归属**(个人/团队名)。

#### Scenario: 团队资源对成员可见

- GIVEN 归属团队 T 的 Bot
- WHEN 团队 T 的 VIEWER 查询 Bot 列表并运行
- THEN 可见可运行;编辑被拒

#### Scenario: 团队资源对非成员不可见

- GIVEN 归属团队 T 的工作流
- WHEN 非 T 成员查询列表或直访
- THEN 列表不含该项;直访 404 同形

#### Scenario: 个人资源零回归

- GIVEN 归属个人的资源(team_id NULL)
- WHEN 任意查询/运行
- THEN 仅创建者可见可用,行为与团队域引入前一致

#### Scenario: 归属变更审计

- GIVEN 资源创建者(且是目标团队 OWNER)
- WHEN 将个人资源关联到团队
- THEN 归属变更为团队,成员按角色获得访问权,operlog 落审计

#### Scenario: 归属字段回传与徽标

- GIVEN 归属团队 T 的 Bot 与归属个人的记忆条目
- WHEN 查询 Bot 列表/记忆列表
- THEN 响应含 team_id(团队资源=团队 id,个人=null);页面以
  徽标展示「团队 T」/「个人」
