# agent-memory 规格增量:惰性休眠收窄到本用户本租户

## MODIFIED Requirements

### Requirement: 遗忘与置信度

系统 SHALL 将 confidence<0.3 或超过 90 天未命中(last_hit_at)的记忆标记 dormant;dormant 记忆 SHALL 不参与召回但在自管理页可见(可恢复——编辑即唤醒)。dormant 惰性标记 SHALL 强制 user_id + tenant_id 双过滤,仅作用于当前召回用户本人当前租户的记忆(与召回查询同隔离铁律,禁止跨用户跨租户批量翻牌)。v1 置信度固定(形成时定值,不做自动演化)。

#### Scenario: 休眠记忆不召回

- GIVEN 一条 90 天未命中的记忆
- WHEN 召回执行
- THEN 该记忆不在召回集内,自管理页可见其 dormant 状态

#### Scenario: 惰性休眠只作用本人本租户

- GIVEN 租户 T1 用户 A 存在应休眠记忆,租户 T2 用户 B 也存在应休眠记忆
- WHEN 用户 A(租户 T1)执行一次召回触发惰性休眠
- THEN 仅 A 在 T1 的记忆被标记 dormant;B 在 T2 的记忆不受影响
