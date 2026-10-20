## MODIFIED Requirements

### Requirement: 记忆存储与向量召回

系统 SHALL 以 user_memory 表存储用户长期记忆:content(文本)、category(分类)、confidence(置信度)、source(EXPLICIT/IMPLICIT)、2560 维 embedding、last_hit_at、dormant(休眠标志),user_id + tenant_id 双维归属。问答链路 SHALL 按当前 2560 维问句向量召回 top-3 非 dormant 记忆,召回查询 MUST 强制 user_id + tenant_id 双过滤(fail-closed,跨用户/跨租户永不召回);召回命中 SHALL 刷新 last_hit_at。无记忆或召回为空时 SHALL 不注入,链路行为与引入前逐字一致(零回归)。

#### Scenario: 记忆与查询使用相同向量维度

- GIVEN 用户记忆已生成 2560 维 embedding
- WHEN 问答链路使用问题向量召回
- THEN 查询向量同为 2560 维且维持 user_id + tenant_id 双过滤

#### Scenario: 召回注入仅本人本租户

- GIVEN 用户 A 在租户 T1 有记忆「偏好看周报口径」
- WHEN A 在 T1 发起相关问答
- THEN 召回该记忆并注入提示
- AND 用户 B(同租户)或 A 在其他租户召回不到该记忆

#### Scenario: 无记忆零回归

- GIVEN 用户无任何记忆
- WHEN 发起问答
- THEN 链路行为与 022 之前完全一致(无注入段,响应同构)
