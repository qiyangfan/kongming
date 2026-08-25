## Purpose

在租户内增加团队层级:团队=成员+角色(OWNER/MEMBER/VIEWER);
Bot/工作流可归属团队(成员按角色共享)或个人(仅创建者);记忆
支持团队级共识双路召回。团队管「资源归属可见性」,不替代 RBAC
系统权限点。

## Requirements

### Requirement: 团队与成员管理

系统 SHALL 支持租户内创建团队并管理成员(角色 OWNER/MEMBER/
VIEWER),创建者自动为 OWNER;团队 MUST 严格租户内(跨租户 404
同形);管理操作(创建/编辑/删除/成员增删改)MUST 要求
system:team:manage 权限点;删除末位 OWNER MUST 拒绝。

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

### Requirement: 资源归属与可见性

Bot 与工作流 SHALL 支持归属团队或个人(team_id NULL=个人)。
个人资源 MUST 仅创建者可见(零回归);团队资源 MUST 按成员角色
可见(VIEWER 可见可运行/MEMBER 可编辑/OWNER 全权),对非成员
MUST fail-closed 不可见(列表过滤+直访 404 同形);归属变更 MUST
落操作审计,且仅团队 OWNER 或资源创建者本人(目标团队须其为
OWNER)可执行;运行路径(Bot 三渠道创建者面/工作流触发)MUST
按同规则校验。归属团队的资源执行 MUST NOT 绕过既有防御层。

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

### Requirement: 团队记忆

系统 SHALL 支持团队级记忆(user_memory.team_id):仅 OWNER/MEMBER
成员可写入(显式路径,403 同形);召回 SHALL 为个人 top-3 ∪ 用户
所在团队的团队记忆 top-3 合并注入(注入形态与个人记忆一致);
团队记忆 MUST 按 team_id 隔离(非成员不召回);隐式提取与休眠
流转 MUST 维持仅个人语义(team_id NULL)。

#### Scenario: 团队记忆共享召回

- GIVEN 团队 T 有一条记忆「GMV 口径不含退款」
- WHEN 团队 T 成员的问答召回
- THEN 该团队记忆与个人记忆一并注入

#### Scenario: 团队记忆隔离

- GIVEN 团队 T 的记忆
- WHEN 非 T 成员召回
- THEN 不注入该团队记忆

#### Scenario: 非 MEMBER 写入拒绝

- GIVEN 团队 T 的 VIEWER(或非成员)
- WHEN 向团队 T 写入记忆
- THEN 403 拒绝

### Requirement: 团队管理端点

系统 SHALL 提供团队管理端点:管理 CRUD(挂 system:team:manage)+
「我所在的团队」列表(登录即可,成员关系过滤)+成员管理+资源
归属变更端点;全部按 tenant_id 隔离。

#### Scenario: 我所在的团队列表

- GIVEN 普通用户(无管理权限点)
- WHEN 查询团队列表
- THEN 返回其作为成员的团队(不含其他团队)

#### Scenario: 非成员无权操作团队资源

- GIVEN 非团队 T 成员
- WHEN 尝试编辑团队 T 的资源
- THEN 拒绝(fail-closed,成员关系校验)
