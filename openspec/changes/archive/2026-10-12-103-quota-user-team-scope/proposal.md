# Proposal

## Why

配额体系当前 scope 仅 TENANT/BOT 两级:租户总闸与单 Bot 闸已备,但「个人
额度」(USER)与「团队共享资源包」(TEAM)两级缺失——团队名下多个 Bot
共享一个额度池、给高用量用户单独立额,是 SaaS 配额的标配语义,也是商业化
治理面(三维配额 fail-closed)完整性的最后两级。产品待办已登记
「用户/TEAM 维度配额」。

## What Changes

- 配额策略 scope 枚举扩展 TENANT/BOT → TENANT/BOT/USER/TEAM:
  - **USER 级**:`scope_ref` 绑定当前租户内用户,限定该用户当月 TOKENS/COST
    (以既有用量表的用户归属聚合);
  - **TEAM 级**:`scope_ref` 绑定当前租户内团队,限定归属该团队资源的当月
    TOKENS/COST(用量按资源归属解析——团队名下 Bot 的调用用量;资源换队
    跟随现归属,配额是当月治理窗口语义)。
- 网关判定从双路扩展为**多路叠加**:租户级 + 命中的 BOT/USER/TEAM 级各自
  独立判定,任一超限即拒;拒绝维度文案带 scope 前缀(如 `USER:TOKENS`、
  `TEAM:COST`)归因审计。
- TEAM 级用量聚合采用联表解析(用量行经资源归属解析 team),**零用量表
  schema 变更**;策略表仅 scope CHECK 枚举扩展(新迁移)。
- CALLS 维度维持仅 TENANT 级(资源/用户/团队级不立约,工具审计归因口径
  与既有 BOT 级同因)。
- `scope_ref` 校验同既有 BOT 形态:跨租户/不存在拒绝;引用目标被删除后
  策略保留但零命中,不级联。
- 计费配置面:scope 下拉与 scope_ref 选择器扩展(USER/TEAM,单授
  billing:manage 可配——复用既有租户清单端点范式,用户/团队选择器
  数据源同口径收敛)。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `developer-portal-billing`:「Bot 维度配额」requirement 重命名为
  「多维度配额」并扩展(USER/TEAM 级语义、多路叠加判定、聚合口径)。

## Impact

- `apps/api`:billing 域(策略 CRUD scope 扩展、网关判定多路化、TEAM 聚合
  联表解析);新迁移(scope CHECK 扩展,零数据迁移)。
- `apps/web`:计费配置页表单扩展;类型经管线生成。
- `apps/agent`:零改动。
- chat 面既有「用户问答速率/配额」(062)不动——两切面各司其职(见
  Non-goals);schema:仅策略表 CHECK 变更,无新表新列。
