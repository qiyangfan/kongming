## ADDED Requirements

### Requirement: 团队记忆双路召回

记忆召回 SHALL 合并两路:用户个人记忆 top-3 ∪ 用户所在团队的
团队记忆 top-3(user_memory.team_id 非空者,限成员团队),合并
注入形态与个人记忆一致;团队记忆仅可经显式写入(OWNER/MEMBER
成员,403 同形),隐式提取与休眠流转维持仅个人语义(team_id
NULL);非成员的团队记忆 MUST NOT 被召回。

#### Scenario: 双路注入

- GIVEN 用户有个人记忆,且其团队 T 有团队记忆
- WHEN 问答召回
- THEN 两路记忆一并注入

#### Scenario: 非成员不召回

- GIVEN 团队 T 的记忆
- WHEN 非 T 成员问答
- THEN 不注入
