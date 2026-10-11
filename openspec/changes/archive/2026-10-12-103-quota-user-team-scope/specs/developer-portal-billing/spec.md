# Spec Delta

## RENAMED Requirements

- FROM: `### Requirement: Bot 维度配额`
- TO: `### Requirement: 多维度配额(scope:TENANT/BOT/USER/TEAM)`

## MODIFIED Requirements

### Requirement: 多维度配额(scope:TENANT/BOT/USER/TEAM)

配额策略 SHALL 支持 scope(TENANT[默认]/BOT/USER/TEAM):BOT 级策略以
scope_ref 绑定**该租户内**一个 Bot,USER 级绑定该租户内一个用户,TEAM 级
绑定该租户内一个团队(跨租户或不存在的 scope_ref MUST 拒绝)。BOT 级限定
该 Bot 的当月 TOKENS/COST 用量(以用量表的 bot 归属为准);USER 级限定该
用户当月 TOKENS/COST(以用量表的用户归属聚合);TEAM 级限定归属该团队的
资源的当月 TOKENS/COST(用量行经资源归属解析 team——资源变更归属后跟随
现归属,配额为当月治理窗口语义)。同一(租户,维度,周期,scope,scope_ref)
组合 MUST 唯一(零 uuid 归一的表达式唯一约束,TENANT 行不因 NULL
scope_ref 重复)。网关判定 MUST 多路叠加:租户级与命中的 BOT 级(api-key
绑定 Bot)/USER 级(调用归属用户)/TEAM 级(归属团队命中策略)各自独立
判定,任一超限即拒,拒绝维度文案 MUST 带 scope 前缀(如 BOT:TOKENS、
USER:TOKENS、TEAM:COST)以归因审计。BOT/USER/TEAM 级均无套餐兜底
(未配置=不限);CALLS 维度仅支持 TENANT 级(工具审计归因口径,资源/
用户/团队级不立约)。引用目标(Bot/用户/团队)被删除后其策略保留但零命中,
不立级联。

#### Scenario: Bot 级超限拒绝

- GIVEN Bot A 配置 BOT 级 TOKENS 上限且当月已达
- WHEN 以 Bot A 的 api-key 调用网关
- THEN 429 QUOTA_EXCEEDED: BOT:TOKENS + 审计,不执行

#### Scenario: Bot 级与租户级独立

- GIVEN Bot A BOT 级未超,但租户级 CALLS 已超限
- WHEN 调用
- THEN 按租户级 429 拒绝

#### Scenario: 跨租户 scope_ref 拒绝

- GIVEN 超管为租户 A 配置 BOT 级策略
- WHEN scope_ref 指向租户 B 的 Bot
- THEN 404 拒绝,不落策略

#### Scenario: 同 scope 组合唯一

- GIVEN 租户 A 已有 BOT 级(bot X)TOKENS 策略
- WHEN 再次创建同组合策略
- THEN upsert 语义更新限额,不产生重复行

#### Scenario: USER 级超限拒绝

- GIVEN 用户 U 配置 USER 级 TOKENS 上限且当月已达
- WHEN 以 U 归属的 api-key 调用网关
- THEN 429 QUOTA_EXCEEDED: USER:TOKENS + 审计,不执行

#### Scenario: TEAM 级按资源归属聚合

- GIVEN 团队 T 配置 TEAM 级 TOKENS 上限,T 名下 Bot X 与 Bot Y 各自消耗部分额度
- WHEN X+Y 合计当月用量达上限
- THEN T 名下任一 Bot 的下一次网关调用被 429 QUOTA_EXCEEDED: TEAM:TOKENS 拒绝

#### Scenario: 资源换队跟随现归属

- GIVEN Bot X 当月已消耗用量 N,月中从团队 T 移至团队 R(或改为个人)
- WHEN 当月 TEAM 级判定
- THEN 用量 N 计入团队 R(现归属),不再计入团队 T

#### Scenario: 多路叠加任一超限即拒

- GIVEN 租户级未超、USER 级已超
- WHEN 该用户调用网关
- THEN 按 USER 级 429 拒绝(租户级未超不构成放行)

#### Scenario: 引用目标删除后零命中

- GIVEN USER 级策略引用的用户被删除
- WHEN 网关判定
- THEN 该策略不命中(零限流效果),策略行保留,不级联清理
