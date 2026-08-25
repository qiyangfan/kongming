# 032 team-workspace 技术设计

## Context

资源可见性现二元:租户共享或个人。团队层补中间态。约束:可见性
fail-closed(过滤+同形);个人资源零回归;不替代 RBAC 权限点。

## Decisions

### D1: 数据模型(0020)

sys_team:id uuid7 / tenant_id / team_key / name / created_by /
created_at;唯一(tenant_id, team_key)。
sys_team_member:team_id / user_id / role VARCHAR CHECK(OWNER/
MEMBER/VIEWER)/ created_at;主键(team_id, user_id)。
ALTER:sys_bot_definition/sys_workflow_def/user_memory 各 ADD
team_id UUID NULL(个人语义,零回归)+ 普通索引。

### D2: 可见性判定(team.models,纯函数)

`visible(role_in_team|None, creator_id, user_id) -> bool`:
creator 恒可见;team_id NULL 仅 creator;团队资源按成员角色:
VIEWER/MEMBER/OWNER 均可见。操作级:
- 运行(bot 三渠道创建者面/workflow run):可见即可运行;
- 编辑(bot update/工作流新版本/启停):MEMBER+;
- 删除/归属变更/成员管理:OWNER(创建者=资源无团队时的唯一
  全权;归属变更另放行「创建者本人→归属到自己是 OWNER 的团队」)。

### D3: 查询面过滤

列表:creator=me OR team_id ∈ (我所属团队 id 集)——仓储 SQL
双支(个人列 creator_id=me + team_id IN 成员集);直访按 D2 判定
404 同形。成员集查询:单 SQL 取 user 的 team→role 映射(每请求
一次,无 N+1)。

### D4: 团队记忆

写入:POST /memory {content, team_id?}(既有端点纯增;team_id 非空
须 OWNER/MEMBER 成员校验 403);召回(022 recall_hint):个人
top-3 ∪ 团队 top-3 合并去重截断(注入形态不变);team_id 隔离
(仅成员团队);隐式提取/dormant 维持个人语义(team_id NULL)。

### D5: 端点

- POST/GET /system/teams;GET /{id}(成员列表);PUT /{id};
  DELETE /{id}(连带成员;资源 team_id 置 NULL 不删资源);
- POST /{id}/members {user_id, role};PUT /{id}/members/{uid};
  DELETE /{id}/members/{uid}(OWNER 至少留一);
- POST /{id}/attach {resource_type: bot|workflow, resource_id}
  (归属变更;OWNER 或资源创建者本人);
- 列表语义:非管理面用户 GET /system/teams 返回「我所在的团队」
  (成员关系过滤,登录即可);管理面 CRUD 挂 system:team:manage。

### D6: 测试策略

- 角色边界矩阵(VIEWER 运行可编辑拒/MEMBER 编辑可删成员拒/
  OWNER 全权);非成员列表过滤+直访 404;个人零回归;
- 归属变更(创建者本人/非创建者非 OWNER 拒/审计);
- 团队记忆:写入非 MEMBER 拒/召回双路/非成员不召回;
- 成员删除(末位 OWNER 拒)。

## Risks / Trade-offs

- [可见性过滤遗漏面] → 过滤收在仓储单点(列表)+ service 判定
  单点(直访/运行),评审按面核对。
- [Bot 公开页/api-key 绕过归属] → 既定语义(公开即公开/key 即
  授权,proposal 已声明);归属过滤只作用管理面与创建者面。
- [团队删除的资源悬置] → team_id 置 NULL 回个人(创建者可见,
  不丢资源)。

## Migration Plan

0020 两新表+三处纯增列;存量 team_id 全 NULL=个人零回归;
权限点 25 只增;回滚=删端点+列(列保留无害)。

## Open Questions

- O1:知识库团队归属(独立变更);O2:触发器归属;O3:团队级
  用量配额;O4:VIEWER「仅运行」细粒度;O5:团队记忆隐式提取
  与编辑/休眠流转。
