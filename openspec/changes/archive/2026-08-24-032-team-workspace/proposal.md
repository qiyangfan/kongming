# 032 team-workspace:团队工作区

## Why

租户内的资源共享当前只有两个极端:全员共享(知识库等)或个人重做
(Bot/工作流/记忆)。团队(项目组)层级的缺失让协作要么越权要么重复。
本变更在租户内增加团队层:团队=成员+角色(OWNER/MEMBER/VIEWER),
Bot/工作流可归属团队共享,记忆支持团队级共识双路召回(上游
team-workspace 域的 Kongming 适配;知识库归属改造面大,收 Non-goals)。

## What Changes

- **团队与成员**(api 新 `team` 域包 + 0020 迁移 sys_team/
  sys_team_member):租户内 CRUD+成员管理(角色 OWNER/MEMBER/VIEWER);
  创建者自动为 OWNER;严格租户内(404 同形);system:team:manage
  权限点(排序 25,管理面);审计复用 operlog。
- **资源归属(Bot/工作流)**:sys_bot_definition/sys_workflow_def 纯增
  team_id 列(0020;NULL=个人,零回归);归属变更端点(OWNER 或创建者
  本人,归属变更落审计);**可见性规则**:个人资源仅创建者;团队资源
  VIEWER 可见+可运行/MEMBER 可编辑(新版本/启停)/OWNER 全权
  (成员管理/删除);非成员 fail-closed 不可见(列表过滤+直访 404
  同形);运行路径(Bot 三渠道/工作流手动触发)按同规则校验。
- **团队记忆**:user_memory 纯增 team_id 列(0020);成员(OWNER/
  MEMBER)可在记忆页按团队写入共识;召回=个人+所在团队双路合并
  top-3(022 既有管线注入形态不变);隐式提取维持仅个人(团队记忆
  只经显式写入,防自动提取污染共识);非成员不召回。
- **web**:/admin/teams 第十四子页(团队列表[我所在的]+创建+成员
  管理对话框[加/删/改角色]+删除受控确认);Bots 页与工作流页
  归属选择器(个人/我所属团队下拉);MemoryPage 团队写入选择器。

## Capabilities

### New Capabilities

- `team-workspace`:团队与成员管理、资源归属与可见性、团队记忆
  的行为契约。

### Modified Capabilities

- `custom-agent`:Bot 列表/运行/编辑按归属可见性过滤。
- `workflow-orchestration`:工作流列表/运行/编辑按归属可见性过滤。
- `agent-memory`:召回双路(个人+团队),团队记忆显式写入。

## Non-goals

- **知识库(rag)团队归属**(009 租户级共享模型的深化改造,涉及
  三路召回/内省/上传全链,独立变更再做);触发器(031)归属(v1
  维持租户管理面语义);Bot 公开页/api-key 渠道语义不变(公开即
  公开,key 绑定即授权——归属过滤作用于管理面与创建者面)。
- 不做团队级 RBAC 权限点映射(团队管「资源归属可见性」,不替代
  系统权限点);不做跨租户团队;不做团队成员上限配额;不做 VIEWER
  的「仅运行不可见定义」细粒度(v1 VIEWER=可见+可运行)。
- 不做团队记忆的隐式提取与休眠自动流转(显式写入先行);不做
  归属变更历史表(operlog 已记)。

## Impact

- **代码**:api 新 team 域包 + bot/workflow 归属列与可见性过滤 +
  memory 双路召回 + web TeamsPage/归属选择器/记忆选择器。
- **Schema**:0020 迁移(sys_team/sys_team_member 两新表 +
  bot/workflow/user_memory 三处 team_id 纯增列;ADR-004 约定)。
- **契约**:/system/teams/* 端点族 + 归属变更端点 + memory 写入
  team_id 纯增;make contracts。
- **ADR 引用**:ADR-004(新迁移)/ADR-005(契约)。
- **安全**:可见性 fail-closed(列表过滤+直访同形);归属变更审计;
  团队记忆隔离;运行路径同规则;测试先行(角色边界/非成员/隔离/
  零回归)。
