## Purpose

让 Agent 沉淀用户长期记忆(偏好/事实/目标),运行时按问题向量召回注入提示,跨会话「越用越懂你」;user_id + tenant_id 双重 fail-closed 隔离,用户对自己的记忆有完整数据主权。

## Requirements

### Requirement: 记忆存储与向量召回

系统 SHALL 以 user_memory 表存储用户长期记忆:content(文本)、category(分类)、confidence(置信度)、source(EXPLICIT/IMPLICIT)、embedding(向量)、last_hit_at、dormant(休眠标志),user_id + tenant_id 双维归属。问答链路 SHALL 按当前问句向量召回 top-3 非 dormant 记忆,召回查询 MUST 强制 user_id + tenant_id 双过滤(fail-closed,跨用户/跨租户永不召回);召回命中 SHALL 刷新 last_hit_at。无记忆或召回为空时 SHALL 不注入,链路行为与引入前逐字一致(零回归)。

#### Scenario: 召回注入仅本人本租户

- GIVEN 用户 A 在租户 T1 有记忆「偏好看周报口径」
- WHEN A 在 T1 发起相关问答
- THEN 召回该记忆并注入提示
- AND 用户 B(同租户)或 A 在其他租户召回不到该记忆

#### Scenario: 无记忆零回归

- GIVEN 用户无任何记忆
- WHEN 发起问答
- THEN 链路行为与 022 之前完全一致(无注入段,响应同构)

### Requirement: 记忆形成(显式 + 隐式)

系统 SHALL 支持两条形成路径:**显式**——用户输入含「记住/请记住」意图词时,系统提取其后内容形成记忆(confidence=1.0,source=EXPLICIT),并经助手答复告知已记住;**隐式**——问答成功后后台任务经 LLM 提取用户偏好/事实候选落库(confidence=0.5,source=IMPLICIT)。两条路径在落库前 SHALL 经 PII/凭证检测(复用 prompt 护栏规则集):命中密码/密钥/token/身份证/手机号等形态 SHALL 拒绝落库并记操作日志。

#### Scenario: 显式记忆

- GIVEN 用户输入「请记住:我看报表只关心月度口径」
- WHEN 系统处理
- THEN 落一条 EXPLICIT 记忆(confidence=1.0),答复确认已记住

#### Scenario: 隐式记忆

- GIVEN 一轮成功问答(用户表达「我一般只看华东区数据」)
- WHEN 后台提取任务执行
- THEN 落一条 IMPLICIT 记忆(confidence=0.5),用户可在自管理页查看/确认/删除

#### Scenario: PII 拒绝落库

- GIVEN 用户输入「请记住:我的密码是 Xyz123」
- WHEN 形成前检测
- THEN 拒绝落库,答复说明敏感信息不记忆,落操作日志

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

### Requirement: 记忆自管理(数据主权)

系统 SHALL 提供登录用户管理本人记忆的端点:列表(含 dormant)/编辑(内容修改;IMPLICIT 编辑或确认即视为用户认可)/删除/导出(JSON)。操作对象 MUST 为当前登录用户本人记忆(直接传他人记忆 id SHALL 404 同形,不暴露存在性);租户维度按当前租户上下文。形成/编辑/删除 SHALL 落操作日志。

#### Scenario: 本人记忆管理

- GIVEN 用户有 3 条记忆
- WHEN 列表/编辑/删除/导出
- THEN 操作生效且落操作日志

#### Scenario: 越权操作同形拒绝

- GIVEN 用户 A 持有用户 B 的记忆 id
- WHEN A 尝试编辑/删除该记忆
- THEN 返回 404(不暴露存在性),无副作用

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
